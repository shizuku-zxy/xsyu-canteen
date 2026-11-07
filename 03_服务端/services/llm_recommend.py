
"""大模型直连推荐（2026-10-12 新增，主推荐引擎）。

架构（数据层接大模型）：
    本地数据层只做两件规则能做对的事：
      ① 硬条件粗筛——预算/食堂/楼层/排除标签/忌口菜名/过敏原（正则抽取这些是准的）
      ② 候选池采样——过滤后超过 60 道时按档口多样性采样（日期种子轮换）
    然后把「候选池 + 用户原句」整体交给大模型按语义挑菜。
    大模型只能返回池内的 dish_id（防幻觉），本地再校验一遍 + 过敏复查。

    规则 NLU + 规则推荐引擎降级为「无 LLM_API_KEY 时」的兜底。
    任何一步失败（网络/超时/解析/全幻觉）都返回 None，上层回落规则引擎，
    服务永不因 LLM 挂掉而不可用。

配置：复用 llm_fallback 的 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL 环境变量。
"""
import datetime
import hashlib
import json
import re
import urllib.request

from dao import db
from services import nlu
from services import slots as S
from services.recommend import _slots_text
from services.llm_fallback import API_KEY, BASE_URL, MODEL, TIMEOUT

MAX_POOL = 60        
PICK_CAP = 8         
MIN_POOL = 8         

def _loose_json(text):
    """从 LLM 回复里抠 JSON dict（容忍 ```json 包裹与前后废话）。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        o = json.loads(m.group(0))
        return o if isinstance(o, dict) else None
    except Exception:
        return None

PROMPT = """你是「西石大·今天吃啥」食堂推荐引擎。学生发来一句话，你从候选菜品池里挑出最合适的菜。

【候选池】（格式：dish_id|菜名|价格元|楼层|档口|食堂|标签）
%s

【学生的话】%s

【规则】
1. 只能从候选池的 dish_id 里挑，最多挑 %d 道，按推荐顺序排列；
2. 严格按学生话里的语义挑：忌口/口味/品类/预算/清淡与否全在这句话里，例如「不辣的」就绝不能挑带辣的菜；
3. 兼顾多样性：尽量覆盖不同档口，别只挑一家；
4. 输出严格 JSON（不要任何其他文字）：
   {"pick":["D0234","D0101"],"note":"一句15字内的推荐理由"}
"""

def available():
    return bool(API_KEY)

def _dish_line(d):
    return "%s|%s|%s|%s|%s|%s|%s" % (d["dish_id"], d["name"],
                                    d["price"] if d["price"] is not None else "?",
                                    d["floor"], d["shop"], d["canteen"], d["tags_str"])

def _hard_filter(rows, s):
    """本地粗筛：只做规则抽取得准的硬条件（LLM 模式不做放宽，池空就回落规则引擎）。"""
    out = []
    excl = s.get("exclude") or []
    avoid_allergen = s.get("avoid_allergen") or []
    avoid_raw = s.get("avoid_raw") or []
    budget = s.get("budget")
    for d in rows:
        if s.get("canteen_q") and d["canteen"] != s["canteen_q"]:
            continue
        if s.get("floor_q") and d["floor"] != s["floor_q"]:
            continue
        if budget is not None and d["price"] is not None and d["price"] > budget:
            continue
        if s.get("shop_q") and d["shop"] != s["shop_q"]:
            continue
        tags = (d["tags_str"] or "").split(",")
        if any(e in tags for e in excl):
            continue

        hit = (d.get("allergen_inferred") or "").split(",")
        name = d["name"]
        if any(a and (a in hit or a in name) for a in avoid_allergen):
            continue
        if any(a and a in name for a in avoid_raw):         
            continue
        out.append(d)
    return out

def _sample(pool, s):
    """池超限时采样：正向标签命中优先占坑 + 档口多样性轮换（日期种子，同日确定）。"""
    if len(pool) <= MAX_POOL:
        return pool
    try:
        seed = int(hashlib.md5(datetime.date.today().isoformat().encode("utf-8")).hexdigest()[:8], 16)
    except Exception:
        seed = 0
    pos = s.get("tags") or []
    tagged = [d for d in pool if any(t in (d["tags_str"] or "").split(",") for t in pos)][:PICK_CAP * 5]
    picked, seen_ids = [], {d["dish_id"] for d in tagged}

    shops = sorted(set(d["shop"] for d in pool))
    start = seed % len(shops) if shops else 0
    by_shop = {}
    for d in sorted(pool, key=lambda x: (x["price"] is None, x["price"] if x["price"] is not None else 999)):
        by_shop.setdefault(d["shop"], []).append(d)
    for sh in shops[start:] + shops[:start]:
        if by_shop.get(sh) and len(picked) < MAX_POOL - min(len(tagged), MAX_POOL // 2):
            picked.append(by_shop[sh][0])
    merged = tagged[:MAX_POOL // 2] + [d for d in picked if d["dish_id"] not in seen_ids]
    return merged[:MAX_POOL]

def _ask_llm(q, pool):
    """调大模型挑菜。返回 dict(pick, note) 或 None。"""
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user",
                      "content": PROMPT % ("\n".join(_dish_line(d) for d in pool), q, PICK_CAP)}],
        "temperature": 0, "max_tokens": 300,
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        o = _loose_json(data["choices"][0]["message"]["content"].strip())
    except Exception:
        return None
    if not o:
        return None
    pick = o.get("pick") or o.get("picks") or []
    note = str(o.get("note") or "").strip()[:30]
    return {"pick": [str(x).strip() for x in pick], "note": note}

def recommend(q):
    """大模型直连推荐主入口。返回与规则引擎同契约的 payload dict；失败返回 None（回落规则）。"""
    if not available() or not q:
        return None
    raw = nlu.parse_question(q)
    s = S.normalize_slots(raw)
    rows = db.recommendable_rows(campus=s.get("campus_q") or None)
    pool_all = _hard_filter(rows, s)
    if len(pool_all) < MIN_POOL:               
        return None
    pool = _sample(pool_all, s)
    reply = _ask_llm(q, pool)
    if reply is None:
        return None
    by_id = {d["dish_id"]: d for d in pool}
    picks, seen = [], set()
    for pid in reply["pick"]:                  
        d = by_id.get(pid)
        if d and pid not in seen:
            seen.add(pid)
            picks.append(d)
    if not picks:
        return None

    avoid_allergen = s.get("avoid_allergen") or []
    picks = [d for d in picks if not any(a and a in d["name"] for a in avoid_allergen)]
    if not picks:
        return None

    lines = ["%s · %s · %s ¥%s" % (d["floor"], d["shop"], d["name"],
                                   d["price"] if d["price"] is not None else "—")
             for d in picks]
    n_alg = _count_allergen(rows, s)
    alg_text = ("已排除 %d 道可能含%s的菜（按菜名推测，平台未登记过敏原）"
                % (n_alg, "、".join(s["avoid_allergen"]))) if n_alg and s.get("avoid_allergen") else ""
    return {
        "has_result": True,
        "display_text": "\n".join(lines),
        "dishes_json": json.dumps([{k: d[k] for k in ("name", "price", "spec", "canteen", "floor", "shop", "category")}
                                    for d in picks], ensure_ascii=False),
        "total_count": len(pool_all), "shop_count": len({d["shop"] for d in picks}),
        "relax_note": "", "kw_note": "",
        "allergy_note": bool(s.get("allergy_note")), "top_dish": picks[0]["name"],
        "slots_text": _slots_text(s), "stage": 0, "reason": "",
        "allergen_excluded": n_alg, "allergen_note_text": alg_text,
        "avoid_note": "", "budget_hint": "",
        "llm_note": reply["note"],             
        "engine": "llm",
        "slots": {k: s.get(k) for k in ("canteen_q", "floor_q", "budget", "tags",
                                        "exclude", "avoid_allergen", "avoid_raw")},
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

def _count_allergen(rows, s):
    """按 allergen_inferred 字段统计被排除数（与规则版口径一致）。"""
    avoid_allergen = s.get("avoid_allergen") or []
    n = 0
    for d in rows:
        hit = (d.get("allergen_inferred") or "").split(",")
        if any(a and (a in hit or a in d["name"]) for a in avoid_allergen):
            n += 1
    return n

_CACHE = {}

def recommend_cached(q):
    """同问缓存（I1 同问同答 + 演示重复问不重复花钱）。失败不缓存。"""
    if q in _CACHE:
        return _CACHE[q]
    r = recommend(q)
    if r is not None:
        _CACHE[q] = r
    return r
