

import json
import re
from services import exposure
from services import lexicon

try:
    import time

    def _now():
        return time.strftime("%Y-%m-%d %H:%M:%S")
except Exception:  
    def _now():
        return ""

TASTE_TAGS = {"辣", "麻", "酸", "甜", "清淡"}

CAT_SCORE = {"米饭": 40, "快餐": 40, "面": 40, "粉": 40, "水饺包子": 40, "麻辣烫香锅": 40,
             "汉堡披萨": 40, "小吃饼类": 20, "粥汤": 15, "甜品": 15, "饮品": 15, "轻食": 15,
             # 2026-11：dish.category 用的是档口级品类名（米饭快餐/甜品饮品/轻食粥品），
             # 以前不在表里 → 666 道米饭套餐全按默认 15 分算，比「面」低一大截，排序失真
             "米饭快餐": 40, "甜品饮品": 15, "轻食粥品": 15,
             "面食": 40, "米粉": 40, "米饭快餐类": 40}

BURGER_SHOP_RE = re.compile(r"汉堡|堡")
DRINK_WORD_RE = re.compile(
    r"可乐|七喜|雪碧|美年达|芬达|红茶|绿茶|奶茶|果茶|果汁|咖啡|豆浆|酸梅汤|"
    r"柠檬水|饮料|汽水|奶饮品|黑米浆|椰汁|王老吉|加多宝|脉动|冰柠")
BURGER_STAPLE_RE = re.compile(r"堡|卷|饭|面|粉|披萨|意面|套餐")
BURGER_SIDE_RE = re.compile(
    r"鸡块|鸡翅|翅根|翅尖|鸡米花|鸡柳|鸡排|鸡架|鸡脖|水果派|蛋挞|摇摇|小食|"
    r"拼盒|三拼|薯条|粗薯|鸡叉骨")

DRINK_FOOD_RE = re.compile(r"鸡|翅|肉|鱼|蛋|饭|面|粉|薯|堡|卷|饺|包|粥|汤|菜|豆腐|馍|饼|肠|丸|虾")

def _is_burger_shop(name):
    return bool(BURGER_SHOP_RE.search(_s(name)))

def _is_standalone_drink(d):
    """汉堡窗口的独立饮品（套餐里的可乐、可乐鸡翅这种菜不算）。"""
    n = _s(d.get("name"))
    if not n or not DRINK_WORD_RE.search(n):
        return False
    if DRINK_FOOD_RE.search(n):
        return False
    return not BURGER_STAPLE_RE.search(n)

def _is_burger_staple(d):
    """汉堡窗口的主食：米饭/面/粉/汉堡/卷/披萨/意面/套餐；独立饮品和小食不算。"""
    n = _s(d.get("name"))
    if _is_standalone_drink(d):
        return False

    if BURGER_STAPLE_RE.search(n):
        return True
    if BURGER_SIDE_RE.search(n):
        return False
    return d.get("category") in ("米饭", "快餐", "面", "粉", "汉堡披萨")

BREAKFAST_START, BREAKFAST_END = 6 * 60 + 30, 9 * 60 + 30

def _now_minutes():
    import os
    env = (os.environ.get("CANTEEN_NOW") or "").strip()
    m = re.match(r"^(\d{1,2}):(\d{2})$", env)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    import datetime
    t = datetime.datetime.now()
    return t.hour * 60 + t.minute

def _in_breakfast_window():
    m = _now_minutes()
    return BREAKFAST_START <= m < BREAKFAST_END

ADDON_LIKE_EXACT = {"鸡蛋", "卤鸡蛋", "煎蛋", "卤蛋", "茶叶蛋", "水煮鸡蛋", "咸鸭蛋", "卤蛋/煎蛋"}

ADDON_PRICE_CAP = 3.0
ADDON_LIKE_ANY = ["油条", "烤肠", "香肠", "腊肠", "培根", "火腿肠", "丸子", "鱼丸", "贡丸",
                  "豆干", "豆泡", "腐竹", "油果", "臊子", "黄金豆", "方便面", "炸干", "花干", "素鸡", "香干"]

ADDON_MEAL_KEEP = ["包", "饺", "面", "饭", "粉", "馍", "饼", "盒", "炒", "丝", "汤", "粥",
                   "堡", "卷", "捞", "拌", "麦", "冬瓜", "白菜", "青菜", "豆腐", "豆芽",
                   "土豆", "茄子", "南瓜", "黄瓜", "瓜"]
ADDON_PENALTY = -35
DISPLAY_CAP = 12  

MULTI_MEAL_RE = re.compile(r"[二三四五六七八九\d]?人餐")

BUDGET_HINT_CAP = 50

PRICE_BANDS = ("≤5元", "5–10元", "10–15元", "15元+")
_BAND_RANGE = {"≤5元": (None, 5.0), "5–10元": (5.0, 10.0),
               "10–15元": (10.0, 15.0), "15元+": (15.0, None)}

def band_of_price(p):
    """数值价格 → 价格档标签；无价格返回 ''。"""
    if p is None or p == "":
        return ""
    try:
        p = float(p)
    except (TypeError, ValueError):
        return ""
    if p <= 5:
        return "≤5元"
    if p <= 10:
        return "5–10元"
    if p <= 15:
        return "10–15元"
    return "15元+"

def band_max(band):
    """价格档的上界（用于"都在 N 元以内"这类断言的校验）。"""
    r = _BAND_RANGE.get(band)
    if not r:
        return None
    return r[1] if r[1] is not None else 999.0

def budget_bands(s):
    """槽位预算 → 界面要亮起的价格档（纯展示，不参与筛选）。

      · 区间「5到10元」   → 与 [5,10] 相交的档
      · 近似「10块左右」  → 与 [8,12] 相交的档（= 问的价 + 左右档）
      · 上限「10元以内」  → 下界小于 10 的档
    """
    hi = (s or {}).get("budget")
    lo = (s or {}).get("budget_lo")
    if hi is None:
        return []
    try:
        hi = float(hi)
        lo = float(lo) if lo is not None else None
    except (TypeError, ValueError):
        return []
    if lo is not None:
        a, b = min(lo, hi), max(lo, hi)
    elif (s or {}).get("budget_around"):
        a, b = hi - 2.0, hi + 2.0
    else:
        a, b = None, hi
    out = []
    for band in PRICE_BANDS:
        blo, bhi = _BAND_RANGE[band]
        bmin = -1e9 if blo is None else blo
        bmax = 1e9 if bhi is None else bhi
        if a is None:
            hit = bmin < b
        else:
            hit = bmax >= a and bmin < b
        if hit:
            out.append(band)
    return out

def budget_label(s):
    bands = budget_bands(s)
    return " / ".join(bands)

EXCLUDE_NAME_HINTS = {
    '猪肉': re.compile(r'猪|五花|排骨|里脊|肉丝|肉片|肉末|肉沫|肉臊|肉酱|肘子|猪肘|'
                       r'腊肠|培根|火腿|肉夹馍|把子肉|红烧肉|小炒肉|肉丸|肉饼|肉松|叉烧'),
    '牛肉': re.compile(r'牛肉|牛排|牛柳|牛腩|牛筋|牛杂|肥牛|牛肚|牛舌|牛尾'),
    '鸡肉': re.compile(r'鸡肉|鸡翅|鸡腿|鸡排|鸡柳|鸡块|鸡丁|鸡丝|鸡米花|鸡爪|'
                       r'口水鸡|黄焖鸡|辣子鸡|火鸡|炸鸡|烤鸡|鸡架'),
    '鸭肉': re.compile(r'鸭肉|烤鸭|酱鸭|鸭腿|鸭翅|鸭脖|鸭掌|板鸭|黑鸭'),
    '羊肉': re.compile(r'羊肉|羊排|羊杂|羊蝎子'),
    '鱼虾': re.compile(r'鱼(?!香)|虾|蟹|鱿|龙虾|扇贝|蛤|蛏'),
    '蛋': re.compile(r'蛋'),
}

EXCLUDE_NAME_KEEP = {
    '猪肉': ('素肉', '素排', '素丸'),
    '鸡肉': ('素鸡', '鸡蛋'),
    '鱼虾': ('鱼香',),
    '牛肉': ('牛奶', '牛油果'),
    '鸭肉': ('鸭梨',),
    '羊肉': ('羊角',),
}
RELAX_NOTES = {0: "",
                1: "预算内没有完全符合条件的菜品，已将预算放宽 20% 为你挑选",
                2: "指定楼层内没有符合条件的菜品，已放宽到整个食堂",
                3: "严格匹配没有结果，已让掉最受限的一个偏好标签（忌口、过敏、素食类约束一律不放宽）",
                4: "严格匹配没有结果，已放宽部分口味/偏好类要求（忌口、过敏、素食类约束一律不放宽）"}

RELAXABLE_TAGS = frozenset({'辣', '麻', '酸', '甜', '清淡', '低卡', '轻食', '自选'})

def _s(x):
    if x is None:
        return ""
    return str(x).strip()

def _fnum(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None

def _dish_norm(d):
    """数据库行 → 统一结构。tags_str 拆成集合，价格转 float。"""
    name = _s(d.get("name"))
    if not name:
        return None
    return {
        "dish_id": _s(d.get("dish_id")), "name": name,
        "price": _fnum(d.get("price")), "spec": _s(d.get("spec")),
        "campus": _s(d.get("campus")), "canteen": _s(d.get("canteen")),
        "floor": _s(d.get("floor")), "shop": _s(d.get("shop")),
        "category": _s(d.get("category")),
        "img": _s(d.get("img")),

        "sold": (int(d["sold"]) if str(d.get("sold") or "").strip().isdigit()
                 else None),
        "tags": set(t for t in re.split(r"[,，、;；]", _s(d.get("tags_str"))) if t),

        "allergen_inferred": _s(d.get("allergen_inferred")),
    }

def _pass(d, s, budget, use_floor, req_tags):
    """单道菜是否通过当前阶段的全部条件（exclude / 过敏安全门 永不放宽）
    budget：None=不限；数字=价格上限；二元组=(下限,上限)——「10块左右」→(8,12) 区间"""

    if _is_burger_shop(d.get("shop")) and _is_standalone_drink(d):
        return False
    if s["canteen_q"] and d["canteen"] != s["canteen_q"]:
        return False
    if use_floor and s["floor_q"] and d["floor"] != s["floor_q"]:
        return False
    if s["shop_q"] and d["shop"] != s["shop_q"]:
        return False
    if budget is not None:
        if isinstance(budget, tuple):
            blo, bhi = budget
        else:
            blo, bhi = 0, budget
        if d["price"] is None or d["price"] > bhi or d["price"] < blo:
            return False  
    if d["tags"] & set(s["exclude"]):
        return False

    for _ex in s["exclude"]:
        _pat = EXCLUDE_NAME_HINTS.get(_ex)
        if _pat is None:
            continue
        _nm = d.get("name") or ""
        if _pat.search(_nm) and not any(k in _nm for k in EXCLUDE_NAME_KEEP.get(_ex, ())):
            return False

    if s.get("avoid_allergen"):
        hit = d.get("allergen_inferred") or ""
        if hit:
            for a in s["avoid_allergen"]:
                if a in hit.split(","):
                    return False

    _cat_req = [t for t in req_tags if t in CAT_SCORE]
    if _cat_req and not (d["tags"] & set(_cat_req)):
        return False
    for t in req_tags:
        if t in CAT_SCORE:
            continue
        # 2026-11：同义标签组过滤——查「稀饭」时，菜上打的是「粥/稀饭」都算命中；
        # 「馒头」组的「馍」同理。组由 services/lexicon.FAMILIES 维护。
        if not (d["tags"] & set(lexicon.match_group(t))):
            return False
    return True

def _allergen_excluded(dishes_raw, s):
    """统计有多少道菜因过敏安全门被排除（用于诚实说明，不参与打分）"""
    if not s.get("avoid_allergen"):
        return 0
    n = 0
    for raw in dishes_raw:
        hit = raw.get("allergen_inferred") or ""
        if hit and any(a in hit.split(",") for a in s["avoid_allergen"]):
            n += 1
    return n

def _is_addon_like(d):
    """加购/小食判定（仅展示降权，不参与过滤，不影响 396/72/28 等统计口径）"""
    n = d["name"]
    if MULTI_MEAL_RE.search(n):          
        return True
    if n.startswith("加") or n in ADDON_LIKE_EXACT:
        return True
    pr = d["price"]
    if pr is not None and pr <= ADDON_PRICE_CAP:
        if any(w in n for w in ADDON_LIKE_ANY):
            return True
        main = re.split(r"[（(]", n)[0].rstrip()
        if main.endswith("蛋"):
            return True
        if not any(w in n for w in ADDON_MEAL_KEEP):
            return True  
    return False

STAPLE_SMALL_EXACT = {"花卷", "肉花卷", "水煎包", "烧麦"}

STAPLE_SMALL_PRICE_CAP = 6.0
EGG_SNACK_RE = re.compile(r"(卤|煎|茶叶|水煮|虎皮|溏心|咸鸭|鹌鹑|香)?鸡?蛋")   
EGG_PRICE_CAP = 3.0

def _snack_filtered(d, s):
    """推荐候选阶段的三组硬过滤。点名保护：关键词/直接命中词命中菜名的菜不滤（搜索语义不变）。"""
    n = d["name"]
    _named = list(s.get("kw") or ()) + list(s.get("terms") or ())
    if _named and any(k and k in n for k in _named):
        return False
    pr = d["price"]
    main = re.split(r"[（(]", n)[0].rstrip()

    if n.startswith("加") or n in {"方便面", "炸干", "黄金豆"}:
        return True
    if pr is not None and pr <= ADDON_PRICE_CAP and any(w in n for w in ADDON_LIKE_ANY) \
            and not any(w in n for w in ADDON_MEAL_KEEP):
        return True

    if pr is not None and pr <= EGG_PRICE_CAP and (EGG_SNACK_RE.fullmatch(main) or n in ADDON_LIKE_EXACT):
        return True

    if pr is not None and pr <= STAPLE_SMALL_PRICE_CAP and "早餐" not in s.get("tags", []) \
            and not s.get("breakfast_gate"):
        if main in STAPLE_SMALL_EXACT or main.endswith("烧麦"):
            return True
        if main.endswith("包") and "堡" not in n and "汉堡" not in n:   
            return True
    return False

def _score(d, s):
    """8 维整数打分器（可实现维度）：正餐优先 + 标签命中 + 关键词 + 价格契合 + 加购降权
    + 用户画像（2026-10-12 新增：历史偏好标签 ×6 分上限，权重 0~1；
      画像固定时打分确定 → 「同问同画像必同答」仍成立）"""
    sc = CAT_SCORE.get(d["category"], 15)

    if _is_burger_shop(d.get("shop")):
        if _is_burger_staple(d):
            sc += 18
        elif _is_standalone_drink(d):
            sc -= 30
    # 2026-11：标签命中加权——细分标签（馒头/稀饭/米线…）比粗品类标签更有信息量。
    # 同义标签组（粥/稀饭）按组命中算一次，不重复加分。
    for _t in s["tags"]:
        if _t in d["tags"]:
            sc += 14 if lexicon.TAG_DIM.get(_t) == "细分" else 10
    # 2026-11 用户要求「直接命中的优先」：用户原话里的食物词出现在菜名里 → 大加分；
    # 同义叫法（说稀饭→菜名含粥）→ 中加分。排序里还有一层 tier 硬优先（见 _direct_tier）。
    _terms = s.get("terms") or ()
    if _terms:
        for _term in _terms:
            if _term and _term in d["name"]:
                sc += 25
        _alias_surfs = lexicon.surfaces_for_tags(s["tags"])
        for _al in _alias_surfs:
            if _al and _al not in _terms and _al in d["name"]:
                sc += 10
    pref = s.get("pref")
    if pref:
        sc += int(round(6 * sum(pref.get(t, 0.0) for t in d["tags"])))
    pref_kw = s.get("pref_kw")
    if pref_kw:
        sc += int(round(6 * sum(pref_kw.get(k, 0.0) for k in s["kw"] if k in d["name"])))

    # —— 账号画像（2026-11）：常去食堂/常点档口/常消费价位，只加分不过滤 ——
    prof = s.get("profile")
    if prof:
        sc += int(round(8 * (prof.get("canteen_w") or {}).get(d.get("canteen") or "", 0.0)))
        sc += int(round(4 * (prof.get("shop_w") or {}).get(d.get("shop") or "", 0.0)))
        _pp = prof.get("price_p")
        if _pp is not None and d["price"] is not None and abs(d["price"] - _pp) <= 2:
            sc += 3

    if s["kw"]:
        sc += 8 * sum(1 for k in s["kw"] if k in d["name"])
        # 更具体的关键词命中（土豆丝 > 土豆）额外加分
        sc += 3 * max([len(k) for k in s["kw"] if k in d["name"]] or [0])
    if s["budget"] and d["price"] is not None:
        if s.get("budget_around"):

            diff = abs(d["price"] - s["budget"])
            if diff <= 1:
                sc += 5
            elif diff <= 2:
                sc += 3
        else:
            if d["price"] <= s["budget"]:
                sc += 5
            if d["price"] <= s["budget"] * 0.6:
                sc += 3
    if _is_addon_like(d):
        sc += ADDON_PENALTY

    mods = s.get("modifier") or ()
    if mods:
        tg = d["tags"]
        cat = d["category"]
        if "more_spicy" in mods and ({"辣", "麻"} & tg):
            sc += 6
        if "lighter" in mods:

            if {"清淡", "低卡", "轻食", "粥汤", "凉拌"} & tg:
                sc += 6
            if {"辣", "麻", "炸"} & tg:
                sc -= 5
        if "quicker" in mods:

            if cat == "小吃饼类":
                sc += 6
            elif cat in ("面", "粉"):
                sc += 2
            elif cat in ("米饭", "快餐"):
                sc -= 3

    return sc

# 泛主食词（问「面/米饭/粉/粥/汤」时按综合分排，不按名字长短排）
_GENERIC_STAPLE = {"面", "米饭", "粉", "粥", "汤"}


def _direct_tier(d, s):
    """直接命中层级（2026-11 用户要求「想吃馒头时优先直接命中的」）：

      0 = 菜名里直接含用户原话的食物词（馒头 → 馒头/菜馒头）
      1 = 菜名里含同义叫法（说稀饭 → 菜名含粥；说馒头 → 菜名含馍/白馍）
      2 = 只是标签组命中（馍标签的肉夹馍等）
    无直接命中词时恒为 0，不影响老逻辑。
    """
    terms = s.get("terms") or ()
    if not terms:
        return 0
    n = d["name"]
    for t in terms:
        if t and t in n:
            return 0
    for a in lexicon.surfaces_for_tags(s["tags"]):
        if a and a in n:
            return 1
    return 2


def _sort_tier(d, s):
    """排序用层级：菜名与用户原词**完全相等**最优（-1），其次直接命中 0/1/2。"""
    n = d["name"]
    if n in (s.get("terms") or ()):
        return -1
    if n in (s.get("kw") or ()):
        return -1
    return _direct_tier(d, s)


def _kw_hit_len(d, s):
    """菜名命中的最长关键词长度（土豆丝=3 > 土豆=2，排序里更具体的关键词优先）。"""
    best = 0
    n = d["name"]
    for k in (s.get("kw") or ()):
        if k and k in n and len(k) > best:
            best = len(k)
    return best


def _sim(a, b):
    """菜品间相似度（确定性，供 MMR 多样性用）：
    同档口=1.0（最强连坐惩罚）；否则 标签Jaccard*0.6 + 同品类0.3，封顶 0.9"""
    if a["shop"] == b["shop"]:
        return 1.0
    ta, tb = a["tags"], b["tags"]
    sim = 0.0
    if ta & tb:
        sim += 0.6 * len(ta & tb) / len(ta | tb)
    if a["category"] == b["category"]:
        sim += 0.3
    return min(sim, 0.9)

def _mmr_diversify(ranked, cap, lam=0.7):
    """MMR 多样性重排（2026-10-12，Carbonell & Goldstein 1998 的 MMR 准则；
    工程参照 Uber Eats 个性化多样化：解决「推荐列表全是同一家/同一类」）。
    MMR(i) = λ·rel(i) − (1−λ)·max_{j∈已选} sim(i,j)，λ=0.7 相关性优先。
    TOP1 固定取原排序第一（相关性锚点）；全程无随机 → 同问同画像必同答（I1 保持）。
    ranked：已按业务序（销量/价格）排好的列表；cap：取前几个。"""
    pool = ranked[:max(cap * 4, 24)]
    n = len(pool)
    if n <= 2:
        return pool[:cap]
    rel = [1.0 - i / (n - 1) for i in range(n)]        
    sel = [0]                                          
    rest = list(range(1, n))
    while rest and len(sel) < cap:
        best_i, best_v = None, -1e18
        for i in rest:
            msim = max(_sim(pool[i], pool[j]) for j in sel)
            v = lam * rel[i] - (1 - lam) * msim
            if v > best_v:
                best_i, best_v = i, v
        sel.append(best_i)
        rest.remove(best_i)
    return [pool[i] for i in sel]

def _one_per_shop(ranked, cap, exempt_cats=()):
    """每档口只出一道（2026-10-12 v4 用户决策：优先推不同商家的菜，看着均衡）。
    「十块钱左右的面」以前 MMR 软惩罚挡不住高销量档口连出 8 道户县软面；
    现在严格硬约束：按业务排序序遍历，每个档口只取它的第一道。
    档口数不足 cap 就只出这些——均衡优先于凑数（点名档口/点名菜走外面的
    kw_used / shop_q 分支拿原排序，不吃亏）。
    全程无随机 → 同问同画像必同答（I1 保持）。

    exempt_cats（2026-10-19 边界修）：**用户明确点名过的品类**不参与"每品类 ≤3 道"配额。
      原 bug：「想吃面」387 道候选全属"面"品类，被配额砍到只剩 3 条 ——
      用户要 12 碗面只给 3 碗，多样性做成了饥饿。单品类候选同理不启用配额。
    """

    CAT_QUOTA = 3

    cats = {(d.get("category") or d.get("cat") or '') for d in ranked}
    cats.discard('')
    exempt = set(exempt_cats or ())
    _use_quota = len(cats) > 1 and len(ranked) > cap * 2
    by_shop = {}
    for d in ranked:
        by_shop.setdefault(d["shop"], []).append(d)      
    top, cat_n = [], {}
    for d in ranked:
        sh = d["shop"]
        if sh not in by_shop:
            continue
        cat = d.get("category") or d.get("cat") or ''
        if _use_quota and cat and cat not in exempt and cat_n.get(cat, 0) >= CAT_QUOTA:
            continue
        top.append(by_shop.pop(sh)[0])
        if cat:
            cat_n[cat] = cat_n.get(cat, 0) + 1
        if len(top) >= cap:
            return top
    return top

def _slots_text(s):
    bits = []
    if s.get("canteen_q"):
        bits.append(s["canteen_q"])
    if s.get("floor_q"):
        bits.append(s["floor_q"])
    if s.get("shop_q"):
        bits.append("档口:" + s["shop_q"])
    if s.get("building"):
        bits.append("%s号楼·近%s" % (s["building"], s.get("building_pref") or ""))
    if s.get("budget"):
        _lo = s.get("budget_lo")
        if _lo is not None:
            bits.append("预算%s–%s元" % (_lo, s["budget"]))
        elif s.get("budget_around"):
            bits.append("预算%s元左右" % s["budget"])
        else:
            bits.append("预算≤%s元" % s["budget"])
    if s.get("tags"):
        bits.append("要:" + "+".join(s["tags"]))
    if s.get("exclude"):
        bits.append("排除:" + "+".join(s["exclude"]))

    if s.get("avoid_unmapped"):
        bits.append("忌口(未识别):" + "+".join(s["avoid_unmapped"]))
    if s.get("avoid_allergen"):
        bits.append("过敏原:" + "+".join(s["avoid_allergen"]))
    if s.get("kw"):
        bits.append("关键词:" + "+".join(s["kw"]))
    if s.get("cheap"):
        bits.append("要便宜")
    return "；".join(bits) if bits else "无特定条件（随便推荐）"

def _avoid_note(s):
    """落空忌口的诚实说明（2026-10-12 新增）：识别不了的忌口绝不假装已生效"""
    if not s.get("avoid_unmapped"):
        return ""
    return ("忌口「%s」不在菜品标签可识别范围（库内无此食材标记），以下结果不保证不含它，"
            "打饭后请与师傅口头确认" % "、".join(s["avoid_unmapped"]))

def _budget_hint(s, rows=None):
    """预算远超库内单品上限时的引导（2026-10-12 新增）。

    2026-10-13 修：原文案写死「最贵是多人套餐 ¥53」——**对用户报了个假数字**。
    库里可推荐单品的真实最高价是 ¥79（小吃街·翘力仕炒鸡 的现炒鸡/黑鸭煲，
    没有一个是多人套餐）。现在按实际行集算；算不出来就不写括号里的数字。
    """
    b = s.get("budget")
    if not (b and b > BUDGET_HINT_CAP):
        return ""

    return ("预算 %d 元很充足了～菜品都标实际价格，随便挑" % b)

NO_HIT_TEXT = '暂时没理解你的意思，换个说法试试'

def _empty(s, reason, n_alg=0):
    return {"has_result": False, "display_text": "", "dishes_json": "[]",
            "total_count": 0, "shop_count": 0, "relax_note": "", "kw_note": "",
            "allergy_note": bool(s.get("allergy_note")), "top_dish": "",
            "slots_text": _slots_text(s), "stage": 0, "reason": reason or NO_HIT_TEXT,
            "allergen_excluded": n_alg,
            "allergen_note_text": ("全库共 %d 道菜可能含%s，已一律排除在推荐之外"
                                   "（按菜名推测，平台未登记过敏原）"
                                   % (n_alg, "、".join(s["avoid_allergen"]))) if n_alg else "",
            "avoid_note": _avoid_note(s), "budget_hint": _budget_hint(s),
            "loc_note": "", "breakfast_note": "",
            "created_at": _now()}

def recommend(dishes_raw, s, pref=None, pref_kw=None, picker=None):
    """纯逻辑核心：合并两食堂查询结果 → 过滤 → 打分 → 分组展示（本地自测与扣子共用）
    pref / pref_kw：用户画像（标签权重 / 关键词权重，来自 services/profile.py），
    None 时退化为无画像

    picker（2026-10-13 新增，LLM 放权 L2）：可选的"候选池挑选器"回调，
    签名 `picker(candidates) -> [dish_id, ...]`，用来决定**展示哪些、按什么顺序**。

    为什么用依赖注入而不是在这里 import LLM：
      本模块是"决策 100% 确定性、可单测、可复现"的那一层，**不能引入网络依赖**。
      挑选器由路由层传入（`services/llm_pick.py`），本层只负责：
        · 把**已过完全部硬约束**（忌口/过敏/预算/放宽）的候选交给它；
        · 校验它返回的 id 必须都在候选里（不在的一律忽略）；
        · 返回空/抛异常 → 保持规则排序（LLM 挂了就等于没启用）。
      也就是说：**LLM 只能重排事实，不能产生事实。**
    """
    if s.get("campus_ok") is False:
        return _empty(s, "这个校区还没采集菜品数据（目前只有鄠邑校区的东食堂、西食堂和小吃街）")
    if pref or pref_kw:
        s = dict(s)
        if pref:
            s["pref"] = pref          
        if pref_kw:
            s["pref_kw"] = pref_kw

    dishes = []
    for d in dishes_raw:
        nd = _dish_norm(d)
        if nd:
            dishes.append(nd)

    breakfast_gate = _in_breakfast_window() and "早餐" not in (s.get("tags") or [])
    if breakfast_gate:
        s = dict(s)
        s["breakfast_gate"] = True
    eff_tags = list(s["tags"]) + (["早餐"] if breakfast_gate else [])

    dishes = [nd for nd in dishes if not _snack_filtered(nd, s)]

    b = s.get("budget")
    lo = s.get("budget_lo")
    around = bool(b and s.get("budget_around"))
    if b and lo is not None:                     
        _rng = (float(min(lo, b)), float(max(lo, b)))
        bud0 = bud1 = _rng
    elif around:
        bud0 = (max(0, int(b * 0.8)), int(round(b * 1.2)))
        bud1 = (max(0, int(b * 0.6)), int(round(b * 1.4)))
    else:
        bud0 = (0, b) if b else None
        bud1 = (0, int(round(b * 1.2))) if b else None

    tags0 = tuple(eff_tags)

    protected = [t for t in tags0 if t not in RELAXABLE_TAGS]
    relaxable = [t for t in tags0 if t in RELAXABLE_TAGS]
    ladder = [tuple(eff_tags)]                      
    pool = list(relaxable)
    while pool:

        cnt = {t: sum(1 for d in dishes if t in d["tags"]) for t in pool}
        rarest = min(pool, key=lambda t: (cnt[t], t))
        pool.remove(rarest)
        ladder.append(tuple(protected + pool))
    base = [(0, bud0, True, ladder[0]),
            (1, bud1, True, ladder[0]),
            (2, bud1, False, ladder[0])]
    for i, lv in enumerate(ladder[1:], start=3):     
        base.append((i, bud1, False, lv))
    base = tuple(base)

    pref_canteen = s.get("building_pref") if not s.get("canteen_q") else None
    plan = []
    if pref_canteen:
        for st in base:
            if st + (pref_canteen,) not in plan:
                plan.append(st + (pref_canteen,))
    for st in base:
        if st + (None,) not in plan:
            plan.append(st + (None,))

    picked, stage, used_pref = [], 0, None
    for st in plan:
        ss = dict(s, canteen_q=st[4]) if st[4] else s
        picked = [d for d in dishes if _pass(d, ss, st[1], st[2], list(st[3]))]
        stage, used_pref = st[0], st[4]
        if picked:
            break

    breakfast_note = ""
    kw_used = None
    if s["kw"] and picked:
        hit = [d for d in picked if any(k in d["name"] for k in s["kw"])]
        if hit:
            picked, kw_used = hit, True
        else:
            has_other = (s["canteen_q"] or s["floor_q"] or s["shop_q"]
                         or s["budget"] or s["tags"])
            if not has_other and not breakfast_gate:
                kwtxt = "、".join(s["kw"][:3])
                return _empty(s, "暂时没有「%s」这道菜，换个说法试试" % kwtxt)
            if not has_other and breakfast_gate:

                breakfast_note = "现在是早餐时段（6:30–9:30），只供应早餐；你点的菜这个时段还没有，先看看早餐吧～"
            kw_used = False

    if not picked and breakfast_gate:
        gate_tags = ("早餐",)
        base_bf = ((0, bud0, True, gate_tags), (1, bud1, True, gate_tags),
                   (2, bud1, False, gate_tags), (3, bud1, False, gate_tags))
        plan_bf = []
        if pref_canteen:
            for st in base_bf:
                plan_bf.append(st + (pref_canteen,))
        for st in base_bf:
            plan_bf.append(st + (None,))
        for st in plan_bf:
            ss = dict(s, canteen_q=st[4]) if st[4] else s
            ss = dict(ss, kw=[])          
            picked = [d for d in dishes if _pass(d, ss, st[1], st[2], list(st[3]))]
            stage, used_pref = st[0], st[4]
            if picked:
                s = ss                    
                breakfast_note = "现在是早餐时段（6:30–9:30），你想要的品类还没上，先给你挑了早餐～"
                break
        if not picked:
            return _empty(s, "现在是早餐时段（6:30–9:30），这个条件下没有早餐可选，"
                             "换个条件试试（比如粥、包子、豆浆、饼）")

    if not picked:

        if s.get("kw"):
            return _empty(s, "暂时没有「%s」这道菜，换个说法试试" % "、".join(s["kw"][:3]))
        return _empty(s, NO_HIT_TEXT)

    for d in picked:
        d["_sc"] = _score(d, s)

    seed_key = s.get("seed_key") or ""
    sess = s.get("session_seen") or set()
    pops = [d.get("sold") for d in picked if d.get("sold") is not None]
    max_sold = max(pops) if pops else 1
    scs = [d["_sc"] for d in picked] or [1]
    lo_sc, hi_sc = min(scs), max(scs)
    import math as _math

    _explicit = bool(s.get("tags") or s.get("kw") or s.get("budget") or s.get("shop_q"))
    _w_match, _w_jit = (0.75, 0.06) if _explicit else (0.4, 0.15)
    _epm = s.get("exp_pen_map")            # 登录用户：曝光按人隔离（空 dict = 无惩罚）
    for d in picked:
        sold = d.get("sold")

        pop = 0.5 if sold is None else min(1.0, _math.log1p(sold) / _math.log1p(max_sold))
        match = (d["_sc"] - lo_sc) / float((hi_sc - lo_sc) or 1)
        qual = (1.0 - _w_match) * pop + _w_match * match
        did = d.get("dish_id")
        if _epm is not None:
            pen = _epm.get("dish:%s" % did, 0.0) if did else 0.0
        else:
            pen = exposure.penalty("dish:%s" % did) if did else 0.0
        sess_pen = 1.0 if did in sess else 0.0
        d["_v"] = (qual - 0.5 * pen - 0.9 * sess_pen
                   + _w_jit * exposure.jitter(seed_key, did))

    def _sold_key(d):
        v = d.get("sold")
        return -1 if v is None else v
    _has_hit = bool(s.get("terms") or s.get("kw") or s.get("food_terms"))
    _tf = (lambda d: _sort_tier(d, s)) if _has_hit else (lambda d: 0)
    _kf = (lambda d: -_kw_hit_len(d, s)) if s.get("kw") else (lambda d: 0)
    # 2026-11：点名**具体**食物时「名字越短越纯」优先（豆浆 > 纯手工灌汤包+现磨豆浆）；
    # 泛主食（面/米饭/粉/粥/汤）与口味/特点类查询不按名字长短排，避免便宜小菜冒头
    _specific = [t for t in (s.get("tags") or ())
                 if lexicon.TAG_DIM.get(t) == "细分" and t not in _GENERIC_STAPLE]
    _lf = (lambda d: len(d["name"])) if (s.get("kw") or (s.get("food_terms") and _specific)) \
        else (lambda d: 0)
    if s.get("cheap"):          
        picked.sort(key=lambda d: (_tf(d), _kf(d), _lf(d),
                                   d["price"] if d["price"] is not None else 999.0,
                                   -d.get("_v", 0.0), d["dish_id"]))
    elif s.get("shop_q") and _is_burger_shop(s.get("shop_q")):

        picked.sort(key=lambda d: (_tf(d), _kf(d), _lf(d), 0 if _is_burger_staple(d) else 1,
                                   -d.get("_v", 0.0), d["dish_id"]))
    elif [m for m in (s.get("modifier") or ()) if m in ("more_spicy", "lighter", "quicker")]:

        picked.sort(key=lambda d: (_tf(d), _kf(d), _lf(d), -d.get("_sc", 0),
                                   -d.get("_v", 0.0), d["dish_id"]))
    else:
        picked.sort(key=lambda d: (_tf(d), _kf(d), _lf(d), -d.get("_v", 0.0), d["dish_id"]))

    total = len(picked)
    shops = sorted(set(d["shop"] for d in picked))

    if s.get("shop_q") or (kw_used is True and len(shops) <= 2) or (
            (s.get("terms") or s.get("kw")) and len(picked) <= 16):
        # 2026-11：用户点名食物（馒头/花卷/煎蛋…）且候选很少时，不再"每档口只出一道"，
        # 否则同一档口的「花卷/肉花卷」会被砍到只剩一道，用户看不到点名的那道
        top = picked[:DISPLAY_CAP]          
    else:

        _asked_cats = {t for t in (s.get("tags") or ()) if t in CAT_SCORE}
        top = _one_per_shop(picked, DISPLAY_CAP, exempt_cats=_asked_cats)

        _open_q = not (s.get("tags") or s.get("kw") or s.get("budget")
                       or s.get("shop_q") or s.get("cheap"))
        if _open_q and len(top) >= 6:
            have_shops = {d["shop"] for d in top}
            have_ids = {d["dish_id"] for d in top}
            cat_n2 = {}
            for d in top:
                c2 = d.get("category") or ""
                cat_n2[c2] = cat_n2.get(c2, 0) + 1
            fresh = []
            for d in picked:
                if d.get("sold") is not None or d.get("dish_id") in have_ids:
                    continue
                if d["shop"] in have_shops:
                    continue
                c2 = d.get("category") or ""
                if c2 and cat_n2.get(c2, 0) >= 3:
                    continue
                fresh.append(d)
                have_shops.add(d["shop"])
                if c2:
                    cat_n2[c2] = cat_n2.get(c2, 0) + 1
                if len(fresh) >= 2:
                    break
            if fresh:
                top = top[:DISPLAY_CAP - len(fresh)] + fresh

    if not (s.get("canteen_q") or s.get("floor_q") or s.get("shop_q")
            or s.get("budget") or s.get("tags") or s.get("exclude")
            or s.get("avoid_raw") or s.get("kw") or s.get("cheap")):
        try:
            import datetime, hashlib
            day = datetime.date.today().isoformat()
            seed = int(hashlib.md5(day.encode("utf-8")).hexdigest()[:8], 16)
        except Exception:
            seed = 0
        start = seed % len(shops) if shops else 0
        order = shops[start:] + shops[:start]
        by_shop = {}
        for d in picked:
            by_shop.setdefault(d["shop"], []).append(d)      
        top = []
        for sh in order:
            if by_shop.get(sh):
                top.append(by_shop[sh][0])
            if len(top) >= DISPLAY_CAP:
                break
        if len(top) < DISPLAY_CAP:
            chosen = {d["dish_id"] for d in top}
            top += [d for d in picked if d["dish_id"] not in chosen][:DISPLAY_CAP - len(top)]

    n_alg = _allergen_excluded(dishes_raw, s)

    pick_source = 'rule'
    if picker is not None and picked:
        pool = picked[:60]
        try:
            want = [str(x) for x in (picker(pool) or [])]
        except Exception:                                     
            want = []
        idx = {d['dish_id']: d for d in pool}
        chosen, seen = [], set()
        for did in want:
            d = idx.get(did)
            if d is not None and did not in seen:
                chosen.append(d)
                seen.add(did)
            if len(chosen) >= DISPLAY_CAP:
                break
        if chosen:

            rest = [d for d in top if d['dish_id'] not in seen]
            top = (chosen + rest)[:DISPLAY_CAP]
            pick_source = 'llm'

    alg_text = ("全库共 %d 道菜可能含%s，已一律排除在推荐之外"
                "（按菜名推测，平台未登记过敏原）"
                % (n_alg, "、".join(s["avoid_allergen"]))) if n_alg else ""

    if breakfast_gate and not breakfast_note:
        breakfast_note = "早餐时段（6:30–9:30）：只给你保留了早餐的饭，其他品类 9:30 后陆续供应"
    loc_note = ""
    if pref_canteen:
        if used_pref:
            loc_note = "你在%s号楼，离%s更近，优先挑了这边的" % (s.get("building"), used_pref)
        else:
            loc_note = "离你近的%s没有完全符合的，给你看了全部食堂" % pref_canteen

    lines = []
    for d in top:
        pr = (("%g元" % d["price"]) if d["price"] is not None else "")
        nm = d["name"] + (("（%s）" % d["spec"]) if d["spec"] else "")
        loc = d["floor"] if s.get("canteen_q") else (d["canteen"] + d["floor"])
        lines.append("%s · %s · %s%s" % (loc, d["shop"], nm, ((" " + pr) if pr else "")))

    kw_note = "" if kw_used in (None, True) else "关键词未直接命中菜名，已按其余条件推荐"
    return {
        "has_result": True,
        "display_text": "\n".join(lines),
        "dishes_json": json.dumps(
            [dict({k: d.get(k) for k in ("dish_id", "name", "price", "spec", "campus",
                                          "canteen", "floor", "shop", "category", "img")},
                   price_band=band_of_price(d.get("price")))
             for d in top], ensure_ascii=False),
        "total_count": total, "shop_count": len(shops),

        "relax_note": RELAX_NOTES[min(stage, max(RELAX_NOTES))], "kw_note": kw_note,
        "allergy_note": bool(s.get("allergy_note")), "top_dish": top[0]["name"],
        "slots_text": _slots_text(s), "stage": stage, "reason": "",
        "allergen_excluded": n_alg, "allergen_note_text": alg_text,
        "avoid_note": _avoid_note(s), "budget_hint": _budget_hint(s, dishes_raw),
        "loc_note": loc_note, "breakfast_note": breakfast_note,

        "pick_source": pick_source,
        "created_at": _now(),
    }
