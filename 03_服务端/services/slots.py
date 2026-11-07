

import re

from services import lexicon

# 口语/别名 → 规范标签（2026-11 大改：改成**细分标签**，馒头→馒头、稀饭→稀饭、粥→粥，
# 不再把馒头/馍糊成「小吃饼类」，也不再让「稀饭」沾上「米饭」。）
TAG_SYNONYM = {

    # —— 主食细分（同义叫法都归到细分标签，菜上打全同义标签后都能命中）——
    "米饭": "米饭", "白饭": "米饭", "大米饭": "米饭", "饭": "米饭",
    "午饭": "米饭", "晚饭": "米饭", "吃饭": "米饭",
    "盖饭": "盖饭", "盖浇饭": "盖饭", "盖浇": "盖饭",
    "炒饭": "炒饭", "蛋炒饭": "炒饭", "拌饭": "拌饭", "饭团": "饭团",
    "面条": "面", "拉条子": "面", "面食": "面",
    "拌面": "拌面", "干拌面": "拌面", "汤面": "汤面", "炒面": "炒面",
    "焖面": "焖面", "凉面": "凉面", "臊子面": "臊子面", "油泼面": "油泼面",
    "蘸水面": "蘸水面", "刀削面": "刀削面", "拉面": "拉面", "烩面": "烩面",
    "麻食": "麻食", "意面": "意面", "意大利面": "意面",
    "方便面": "方便面", "泡面": "方便面",
    "米线": "米线", "米粉": "米粉", "粉条": "粉条", "河粉": "河粉",
    "米线粉": "粉", "粉丝": "粉丝", "土豆粉": "土豆粉",
    "螺蛳粉": "螺蛳粉", "酸辣粉": "酸辣粉", "渔粉": "渔粉", "五谷渔粉": "渔粉",
    "粥": "粥", "稀饭": "稀饭", "稀粥": "稀饭", "米粥": "粥", "白粥": "粥",
    "汤": "汤", "汤水": "汤", "喝汤": "汤",

    # —— 馍 / 包 / 饺 / 饼 ——
    "馒头": "馒头", "馍": "馍", "馍馍": "馒头", "白馍": "馒头",
    "花卷": "花卷", "肉花卷": "花卷",
    "包子": "包子", "肉包": "包子", "菜包": "包子", "素包": "包子",
    "灌汤包": "包子", "小笼包": "包子", "生煎包": "包子",
    "水饺": "饺子", "蒸饺": "饺子", "煎饺": "饺子", "锅贴": "饺子",
    "饺子": "饺子", "馄饨": "馄饨", "云吞": "馄饨", "抄手": "馄饨",
    "烧麦": "烧麦", "烧卖": "烧麦",
    "饼": "饼", "烧饼": "饼", "烙饼": "饼", "煎饼": "饼", "馅饼": "饼",
    "手抓饼": "饼", "葱油饼": "饼", "酱香饼": "饼", "鸡蛋灌饼": "饼",
    "肉夹馍": "夹馍", "夹馍": "夹馍", "加馍": "夹馍", "腊汁肉夹馍": "夹馍",
    "锅盔": "锅盔", "煮馍": "煮馍", "泡馍": "煮馍", "羊肉泡馍": "煮馍",
    "凉皮": "凉皮", "擀面皮": "凉皮", "米皮": "凉皮", "面皮": "凉皮", "酿皮": "凉皮",
    "卷饼": "卷饼", "鸡肉卷": "卷饼", "牛肉卷": "卷饼",

    # —— 西式 / 其他 ——
    "汉堡": "汉堡", "披萨": "披萨", "比萨": "披萨",
    "炸鸡": "炸鸡", "炸鸡腿": "炸鸡", "炸鸡翅": "炸鸡", "鸡米花": "炸鸡",
    "鸡柳": "炸鸡", "鸡排": "炸鸡", "鸡叉骨": "炸鸡",
    "薯条": "薯条", "烤肠": "烤肠", "烧烤": "烧烤", "烤肉串": "烧烤",
    "麻辣烫": "麻辣烫", "冒菜": "冒菜", "香锅": "香锅", "麻辣拌": "麻辣拌",
    "轻食": "轻食", "沙拉": "沙拉", "健身餐": "沙拉", "寿司": "寿司",
    "套餐": "套餐", "盒饭": "盒饭",
    "卤味": "卤味", "卤菜": "卤味", "卤肉": "卤味",
    "丸子": "丸子", "肉丸": "丸子", "狮子头": "丸子",
    "豆浆": "豆浆", "油条": "油条", "煎蛋": "煎蛋", "荷包蛋": "煎蛋",
    "卤蛋": "卤蛋", "卤鸡蛋": "卤蛋", "茶叶蛋": "茶叶蛋", "茶蛋": "茶叶蛋",
    "水煮蛋": "水煮蛋", "白煮蛋": "水煮蛋",
    "奶茶": "奶茶", "轻乳茶": "奶茶", "烤奶": "奶茶",
    "咖啡": "咖啡", "拿铁": "咖啡", "美式": "咖啡",
    "果汁": "果汁", "果茶": "果汁", "柠檬水": "果汁", "酸梅汤": "果汁",
    "汽水": "汽水", "可乐": "汽水", "雪碧": "汽水", "冰峰": "汽水",
    "茶饮": "茶饮", "红茶": "茶饮", "绿茶": "茶饮",
    "蛋糕": "蛋糕", "蛋挞": "蛋挞", "布丁": "布丁", "双皮奶": "双皮奶",
    "冰淇淋": "冰淇淋", "圣代": "冰淇淋", "酸奶": "酸奶",
    "水果捞": "水果捞", "杨枝甘露": "水果捞", "奶昔": "奶昔",
    "砂锅": "砂锅", "年糕": "年糕", "肠粉": "肠粉",
    "胡辣汤": "胡辣汤", "糊辣汤": "胡辣汤", "豆腐脑": "豆腐脑", "豆花": "豆腐脑",
    "菜盒": "菜盒", "麻团": "麻团",

    # —— 旧词表兼容（LLM 可能吐这些词）——
    "养生": "粥汤", "早点": "早餐", "早饭": "早餐", "早餐": "早餐", "早点吃": "早餐",
    "荤": "荤菜", "素的": "素食", "素食": "素食", "吃素": "素食",
    "肉类": "荤菜", "肉": "荤菜", "肉食": "荤菜", "荤菜类": "荤菜",
    "凉菜": "凉拌", "凉拌菜": "凉拌", "烧烤卤味": "烧烤",
    "饮品": "饮品", "饮料": "饮品", "喝的": "饮品", "喝的饮品": "饮品",
    "甜品": "甜品", "甜点": "甜品", "糕点": "甜品",
}

ALLERGEN_KEYWORD = [
    ("海鲜/甲壳类", ("海鲜", "甲壳", "虾", "蟹", "贝", "蛤", "鱿鱼", "章鱼", "生蚝")),
    ("鱼类", ("鱼",)),
    ("蛋类", ("蛋", "鸡蛋", "鸭蛋", "鹌鹑蛋")),
    ("奶类", ("奶", "乳", "芝士", "奶酪", "黄油")),
    ("花生", ("花生",)),
    ("坚果/芝麻", ("坚果", "芝麻", "麻酱", "核桃", "腰果", "杏仁", "榛子")),
    ("大豆", ("大豆", "黄豆", "豆浆", "豆腐", "腐竹", "豆干", "豆皮")),
    ("麸质（小麦）", ("麸质", "面筋", "小麦", "麦", "gluten")),
]

FLOOR_ALIAS = {"一楼": ["一楼", "1楼", "一层", "1f", "1F"],
               "二楼": ["二楼", "2楼", "二层", "2f", "2F"],
               "三楼": ["三楼", "3楼", "三层", "3f", "3F"]}

CANTEEN_ALIAS = {"东食堂": ["东食堂", "东餐厅", "东", "东区", "东一层", "东二楼",
                            "二食堂", "二号食堂", "第二食堂", "新食堂", "南食堂", "南餐厅"],
                 "西食堂": ["西食堂", "西餐厅", "西", "西区", "西一层", "西二楼",
                            "一食堂", "一号食堂", "第一食堂", "老食堂"],
                 "小吃街": ["小吃街", "科技街", "校门口小吃", "小吃"]}

TASTE_EXCLUDE_TAGS = ["辣", "麻", "酸", "甜", "清淡", "咸", "鲜"]

MATERIAL_TAGS = ["猪肉", "牛肉", "羊肉", "鸡肉", "鸭肉", "鱼虾", "蛋",
                 "豆制品", "菌菇", "蔬菜"]

MATERIAL_KEYWORD = [("鱼虾", "鱼虾"), ("鸡蛋", "蛋"), ("海鲜", "鱼虾"), ("鱼类", "鱼虾"),
                    ("豆制品", "豆制品"), ("菌菇", "菌菇"), ("蔬菜", "蔬菜"),
                    ("虾", "鱼虾"), ("鱼", "鱼虾"), ("蛋", "蛋"),
                    ("猪", "猪肉"), ("牛", "牛肉"), ("羊", "羊肉"),
                    ("鸡", "鸡肉"), ("鸭", "鸭肉")]

GENERIC_MEAT_KW = "肉"
GENERIC_MEAT_TAG = "荤菜"

def _s(x):
    if x is None:
        return ""
    return str(x).strip()

def _split_list(x):
    """把逗号/顿号/分号分隔的字符串（或数组）拆成干净列表"""
    if x is None:
        return []
    if isinstance(x, (list, tuple)):
        return [_s(v) for v in x if _s(v)]
    return [p.strip() for p in re.split(r"[,，、;；/]", str(x)) if p.strip()]

def _norm_campus(x):
    x = _s(x)
    if not x:
        return None
    if "鄠" in x:
        return "鄠邑校区"
    if "雁塔" in x or "本部" in x:
        return "雁塔校区"
    if "明德" in x:
        return "明德校区"
    return x

def _norm_canteen(x):
    x = _s(x)
    if not x:
        return None
    for k, al in CANTEEN_ALIAS.items():
        if x in al:
            return k
    for k in CANTEEN_ALIAS:
        if k in x:
            return k
    return None

def _norm_floor(x):
    x = _s(x)
    if not x:
        return None
    for k, al in FLOOR_ALIAS.items():
        if x in al:
            return k
    m = re.search(r"[一二三123]", x)
    if not m:
        return None
    return {"一": "一楼", "1": "一楼", "二": "二楼", "2": "二楼",
            "三": "三楼", "3": "三楼"}.get(m.group(0))

def _norm_budget(x):
    """'15' / 15 / '15元' / '15.5' → 15/16（None 透传）"""
    if x is None or _s(x) == "":
        return None
    try:
        return int(round(float(str(x).replace("元", "").replace("块", ""))))
    except Exception:
        m = re.search(r"\d+(\.\d+)?", str(x))
        return int(round(float(m.group(0)))) if m else None

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

def _norm_building(x):
    """'5' / '5号楼' / '五号楼' / 5 → int 楼号；解析不了返回 None"""
    s = _s(x)
    if not s:
        return None
    m = re.search(r"\d{1,3}", s)
    if m:
        return int(m.group(0))
    m = re.search(r"[一二两三四五六七八九十]{1,3}", s)
    if m:
        w = m.group(0)
        if w == "十":
            return 10
        if "十" in w:
            a, _, b = w.partition("十")
            return (_CN_NUM.get(a, 1) if a else 1) * 10 + (_CN_NUM.get(b, 0) if b else 0)
        return _CN_NUM.get(w)
    return None

def _norm_top_k(v):
    """数量意图：1–6 的整数；奇数向上取到偶数（3→4、5→6）。"""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    if 1 <= n <= 6:
        return n if n % 2 == 0 else n + 1
    return None

MODIFIERS = ('cheaper', 'more_spicy', 'lighter', 'quicker', 'random')

def _norm_modifier(v):
    """修饰语归一：白名单内有序去重；非法值丢弃（绝不因为模型多写词就崩）。"""
    if v is None:
        return []
    if isinstance(v, str):
        v = v.replace('，', ',').split(',')
    out = []
    for x in (v or []):
        t = str(x or '').strip().lower()
        if t in MODIFIERS and t not in out:
            out.append(t)
    return out

def normalize_slots(p):
    """纯逻辑核心：原始入参 dict → 归一化槽位 dict（本地自测与扣子共用）"""
    avoid = _split_list(p.get("avoid"))
    exclude = _split_list(p.get("exclude_tags"))
    mods = _norm_modifier(p.get("modifier"))          

    extra = []
    for a in avoid:
        hit_specific = False
        for tag in MATERIAL_TAGS:
            if tag in a:

                hit_specific = True
                if tag not in exclude and tag not in extra:
                    extra.append(tag)

        cands = [(len(kw), kw, tag) for kw, tag in MATERIAL_KEYWORD if kw in a]
        if cands:
            hit_specific = True
            best = max(c[0] for c in cands)
            for length, _kw, tag in cands:
                if length == best and tag not in exclude and tag not in extra:
                    extra.append(tag)

        # 2026-11：忌口词如果是食物/品类词（不要米饭 / 不吃稀饭 / 不要面），
        # 直接落成排除标签（词库 scan 负责把同义词一起排掉）
        _scan = lexicon.scan_query(a)
        for _t in _scan["tags"]:
            hit_specific = True
            if _t not in exclude and _t not in extra:
                extra.append(_t)
        _nt = lexicon.normalize_tag(a)
        if _nt in lexicon.TAG_DIM:
            hit_specific = True
            if _nt not in exclude and _nt not in extra:
                extra.append(_nt)

        if not hit_specific and GENERIC_MEAT_KW in a \
                and GENERIC_MEAT_TAG not in exclude and GENERIC_MEAT_TAG not in extra:
            extra.append(GENERIC_MEAT_TAG)

    for a in avoid:
        for tag in TASTE_EXCLUDE_TAGS:
            if tag in a and tag not in exclude and tag not in extra:
                extra.append(tag)

    avoid_allergen = []
    for a in avoid:
        for name, kws in ALLERGEN_KEYWORD:
            if any(k in a for k in kws) and name not in avoid_allergen:
                avoid_allergen.append(name)

    avoid_unmapped = []
    for a in avoid:
        hit = (any(tag in a for tag in MATERIAL_TAGS)
               or any(kw in a for kw, _ in MATERIAL_KEYWORD)
               or GENERIC_MEAT_KW in a
               or any(tag in a for tag in TASTE_EXCLUDE_TAGS)
               or any(any(k in a for k in kws) for _, kws in ALLERGEN_KEYWORD)
               or bool(lexicon.scan_query(a)["tags"])
               or lexicon.normalize_tag(a) in lexicon.TAG_DIM)
        if not hit:
            avoid_unmapped.append(a)
    campus_q = _norm_campus(p.get("campus"))

    bld_n = _norm_building(p.get("building"))
    building_pref = ("西食堂" if 1 <= bld_n <= 8 else "东食堂") if bld_n else None

    _tags = []
    for t in _split_list(p.get("tags")):
        t = lexicon.normalize_tag(TAG_SYNONYM.get(t, t))
        if t and t not in _tags:
            _tags.append(t)
    if '素食' in _tags and '素菜' in _tags:
        _tags = [t for t in _tags if t != '素菜']
    # 反义词（"不要这种反义关键词也要加上"）：查稀饭/粥 → 自动排除米饭。
    # 用户同时点名时才不排除（两个都在 _tags 里）。
    for _c in lexicon.apply_counter_tags(_tags):
        if _c not in exclude and _c not in extra:
            extra.append(_c)
    # 直接命中词（用户原话里点到的食物词，recommend 打分时优先菜名命中）
    terms = []
    for t in _split_list(p.get("terms")):
        t = lexicon.normalize_tag(t)
        if t and t not in terms:
            terms.append(t)
    food_terms = []
    for t in _split_list(p.get("food_terms")):
        t = lexicon.normalize_tag(t)
        if t and t not in food_terms:
            food_terms.append(t)
    return {
        "campus_q": campus_q,
        "campus_ok": campus_q in (None, "鄠邑校区"),
        "canteen_q": _norm_canteen(p.get("canteen")),
        "floor_q": _norm_floor(p.get("floor")),
        "shop_q": _s(p.get("shop")) or None,
        "budget": _norm_budget(p.get("budget")),
        "budget_lo": _norm_budget(p.get("budget_lo")),   
        "budget_around": bool(p.get("budget_around")),   
        "building": bld_n,
        "building_pref": building_pref,
        "tags": _tags,
        "terms": terms,
        "food_terms": food_terms,
        "exclude": exclude + extra,
        "avoid_raw": avoid,
        "avoid_allergen": avoid_allergen,
        "avoid_unmapped": avoid_unmapped,
        "kw": _split_list(p.get("keywords")),

        "top_k": _norm_top_k(p.get("top_k")),
        "cheap": bool(p.get("cheap")) or ('cheaper' in mods),

        "modifier": mods,

        "allergy_note": bool(avoid_allergen),
    }
