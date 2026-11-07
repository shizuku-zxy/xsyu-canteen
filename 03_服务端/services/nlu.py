
"""服务层 · 自然语言理解与话术（本地规则版）。

线上用大模型做"口语→槽位"和"结果→推荐语"，本地用规则/模板模拟，两者对外契约完全一致（见 03_服务端/README.md 的接口契约一节）。
"""
import re

from services import lexicon

MODIFIER_WORDS = [
    ('cheaper', r'再便宜|更便宜|便宜点|便宜些|实惠点|实惠些|别太贵|不要太贵|省点钱|省点|省钱|便宜'),
    ('more_spicy', r'再辣|更辣|加辣|辣一点|辣点|够味|重口'),
    ('lighter', r'清淡点|清淡些|少油|别太油|不油腻|养胃|轻一点|淡一点'),
    ('quicker', r'赶时间|快点|快一点|着急|马上|急'),
    ('random', r'随便|都行|看着来|随机|你定|听你的'),
]

FALLBACK_MATERIAL = [
    ('海鲜', '鱼虾'), ('鱼类', '鱼虾'), ('鱿鱼', '鱼虾'), ('鱼', '鱼虾'),
    ('虾', '鱼虾'), ('蟹', '鱼虾'), ('扇贝', '鱼虾'),
    ('鸡蛋', '蛋'), ('鸭蛋', '蛋'), ('鹌鹑蛋', '蛋'), ('蛋', '蛋'),
    ('鸭', '鸭肉'), ('羊', '羊肉'), ('猪', '猪肉'), ('排骨', '猪肉'),
    ('牛腩', '牛肉'), ('肥牛', '牛肉'), ('牛排', '牛肉'), ('牛', '牛肉'),
    ('鸡翅', '鸡肉'), ('鸡腿', '鸡肉'), ('鸡', '鸡肉'),
    ('豆腐', '豆制品'), ('菌', '菌菇'), ('蘑菇', '菌菇'),
]

FALLBACK_MATERIAL_KEEP = {
    '鱼': ('鱼香',),                    
    '牛': ('牛奶', '牛油果', '牛轧'),      
    '鸡': ('素鸡',),                    
    '羊': ('羊角',),                    
    '鸭': ('鸭梨',),
}

FALLBACK_TASTE = [('麻', ('麻酱', '麻花', '麻薯'))]
FALLBACK_DIET = [('减肥', '低卡'), ('减脂', '低卡'), ('控卡', '低卡'), ('瘦身', '低卡')]

def fallback_food_tags(text):
    """规则路径没抽到条件时的兜底：从原句里认食材/口味/减肥意图，返回标签列表。

    只应在本该"进窗口层"时调用（见 api_recommend），否则会把真正的开放问法
    （"有什么吃的没"）也变成筛菜。返回 [] 表示确实没有可识别的偏好。
    2026-11：先走 lexicon（馒头/稀饭/馍/米线/螺蛳粉…都能认），再兜底食材/口味。
    """
    t = text or ''
    out = []
    scan = lexicon.scan_query(t)
    for tag in scan["tags"]:
        if tag and tag not in out:
            out.append(tag)

    def _add(tag):
        if tag and tag not in out:
            out.append(tag)

    for kw, tag in FALLBACK_MATERIAL:
        if kw in t and not any(e in t for e in FALLBACK_MATERIAL_KEEP.get(kw, ())):
            _add(tag)
    for kw, bad in FALLBACK_TASTE:
        if kw in t and not any(b in t for b in bad):
            _add(kw)
    for kw, tag in FALLBACK_DIET:
        if kw in t:
            _add(tag)
    return out

TALK2TAG = [

    ("早上", "早餐"), ("早晨", "早餐"), ("早饭", "早餐"), ("早点", "早餐"), ("早餐", "早餐"),

    ("米线", "粉"), ("米粉", "粉"), ("粉条", "粉"), ("粉丝", "粉"),
    ("米饭", "米饭"), ("饭", "米饭"), ("面条", "面"), ("拉条子", "面"),
    ("馒头", "小吃饼类"), ("肉夹馍", "小吃饼类"), ("烧饼", "小吃饼类"), ("烙饼", "小吃饼类"),
    ("水饺", "水饺包子"), ("蒸饺", "水饺包子"), ("煎饺", "水饺包子"),
    ("馄饨", "水饺包子"), ("云吞", "水饺包子"), ("饺子", "水饺包子"), ("包子", "水饺包子"),
    ("稀饭", "粥汤"), ("粥", "粥汤"), ("汤", "粥汤"),
    ("盖饭", "米饭"), ("盖浇饭", "米饭"), ("盒饭", "快餐"),
    ("套餐", "快餐"), ("米线粉", "粉"),
    ("麻辣烫", "麻辣烫香锅"), ("香锅", "麻辣烫香锅"), ("冒菜", "麻辣烫香锅"),
    ("盖浇饭", "米饭"), ("米饭", "米饭"), ("快餐", "快餐"),
    ("刀削面", "面"), ("拉面", "面"), ("拌面", "面"), ("面食", "面"), ("面", "面"),
    ("汉堡", "汉堡披萨"), ("披萨", "汉堡披萨"),
    ("奶茶", "饮品"), ("甜品", "甜品"), ("饮品", "饮品"), ("饮料", "饮品"),
    ("喝的", "饮品"), ("喝的东西", "饮品"), ("喝点东西", "饮品"), ("想喝点东西", "饮品"),
    ("轻食", "轻食"),
    ("小吃", "小吃饼类"), ("饼", "小吃饼类"),
    ("清淡", "清淡"), ("辣", "辣"), ("酸", "酸"), ("甜", "甜"), ("鲜味", "鲜"), ("咸味", "咸"), ("鲜的", "鲜"), ("咸的", "咸"), ("咸口", "咸"),
    ("素食", "素食"), ("蔬菜", "蔬菜"), ("低卡", "低卡"),
    ("牛肉", "牛肉"), ("羊肉", "羊肉"), ("鸡肉", "鸡肉"), ("鱼虾", "鱼虾"),
    ("虾", "鱼虾"), ("海鲜", "鱼虾"), ("鱼类", "鱼虾"), ("鱿鱼", "鱼虾"), ("蟹", "鱼虾"), ("花甲", "鱼虾"),

    ("肉菜", "荤菜"), ("荤菜", "荤菜"), ("吃肉", "荤菜"), ("肉", "荤菜"),
    ("素菜", "素食"), ("吃素", "素食"), ("吃草", "素食"), ("素", "素食"),
    ("吃饭", "米饭"), ("来碗饭", "米饭"),
]

def _segment_tag_phrase(text):
    """整句是否只是"食物词 + 语气词"拼成（例如 想喝稀饭 / 来碗牛肉面 / 想吃馒头）。

    2026-11：改用 services/lexicon 的词表判断——它能认「稀饭/馍/馒头/米线/螺蛳粉…」
    这些旧 TALK2TAG 认不全的词，也能把「想喝/来碗/一份」这类语气词清掉。
    能完全拼出来 → 交给标签解析（不要当菜名关键词）；拼不出来 → 才当菜名关键词。
    """
    s = lexicon.clean_mood(text or "")
    if not s:
        return True
    scan = lexicon.scan_query(s)
    return not lexicon.leftover_keywords(scan["leftover"])

CATEGORY_WORDS = {"面", "饭", "米饭", "白饭", "大米饭", "粉", "粥", "饺子", "包子", "粥汤", "面条", "米线", "米粉",
                  "馒头", "馍", "水饺", "蒸饺", "馄饨", "稀饭", "汤", "盖饭", "套餐", "快餐",
                  "早餐", "汉堡", "披萨", "奶茶", "甜品", "饮品", "饮料", "炒菜", "荤菜", "素菜",
                  # 2026-11 细分标签（问句里出现这些词时走「标签」而不是「菜名关键词」）
                  "炒饭", "盖浇饭", "拌饭", "饭团", "拌面", "干拌面", "汤面", "炒面", "焖面",
                  "凉面", "臊子面", "刀削面", "拉面", "烩面", "麻食", "意面", "方便面", "泡面",
                  "米线", "米粉", "螺蛳粉", "酸辣粉", "渔粉", "五谷渔粉", "粉丝", "粉条", "土豆粉", "河粉",
                  "花卷", "肉花卷", "灌汤包", "小笼包", "云吞", "抄手", "烧麦", "烧卖", "饼", "烧饼",
                  "手抓饼", "馅饼", "煎饼", "夹馍", "肉夹馍", "锅盔", "煮馍", "泡馍", "凉皮",
                  "擀面皮", "米皮", "汉堡", "炸鸡", "薯条", "烤肠", "麻辣烫", "冒菜", "香锅", "麻辣拌",
                  "沙拉", "豆浆", "油条", "煎蛋", "卤蛋", "茶叶蛋", "奶茶", "咖啡", "果汁", "汽水",
                  "茶饮", "蛋糕", "蛋挞", "布丁", "双皮奶", "冰淇淋", "酸奶", "水果捞", "奶昔",
                  "砂锅", "年糕", "肠粉", "胡辣汤", "豆腐脑", "菜盒", "麻团", "寿司"}

_BUD_NUM = r"(?:[0-9]{1,3}(?:\.[0-9])?|[一二两三四五六七八九十]{1,4})"

_BUD_RE = re.compile(
    r"(?P<pre>大概|大约|约)?\s*(?P<num>[0-9]{1,3}(?:\.[0-9])?|[一二两三四五六七八九十]{1,4})"
    r"\s*(?:块|元)(?:钱)?\s*(?P<suf>左右|上下|以内|以下|内)?")

_BUD_RE2 = re.compile(
    r"(?P<num>[0-9]{1,3}|[一二两三四五六七八九十]{1,4})\s*(?P<suf>左右|上下)")

_BUD_RANGE_RE = re.compile(
    r"(?P<lo>[0-9]{1,3}(?:\.[0-9])?|[一二两三四五六七八九十]{1,4})\s*"
    r"(?:到|至|~|～|－|-|—)\s*"
    r"(?P<hi>[0-9]{1,3}(?:\.[0-9])?|[一二两三四五六七八九十]{1,4})\s*(?:块|元)(?:钱)?")
_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9}

def _cn_num(s):
    """中文数字/阿拉伯数字 → int（十=10，十五=15，二十=20，二十五=25）；解析不了返回 None"""
    s = (s or "").strip()
    if s.isdigit():
        try:
            return int(s)
        except Exception:
            return None
    if s == "十":
        return 10
    if "十" in s:
        a, _, b = s.partition("十")
        hi = _CN_NUM.get(a, 1) if a else 1
        lo = _CN_NUM.get(b, 0) if b else 0
        return hi * 10 + lo
    return _CN_NUM.get(s)
MOOD_WORDS = ["有什么好吃的", "中午吃什么", "吃什么好", "吃什么", "有没有", "有什么",
              "推荐", "我想吃", "想吃", "来一份", "来碗", "来份", "有吗", "呢", "吗",
              "吧", "一点", "些", "的", "。", "？", "，", "！", ",", "?", "!", " "]

_AV_BODY = r"([^，。、,;；！!？?\s不别忌]{1,4})"

AVOID_PATTERNS = [

    r"(?:不想吃|不要|不吃|不想|别(?:推荐|来|给|上|吃)|不来|忌口|忌)" + _AV_BODY,
    r"(?:对)?" + _AV_BODY + r"过敏",
]

AVOID_TAIL_STRIP = r"[的有呢吗哈了吧啊的了什么点儿些]+$"

_CN_NUM = {'一': 1, '两': 2, '二': 2, '三': 3, '四': 4, '五': 5,
           '1': 1, '2': 2, '3': 3, '4': 4, '5': 5}
_TOPK_RE = re.compile(r'(?:推荐|来|要|给我|帮我|挑|选|出)?\s*([一两二三四五1-5])\s*'
                      r'(?:个|道|份|种|样|家)?\s*(窗口|档口|店面|店|菜|菜品)')
_TOPK_VAGUE_RE = re.compile(r'(?:推荐|来|要|给我|帮我|挑|选)\s*(?:几|多少)\s*(?:个|道|份|种|样|家)?')

_TOPK_BARE_RE = re.compile(r'(?:推荐|来|要|给我|帮我|挑|选|整|搞|弄)\s*'
                           r'(?:个|道|份|点|些|一点|一些)?\s*(菜|菜品|窗口|档口|饭|吃的|东西)')

def extract_top_k_unit(text):
    """数量句里的**单位**：要"菜"→dish，要"窗口/档口"→window，含糊→''。

    2026-10-15：光有数量不够，还得知道他要的是"窗口"还是"菜"——
      「推荐三道菜」= 我要 3 道菜（走后厨菜品层）
      「推荐一个窗口」= 我要 1 个档口（走窗口层）
    规则判定（不依赖 LLM），所以没 key 时也准。
    """
    if not text:
        return ''
    m = _TOPK_RE.search(text)
    if m:
        return 'dish' if m.group(2) in ('菜', '菜品') else 'window'
    m = _TOPK_BARE_RE.search(text)
    if m:
        return 'dish' if m.group(1) in ('菜', '菜品', '饭', '吃的', '东西') else 'window'

    m = _TOPK_VAGUE_RE.search(text)
    if m:
        tail = text[m.end():m.end() + 4]
        if tail[:1] in ('菜',) or tail[:2] == '菜品':
            return 'dish'
        if tail[:2] in ('窗口', '档口'):
            return 'window'
    return ''

def extract_top_k(text):
    """从问句里抽「要几个」（1–6，窗口默认偶数）。没有明确数量返回 None。"""
    if not text:
        return None
    m = _TOPK_RE.search(text)
    if m:
        return _CN_NUM.get(m.group(1))
    if _TOPK_VAGUE_RE.search(text):
        return 6                      
    if _TOPK_BARE_RE.search(text):
        return 4                      
    return None

GENERIC_PHRASE_RE = re.compile(
    r"(今天|今儿|咱们?|我)?(早上|中午|下午|晚上|夜里|现在|这会儿)?"
    r"(?:想吃点什么|吃点什么|想吃什么|吃什么|吃啥|有什么吃的|有啥吃的|有没有饭|还有饭吗|有饭吗|"
    r"推荐点好吃的|推荐个好吃的|推荐个饭|推荐一下|随便推荐|推荐|随便来点|随便|饿了|我饿了|好饿|"
    r"饿死了|馋了|来点好吃的|来份饭|来个饭)+"
    r"[呗吧呀啊哈嗯哦呢~～?？!！的]*"
)

def is_generic(text):
    """原句是否纯泛指/寒暄（"今天中午吃什么/hhh/随便推荐"）。
    2026-10-12 新增：供 LLM 兜底触发判定——泛指句走开放式推荐即可，不值得调模型。"""
    t = re.sub(r"[\s，。,、！!？?～~…]", "", text).lower()
    if not t:
        return True
    if re.fullmatch(r"[a-z]+", t):
        return True
    return bool(GENERIC_PHRASE_RE.fullmatch(t))

SHOP_DICT = None
SHOP_BLACKLIST = {"饺子"}

def _load_shop_dict():
    global SHOP_DICT
    if SHOP_DICT is None:
        try:
            from dao import db
            names = {r["shop"] for r in db.query("SELECT shop FROM shop")} - SHOP_BLACKLIST
            SHOP_DICT = tuple(sorted(names, key=len, reverse=True))
        except Exception:
            SHOP_DICT = ()
    return SHOP_DICT


# 否定词与目标之间可能夹着“的/面/饭”等；只把目标移入 avoid，保留后面品类词。
_NEG_HEAD = (r"(?:不想吃|不要吃|不要喝|不想要|不要|不吃|不喝|不想|"
             r"别(?:推荐|来|给|上|吃|放|加)?|不来|忌口|忌|"
             r"(?<!有)没有|不含|不加|不能吃|不可以吃|受不了|拒绝)")
_NEG_RE = re.compile(_NEG_HEAD + r"([^，。、,;；！!？?\s]{1,10})")
_ALLERGY_RE = re.compile(r"(?:对)?([^，。、,;；！!？?\s]{1,10})过敏")
_NEG_CATEGORIES = ("米饭", "快餐", "面", "粉", "水饺包子", "麻辣烫香锅", "汉堡披萨",
                   "粥汤", "小吃饼类", "甜品", "饮品", "轻食", "面条", "米线",
                   "盖饭", "饭", "汤", "白饭", "大米饭", "面食", "拉条子", "米粉",
                   "河粉", "粉条", "粉丝", "饺子", "馄饨", "云吞", "抄手", "包子",
                   "蒸饺", "汉堡", "披萨", "粥", "稀饭", "馍", "饼", "肉夹馍",
                   "奶茶", "甜品", "饮料")
_NEG_CAT_CANON = {"米饭": "米饭", "白饭": "米饭", "大米饭": "米饭",
                  "饭": "米饭", "盖饭": "米饭", "盒饭": "快餐", "套餐": "快餐", "快餐": "快餐",
                  "面条": "面", "面食": "面", "拉条子": "面",
                  "米线": "粉", "米粉": "粉", "河粉": "粉", "粉条": "粉", "粉丝": "粉",
                  "饺子": "水饺包子", "馄饨": "水饺包子", "云吞": "水饺包子",
                  "抄手": "水饺包子", "包子": "水饺包子", "蒸饺": "水饺包子",
                  "汉堡": "汉堡披萨", "披萨": "汉堡披萨",
                  "粥": "粥汤", "汤": "粥汤", "稀饭": "粥汤",
                  "馍": "小吃饼类", "饼": "小吃饼类", "肉夹馍": "小吃饼类",
                  "奶茶": "饮品", "甜品": "甜品", "饮品": "饮品", "饮料": "饮品"}

def _split_neg_body(body):
    """把“辣的面/辣面/猪肉的面”拆成（要排除的目标, 留下的品类词）。"""
    body = re.sub(r"(?:的)+$", "", body or "").strip()
    if not body:
        return "", ""
    if "的" in body:
        target, rest = body.split("的", 1)
        return target.strip(), rest.strip()
    if body in _NEG_CATEGORIES:
        return body, ""
    for suf in sorted(_NEG_CATEGORIES, key=len, reverse=True):
        if body.endswith(suf) and len(body) > len(suf):
            return body[:-len(suf)].strip(), suf
    return body.strip(), ""

def _extract_negations(text):
    """返回 (avoid 列表, 移除否定词后的文本, 品类排除列表)。"""
    avoids, cats = [], []
    out = []
    last = 0
    for m in _NEG_RE.finditer(text or ""):
        out.append(text[last:m.start()])
        target, rest = _split_neg_body(m.group(1))
        if target:
            if target in ("太贵", "贵", "贵了", "太油", "油", "油腻", "太油了"):
                pass
            elif target in _NEG_CATEGORIES:
                canon = _NEG_CAT_CANON.get(target, target)
                if canon not in cats:
                    cats.append(canon)
            elif target not in avoids:
                avoids.append(target)
        out.append(rest)
        last = m.end()
    out.append((text or "")[last:])
    t = "".join(out)
    # “对花生过敏”只保留过敏目标；不把它当正向标签。
    for m in _ALLERGY_RE.finditer(t):
        target = re.sub(r"[的]+$", "", m.group(1)).strip()
        if target and target not in avoids:
            avoids.append(target)
    t = _ALLERGY_RE.sub("", t)
    return avoids, t, cats

def parse_question(text):
    """演示用槽位抽取（规则版）。扣子真实链路由入口大模型完成，泛化能力更强；
    此函数只需覆盖演示问法，不追求生产级。
    2026-10-12 起：规则「没把握」的句子由 api_recommend 层转 LLM 兜底（services/llm_fallback.py）。"""
    t = text
    p = {"campus": "", "canteen": "", "floor": "", "shop": "", "budget": "",
         "tags": "", "exclude_tags": "", "avoid": "", "keywords": "", "cheap": "", "top_k": "",
         "building": "", "budget_around": "", "terms": "", "food_terms": ""}

    _alias = []
    for name in _load_shop_dict():
        _alias.append((name, name))
        short = name.split('·')[0].split('（')[0].split('(')[0].strip()
        if short and short != name:
            _alias.append((short, name))
    _SHOP_Q = ("有什么", "有啥", "哪些", "推荐", "看看", "在哪", "多少钱", "几点", "怎么样", "咋样", "菜单")
    for a, name in sorted(_alias, key=lambda x: -len(x[0])):
        if a not in t:
            continue

        if len(a) < 4 and not any(k in t for k in _SHOP_Q):
            continue
        p["shop"] = name
        t = t.replace(a, "", 1)
        break

    p["top_k"] = extract_top_k(t) or ""

    name_like = re.sub(r"[?？。！，,]", "", t)

    for w in ["有没有", "有吗", "有什么", "有啥", "咋样", "怎么样", "哪些", "什么", "现在", "还有",
              "来一份", "来一碗", "来一碗", "来一块", "来一杯", "来一份", "来碗", "来杯", "来份", "来点", "来个",
              "整点", "搞点", "喝碗", "喝点", "喝杯", "给我", "帮我", "想吃点", "想喝点", "吃点", "喝点",
              "要个", "要份", "要碗", "我想吃", "我想喝", "想吃", "想喝", "想要", "想", "推荐", "在",
              "有", "吗", "呢", "啥", "一点", "一些", "点儿", "些", "一份", "一碗", "一杯", "一块",
              "这", "那", "的", "了", "呗", "吧", "啊", "呀", "哦", "嗯", "哈", "哎", "给我来", "帮我推荐",
              "喝", "吃", "来", "要", "我", "你", "咱们", "我们", "今天", "中午", "晚上", "早上", "现在"]:
        name_like = name_like.replace(w, "")
    name_like = name_like.strip()

    if len(name_like) > 2 and name_like.endswith("菜") and name_like[:-1] in CATEGORY_WORDS:
        name_like = name_like[:-1]
    if re.fullmatch(r"碗[面饭粉粥汤]", name_like):
        name_like = name_like[1:]
    if (1 < len(name_like) <= 10 and "的" not in name_like
            and name_like not in CATEGORY_WORDS          
            and not any(x in name_like for x in ["什么", "食堂", "楼", "块", "元", "校区", "区",
                                                 "早上", "我", "不", "没", "想", "别", "要", "请",

                                                 "过敏", "忌", "不吃", "不要", "素"])
            and re.search(r"[馍饭菜面饺汤包堡粉粥]", name_like)):

        if re.fullmatch(r"(饿了|饿|馋了|馋|好饿|饿死|好想吃)*(个|点|份|些)*(饭|吃的|东西|食物|好吃的)*[呗吧呀啊了的]*",
                        name_like):
            return p

        if extract_top_k(t) is not None:
            return p
        if _segment_tag_phrase(name_like):
            pass
        else:
            p["keywords"] = name_like
            return p

    m = re.search(r"([0-9]{1,3}|[一二两三四五六七八九十]{1,4})\s*号(?:宿舍)?楼", t)
    if m:
        n = _cn_num(m.group(1))
        if n:
            p["building"] = str(n)
        t = t.replace(m.group(0), "", 1)

    if "雁塔" in t or "本部" in t:
        p["campus"] = "雁塔校区"
        t = t.replace("雁塔校区", "").replace("雁塔", "").replace("本部", "")
    elif "明德" in t:
        p["campus"] = "明德校区"
        t = t.replace("明德校区", "").replace("明德", "")
    elif "鄠" in t:
        p["campus"] = "鄠邑校区"
        t = re.sub(r"鄠[邑]*校区?", "", t)

    _CANTEEN_WORDS = (
        ("小吃街", "小吃街"), ("科技街", "小吃街"),
        ("东食堂", "东食堂"), ("东餐厅", "东食堂"),
        ("西食堂", "西食堂"), ("西餐厅", "西食堂"),
        ("第二食堂", "东食堂"), ("二号食堂", "东食堂"), ("二食堂", "东食堂"), ("新食堂", "东食堂"),
        ("南食堂", "东食堂"), ("南餐厅", "东食堂"),
        ("第一食堂", "西食堂"), ("一号食堂", "西食堂"), ("一食堂", "西食堂"), ("老食堂", "西食堂"),
    )
    for _w, _c in _CANTEEN_WORDS:
        if _w in t:
            p["canteen"] = _c
            t = t.replace(_w, "")
            break
    else:
        for c in ("东", "西"):
            m_side = re.search(c + r"(?:区|侧|边)", t)
            if m_side:
                p["canteen"] = c
                t = t.replace(m_side.group(0), "", 1)
                break
    m = re.search(r"[一二三1-3]\s*楼层?", t)
    if m:
        p["floor"] = m.group(0).replace("层", "楼")
        t = t.replace(m.group(0), "")

    _range_hit = False
    _rg = _BUD_RANGE_RE.search(t)
    if _rg:
        _lo, _hi = _cn_num(_rg.group("lo")), _cn_num(_rg.group("hi"))
        if _lo is not None and _hi is not None and 0 < _lo <= _hi:
            p["budget"] = str(_hi)
            p["budget_lo"] = str(_lo)
            p["budget_range"] = "1"
            t = t.replace(_rg.group(0), "", 1)
            _range_hit = True
    m = None if _range_hit else (_BUD_RE.search(t) or _BUD_RE2.search(t))
    if m:
        n = _cn_num(m.group("num"))

        if n is not None:
            p["budget"] = str(n)
            pre = (m.group("pre") or "")
            suf = (m.group("suf") or "")
            if suf in ("左右", "上下") or pre:
                p["budget_around"] = "1"
        t = t.replace(m.group(0), "", 1)

    avoids, t, neg_cats = _extract_negations(t)
    if avoids:
        p["avoid"] = ",".join(avoids)
    if neg_cats:
        p["exclude_tags"] = ",".join(neg_cats)

    neg_tastes = re.findall(r"不(辣|甜|酸|咸)", t)
    if neg_tastes:
        for w in set(neg_tastes):
            t = re.sub(r"不%s[的个]?[点些]?" % w, "", t)
        p["exclude_tags"] = ",".join(dict.fromkeys(neg_tastes))

    if re.search(r"便宜|实惠|低价|省钱|省点|省着点|不要太贵|别太贵|太贵", text):
        p["cheap"] = "1"

    _mods = [name for name, pat in MODIFIER_WORDS if re.search(pat, text)]
    if _mods:
        p["modifier"] = ",".join(_mods)

    # —— 2026-11：食物词 → 标签/直接命中词（lexicon 统一词表，最长匹配优先）——
    tags, terms, food_terms, kws = [], [], [], []
    scan = lexicon.scan_query(t)
    for x in scan["tags"]:
        if x not in tags:
            tags.append(x)
    for x in scan["terms"]:
        if x not in terms:
            terms.append(x)
    for x in scan["food_terms"]:
        if x not in food_terms:
            food_terms.append(x)
    for x in scan["keywords"]:
        if x not in kws:
            kws.append(x)
    _left = scan["leftover"]

    # 旧 TALK2TAG 兜底（词库覆盖不到的说法）；按词长从长到短，防「饭」抢「稀饭」
    for kw, tag in sorted(TALK2TAG, key=lambda x: -len(x[0])):
        if kw and kw in _left:
            tag = lexicon.normalize_tag(tag)
            if tag not in tags:
                tags.append(tag)
            _left = _left.replace(kw, "")

    # 剩下像菜名的片段（如「宫保」）→ 按菜名直接找
    if not kws:
        lk = lexicon.leftover_keywords(_left)
        if lk:
            kws = lk
    if tags:
        p["tags"] = ",".join(tags)
    if terms:
        p["terms"] = ",".join(terms)
    if food_terms:
        p["food_terms"] = ",".join(food_terms)
    if kws:
        p["keywords"] = ",".join(kws)
    return p

def _fix_staple_slots(slots, text):
    """2026-10-26 用户要求：问“主食”时，馒头（小吃饼类）也要算主食。

    LLM 对“主食”可能只给 米饭/面/水饺包子，漏掉 小吃饼类；且“主食”二字
    可能被当成菜名关键词去搜（库里没有菜名含“主食”）→ 0 结果。
    这里做确定性归一：去掉关键词里的“主食”，并补齐 5 个主食品类。
    """
    if not text or '主食' not in text:
        return slots

    if re.search(r'便宜|实惠|低价|省钱', text):
        slots['cheap'] = '1'
    kw = (slots.get('keywords') or '').replace('主食', '').strip(' ，,、')
    if kw:
        slots['keywords'] = kw
    else:
        slots.pop('keywords', None)
    tags = [t for t in re.split(r'[,，、;；]', slots.get('tags') or '') if t]
    for t in ('主食', '米饭', '快餐', '面', '粉', '水饺包子', '小吃饼类'):
        if t not in tags:
            tags.append(t)
    slots['tags'] = ','.join(tags)
    return slots

def parse_question_best(text):
    """返回 (槽位 dict, meta)。优先 LLM（若已配置 api_key），任何异常/未配置 → 规则版。

    meta: {'source': 'llm'|'rules', 'llm_ms': int, 'unmatched_tags': [...], 'error': str}
    """
    meta = {'source': 'rules', 'llm_ms': 0, 'unmatched_tags': [], 'error': ''}
    try:
        from services import llm
        if llm.available():
            from services import nlu_llm
            raw, m = nlu_llm.parse(text)
            if raw.get('tags') or raw.get('exclude_tags') or raw.get('canteen') or raw.get('budget') \
                    or raw.get('keywords') or raw.get('avoid') or raw.get('floor') or raw.get('shop'):
                raw.setdefault('campus', '')
                return _fix_staple_slots(raw, text), m

            rule = parse_question(text)
            for k, v in raw.items():
                if v and not rule.get(k):
                    rule[k] = v
            m['source'] = 'llm'
            return _fix_staple_slots(rule, text), m
    except Exception as e:                                   
        meta['error'] = '%s: %s' % (type(e).__name__, str(e)[:120])
    rule = parse_question(text)
    return _fix_staple_slots(rule, text), meta

def compose_reply(out):
    if not out["has_result"]:
        return out["reason"]
    lines = out["display_text"].split("\n")
    shown = lines[:5]
    head = "符合条件的菜一共 %d 道（%s），给你挑前几名：" % (out["total_count"], out["slots_text"])
    body = "\n".join("%d. %s" % (i, ln) for i, ln in enumerate(shown, 1))
    more = "" if len(lines) <= 5 else "\n……（后面还有，需要看更多跟我说）"
    notes = []
    if out.get("llm_note"):
        notes.append("· " + out["llm_note"])
    if out["relax_note"]:
        notes.append("· " + out["relax_note"])
    if out["kw_note"]:
        notes.append("· " + out["kw_note"])

    if out.get("avoid_note"):
        notes.append("· " + out["avoid_note"])
    if out.get("budget_hint"):
        notes.append("· " + out["budget_hint"])

    if out.get("loc_note"):
        notes.append("· " + out["loc_note"])
    if out.get("breakfast_note"):
        notes.append("· " + out["breakfast_note"])

    if out.get("profile_note"):
        notes.append("· " + out["profile_note"])
    if out["allergy_note"]:
        notes.append("· 你提到过敏，以上结果已严格排除对应食材；档口现做的原料，建议再跟师傅口头确认一下。")
    notes.append("祝你今天干饭愉快～")
    return head + "\n" + body + more + ("\n" + "\n".join(notes) if notes else "")
