
"""LLM 开场白（"先说一句话"，然后接推荐卡片）。

定位（与项目一贯口径一致）
--------------------------------------------------------------------
LLM **只做表达，不做决策**：推荐清单、数量、价格、档口全部由本地引擎
（`services/recommend.py`）算好；本模块拿"已经算好的事实"让模型用自然口吻
说 1–2 句开场，然后前端把推荐卡片接在下面。

防幻觉是**两层**：
  1. 生成侧：prompt 里只给事实清单，明令"不得新增任何菜名/价格/档口/数字"；
  2. 校验侧：拿到文本后**逐项核对**，任一条不过就整条丢弃，退回规则话术
     （见 `verify()`）：
       · 出现的任何数字，必须在事实清单的数字集合里；
       · 引号（「」""''《》）里的字符串，必须是清单里的菜名/档口；
       · 不得提到库里存在、但**本次没推荐**的菜名或档口（长度≥4 的窗口匹配）。
    也就是说——**说错话就闭嘴**，绝不会把编造的菜端给用户。

其它保证：
  · 同问 + 同结果 → 同开场白（走 llm.py 的磁盘缓存，键含事实指纹）；
  · 无 key / 超时 / 报错 / 校验不过 / 文本过长 → 返回 ''，上层退回规则话术，
    演示不会因为网络问题挂掉（**断网可用**是本项目硬指标）。
"""
import hashlib
import os
import re
import sys

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

MAX_LEN = 90                     
MAX_ITEMS_IN_FACTS = 6           

from services import persona as PA          

SYSTEM = PA.intro_system()

def _price_text(v):
    try:
        f = float(v)
        return ('%g元' % f)
    except (TypeError, ValueError):
        return ''

def _facts(out, items):
    """把引擎结果压成一张**封闭**的事实清单（模型只能引用这些）。"""
    lines = []
    mode = out.get("mode") or "dish"
    if not out.get("has_result"):
        lines.append("结果：没有符合条件的菜")
        if out.get("reason"):
            lines.append("原因：%s" % out["reason"])
    else:
        unit = "个窗口" if mode == "window" else "道菜"
        lines.append("结果：共 %s %s，覆盖 %s 个档口"
                     % (out.get("total_count"), unit, out.get("shop_count")))
        for it in (items or [])[:MAX_ITEMS_IN_FACTS]:

            price = _price_text(it.get("price"))
            spec = (it.get("spec") or "").strip()

            loc = (it.get("loc") or "").strip() or (it.get("canteen") or "")
            _meta = "，".join(x for x in (spec, price) if x)
            line = "菜品：%s（%s%s位置：%s · %s）" % (
                it.get("name"), (_meta + "，") if _meta else "", " " if _meta else "",
                loc, it.get("shop") or "")
            lines.append(line)
    for key, label in (("relax_note", "已放宽"), ("kw_note", "关键词说明"),
                       ("avoid_note", "忌口提示"), ("budget_hint", "预算提示"),
                       ("loc_note", "位置说明"), ("breakfast_note", "时段说明"),
                       ("allergen_note_text", "过敏说明")):
        if out.get(key):
            lines.append("%s：%s" % (label, out[key]))
    return "\n".join(lines)

def _allowed_tokens(items):
    toks = set()
    for it in (items or []):
        for k in ("name", "shop", "canteen", "loc", "spec"):
            v = (it.get(k) or "").strip()
            if v:
                toks.add(v)
    return toks

_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
_QUOTE_RE = re.compile(r"[「『“\"'《]([^」』”\"'》]{1,30})[」』”\"'》]")
_CJK_RUN = re.compile(r"[\u4e00-\u9fa5]{3,}")   

_CAP_CLAIM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?:以内|以下|之内|以下|不超过)")

_COND_WORDS = ('肉', '荤', '素', '辣', '清淡', '甜', '酸', '咸', '汤', '面', '饭', '粉', '粥',
               '早餐', '午餐', '晚餐', '夜宵', '便宜', '贵', '划算', '减脂', '低卡', '高蛋白',
               '牛肉', '猪肉', '鸡肉', '羊肉', '鸭肉', '鱼', '虾', '蛋', '奶', '甜点', '小吃', '快餐')
_DB_NAMES = None

def _db_names():
    """库里全部菜名+档口名（懒加载一次，只用于"这句话里的菜是不是本次推荐过的"核对）。"""
    global _DB_NAMES
    if _DB_NAMES is None:
        names = set()
        try:
            from dao import db
            for r in db.query("SELECT name FROM dish"):
                if r.get("name"):
                    names.add(r["name"])
            for r in db.query("SELECT shop FROM shop"):
                if r.get("shop"):
                    names.add(r["shop"])
        except Exception:
            pass
        _DB_NAMES = names
    return _DB_NAMES

def verify(text, out, items, question=''):
    """事实校验：返回 (是否通过, 原因)。任一条不过就丢弃整段开场白。"""
    if not text:
        return False, "空文本"
    t = text.strip().strip('"“”')
    if not t:
        return False, "空文本"
    if "\n" in t or len(t) > MAX_LEN:
        return False, "格式不合规（换行或超过 %d 字）" % MAX_LEN

    if re.search(r'价格待核|价格待审核|待核|待审核', t):
        return False, "不应出现价格待核/待审核表述"

    if re.search(r'价格档|元档|档位|价格区间|元区间', t):
        return False, "出现价格档/区间表述，应改为实际价格"

    if re.search(r'\d+(?:\.\d+)?\s*(?:到|至|~|～|—|-)\s*\d+(?:\.\d+)?\s*元', t):
        return False, "出现价格区间概括，应逐道说实际价格"

    allowed = _allowed_tokens(items)

    blob = " ".join(allowed) + " " + (question or "")

    ok_nums = set()
    for v in (out.get("total_count"), out.get("shop_count")):
        if v is not None:
            ok_nums.add(str(v))
    for it in (items or []):
        for v in (it.get("price"), it.get("spec")):
            if v is not None and v != '':
                ok_nums |= set(_NUM_RE.findall(str(v)))
    ok_nums |= set(_NUM_RE.findall(_facts(out, items)))
    ok_nums |= set(_NUM_RE.findall(_facts(out, items)))
    ok_nums |= set(_NUM_RE.findall(question or ""))
    ok_blob = "|".join(sorted(ok_nums))
    for n in _NUM_RE.findall(t):
        if n not in ok_blob:
            return False, "出现事实外的数字 %s" % n

    _caps = []
    for it in (items or []):
        try:
            _caps.append(float(it.get("price")))
        except (TypeError, ValueError):
            pass
    if _caps:
        _hi = max(_caps)
        for m in _CAP_CLAIM_RE.findall(t):
            try:
                cap = float(m)
            except ValueError:
                continue
            if _hi > cap:
                return False, "上限断言不成立（说有 %s 以内，实际最高 %g 元）" % (m, _hi)

    for q in _QUOTE_RE.findall(t):
        if q.strip() and not any(q.strip() in a or a in q.strip() for a in allowed):
            return False, "引号里出现未推荐的名称 %r" % q

    dbn = _db_names()
    if dbn:
        for run in _CJK_RUN.findall(t):
            for size in (3, 4, 5, 6, 8):
                for i in range(0, len(run) - size + 1):
                    w = run[i:i + size]
                    if w in blob:
                        continue
                    if any(w in n for n in dbn):
                        return False, "提到未推荐的名称 %r" % w

    cond_blob = (blob + ' ' + _facts(out, items) + ' '
                 + ' '.join((out.get('normalized') or {}).get('tags') or []))
    for w in _COND_WORDS:
        if w in t and w not in cond_blob:
            return False, "凭空添加了用户没提的条件 %r" % w

    missing = []
    for it in (items or []):
        loc = (it.get("loc") or "").strip()
        shop = (it.get("shop") or "").strip()
        if loc and shop and shop in t and loc not in t:
            missing.append("%s@%s" % (shop, loc))
    dish_locs = {}
    for it in (items or []):
        nm = (it.get("name") or "").strip()
        loc = (it.get("loc") or "").strip()
        if nm and loc:
            dish_locs.setdefault(nm, set()).add(loc)
    for nm, locs in dish_locs.items():
        if not nm or nm not in t:
            continue

        if any(other != nm and nm in other and other in t for other in dish_locs):
            continue
        if not any(l in t for l in locs):
            missing.append("%s@缺位置" % nm)
    if missing:
        return False, "提到位置却没写食堂+楼层：%s" % "、".join(missing[:2])

    return True, "ok"

def compose(question, out, items, enabled=True):
    """返回开场白字符串；任何异常/校验不过 → ''（调用方退回规则话术）。"""
    if not enabled or not (items is not None):
        return ""
    try:
        facts = _facts(out, items)
        if not facts:
            return ""
        from services import llm
        if not llm.status().get("enabled"):
            return ""

        sig = hashlib.sha256(("%s||%s" % (question or "", facts)).encode("utf-8")).hexdigest()[:16]
        text = llm.chat([{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": "事实清单：\n%s\n\n用户问的是：%s" % (facts, question or "")}],
                        max_tokens=160, use_cache_key="intro:" + sig,
                        json_mode=False)      
        ok, why = verify(text, out, items, question)
        if not ok:
            sys.stderr.write("[intro] 校验不通过，已退回规则话术：%s ｜ %r\n" % (why, text[:80]))
            return ""
        return text.strip().strip('"“”')
    except Exception as e:                                   
        sys.stderr.write("[intro] 生成失败，已退回规则话术：%s: %s\n" % (type(e).__name__, e))
        return ""
