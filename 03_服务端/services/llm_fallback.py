
"""LLM 理解主引擎（2026-10-12 v2，从兜底升级为主路径）。

架构定位（用户拍板）：
    学生口语（长句/复合句/随意说） ──LLM理解──▶ 模版化勾选后端标签（槽位JSON）
    ──normalize_slots归一──▶ 本地规则引擎推荐（过滤/打分/放宽/安全门）
    LLM 只负责"听懂人话"，不参与挑菜——八条承诺不变量全部保留在本地引擎。

默认对接豆包（火山方舟 OpenAI 兼容协议，每个模型自带 50 万 token 免费额度）：
    LLM_API_KEY   火山方舟 API Key（https://console.volcengine.com/ark/region:ark+cn-beijing/apikey）
    LLM_BASE_URL  默认 https://ark.cn-beijing.volces.com/api/v3
    LLM_MODEL     默认 doubao-1-5-lite-32k-250115（lite 版槽位抽取足够，免费额度内演示随便跑）
    换 DeepSeek/Kimi/Ollama 只需改这三个变量（OpenAI 兼容通用）。
未配置 key → available()=False，服务端全自动回落规则 NLU，演示零依赖可跑。
"""
import io
import json
import os
import re
import urllib.request

from services import slots as _slots
from runtime_env import llm_config_path

_DEFAULT_BASE = "https://ark.cn-beijing.volces.com/api/v3"
_DEFAULT_MODEL = "doubao-1-5-lite-32k-250115"

def _load_cfg():
    cfg = {'enabled': False, 'api_key': '', 'base_url': _DEFAULT_BASE,
           'model': _DEFAULT_MODEL, 'timeout': 8}
    try:
        path = llm_config_path()
        if os.path.exists(path):
            cfg.update(json.load(io.open(path, encoding='utf-8')) or {})
    except Exception:
        pass

    for env in ('LLM_API_KEY', 'CANTEEN_LLM_API_KEY', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY'):
        if os.environ.get(env):
            cfg['api_key'] = os.environ[env]
            cfg['enabled'] = True
            break
    cfg['base_url'] = (os.environ.get('LLM_BASE_URL') or os.environ.get('CANTEEN_LLM_BASE_URL')
                       or cfg.get('base_url') or _DEFAULT_BASE).rstrip('/')
    cfg['model'] = (os.environ.get('LLM_MODEL') or os.environ.get('CANTEEN_LLM_MODEL')
                    or cfg.get('model') or _DEFAULT_MODEL)
    return cfg

_CFG = _load_cfg()
BASE_URL = _CFG['base_url']
MODEL = _CFG['model']
TIMEOUT = int(_CFG.get('timeout') or 8)
API_KEY = _CFG['api_key'] if _CFG.get('enabled') else ''


def reload_config():
    """前端保存/清除 Key 后重新读取配置，避免模块级旧配置生效。"""
    global _CFG, BASE_URL, MODEL, TIMEOUT, API_KEY
    _CFG = _load_cfg()
    BASE_URL = _CFG['base_url']
    MODEL = _CFG['model']
    TIMEOUT = int(_CFG.get('timeout') or 8)
    API_KEY = _CFG['api_key'] if _CFG.get('enabled') else ''

_LABELS = [('doubao', '豆包'), ('deepseek', 'DeepSeek'), ('qwen', '通义千问'), ('glm', '智谱GLM'),
           ('moonshot', 'Kimi'), ('gpt', 'GPT'), ('claude', 'Claude'), ('ernie', '文心')]

def model_label():
    m = (MODEL or '').lower()
    for k, v in _LABELS:
        if k in m:
            return v
    return MODEL or 'AI'

MAX_Q_LEN = 200      

_FALLBACK_TAGS = ("辣 麻 酸 甜 清淡 面 粉 米饭 快餐 水饺包子 麻辣烫香锅 汉堡披萨 "
                  "粥汤 小吃饼类 甜品 饮品 轻食 早餐 素食 低卡 荤菜 素菜 自选 "
                  "猪肉 牛肉 羊肉 鸡肉 鸭肉 鱼虾 蛋 蔬菜 豆制品 菌菇 "
                  "炒 蒸 炸 煎烤 卤酱 凉拌 焖炖")

def _load_tag_vocab():
    try:
        from dao import db                                    
        rows = db.query("SELECT tag FROM tag WHERE dimension <> '预算' ORDER BY tag_id")
        tags = [r['tag'] for r in rows if r.get('tag')]
        if tags:
            return ' '.join(tags)
    except Exception:                                         
        pass
    return _FALLBACK_TAGS

TAG_VOCAB = _load_tag_vocab()
EXCLUDE_VOCAB = TAG_VOCAB          
AVOID_VOCAB = "猪肉 鸡肉 牛肉 羊肉 鱼虾 虾 蟹 蛋 鸡蛋 牛奶 奶 大豆 豆腐 花生 坚果 麸质 面筋 香菜 葱 姜 蒜"

PROMPT = """你是大学食堂推荐系统的需求解析器。把学生的话解析成严格 JSON（不要输出任何其他文字）。

【只能从下列词表勾选，禁止发明词表外的词】
- 想吃标签(tags)：%s
- 不要的口味/食材(exclude_tags)：%s
- 忌口/过敏(avoid)：%s

【字段】
campus: 鄠邑校区/雁塔校区；canteen: 东食堂/西食堂/小吃街；floor: 一楼/二楼/三楼（小吃街没有楼层，留空）；
shop: 档口名（没提就空）；budget: 数字（元，"十块左右"填10）；building: 宿舍楼号数字（"我在5号楼"填5；没提就空）；
budget_around: 布尔。预算是"左右/上下/大概/差不多"这类**近似**语义填 true；"以内/以下/不超过/刚好"填 false；
keywords: 具体菜名（只在明确点了某道菜时填）。
confidence: 0~1 的数字，表示你对**本次整体理解**的把握（含糊/多义就填小于 0.7）。
evidence: 对象。给你勾选的每个标签，填**用户原话里对应的那一段原文**——必须逐字摘抄，
  不许改写、不许补字；没把握的标签就不要填。例：用户说"十块以内想吃点热乎的面"，
  可填 {"面":"面","粥汤":"热乎"}。没有把握就整个留空 {}。
top_k: 数字 1–5。用户**想要几个**时填："推荐一个窗口"→1；"来两道菜"→2；"推荐几个"→3；
      **口语常省略数词：「推荐道菜 / 来道菜 / 推荐个菜 / 整点菜 / 来点吃的」都是 1，也要填 1**。
      **完全没提数量**（"有什么吃的""今天吃什么"）才留空。
       注意：它表示"要几个"，**不是菜名**，绝不能把"一道菜就行""三道菜"写进 keywords。
scope: 推荐层级，**只能填 window / dish / shop 三者之一**：
  · window —— 用户在问"有什么吃的/今天吃什么/有什么好吃的/去哪吃"这类
              **没有任何具体菜品偏好**的开放式问法（他要的是"先去哪个档口"）
  · dish   —— 用户表达了具体偏好（想吃面 / 要清淡的 / 10块以内 / 不要辣 / 想减肥…）
  · shop   —— 用户点名了某个档口（"天下好面有什么菜"）
only_window: 布尔。用户**明确只要窗口/档口/店，不要具体菜品**时填 true，例如
  "只推荐窗口""别给我列菜，就告诉我去哪家""只看档口""不要菜，只要店"；
  普通开放式问法（"有什么吃的""今天吃什么"）仍填 false，不要误判。
intent: 意图，**只能填 chat / food 二者之一**（2026-10-13 新增，由你判断该不该走闲聊）：
  · chat —— 用户**不是在找吃的**：问你是谁/你叫什么/你会什么、让介绍学校或食堂、
            问几点开门、寒暄（你好/谢谢/再见）、问与吃饭无关的事（天气/心情）。
            这类只需聊天作答，**不要**给窗口或菜品推荐。
  · food —— 其余一律 food：找吃的、问某食堂/楼层/档口有什么、问菜品、报预算口味忌口，
            包括"食堂都在哪""食堂几点开"这种**问食堂信息**的（也算校园生活，可走聊天或推荐，
            由你判断：问了就填 chat 只有当他在闲聊，不是想让你推荐菜）。
modifier: **相对当前结果的"再调整"**，只能从下面 5 个里选（可多选，没有就 []）：
  · cheaper   —— "更便宜点/便宜些/实惠点/别太贵"
  · more_spicy —— "再辣点/加辣/辣一点/够味"
  · lighter   —— "清淡点/少油/不要太重口/养胃"
  · quicker   —— "快点/赶时间/马上就吃/简单吃点"
  · random    —— "随便/都行/看着来/随机"（明确表示不想挑）
  ⚠️ 它们是"调偏好"，**不是标签**：别把 "更便宜" 塞进 tags，也别塞进 exclude_tags。

【规则】
1. 否定句（不要X/不吃X/别放X/对X过敏）一律进 exclude_tags 或 avoid，绝不进 tags；
2. 品类词（面/饺子/米粉/麻辣烫）进 tags 不进 keywords；
3. 长句里的每个条件都要抽全，别遗漏后半句；
4. 数量：用户说了"一个/一道/一份/两个/三道/几个"这类**数量**，填 top_k（1–5）；
   **我省略数词、直接要菜**的口语（"推荐道菜 / 来道菜 / 推荐个菜 / 整点菜 / 来点吃的"）也算 1；
   ⚠️ **但只是表达想吃什么不算数量**："想吃面""想吃清淡的""10块以内的面""有什么面"
   都**必须留空 top_k**（12 元是价格不是数量）。判据：原句里得有个"数 + 量词"。完全没提就留空。
   数量词（个/道/份/点）**绝不是菜名**，一个都不能写进 keywords —— 把"道菜""个菜""来道菜"写进 keywords 会导致搜不到菜、答非所问；
5. 口语描述（热乎的/开胃的/顶饱的/清淡养胃/想吃点好的）映射到最接近的词表标签；
6. 完全闲聊或没有有效条件就全部留空，此时 scope 填 window；
7. **只要抽到了任何 tags / budget / keywords / exclude_tags，scope 就必须是 dish**（或 shop）。
8. 用户明确“只要窗口/档口、不要菜”时，only_window=true、scope=window；否则 only_window=false。
   普通“有什么吃的/今天吃什么”只是窗口榜起步，仍可推荐菜，only_window=false。
例1 "早上没吃饭，来碗热乎的，别太油，8块以内" → {"campus":"","canteen":"","floor":"","shop":"","budget":"8","budget_around":false,"building":"","tags":["早餐","粥汤"],"exclude_tags":[],"avoid":"","keywords":"","scope":"dish"}
例2 "我这两天肠胃不舒服，想吃点清淡养胃的，不要辣的酸的" → {"campus":"","canteen":"","floor":"","shop":"","budget":"","budget_around":false,"building":"","tags":["清淡","粥汤"],"exclude_tags":["辣","酸"],"avoid":"","keywords":"","scope":"dish"}
例3 "东食堂二楼10块以内的面，不吃猪肉，对花生过敏" → {"campus":"","canteen":"东食堂","floor":"二楼","budget":"10","budget_around":false,"building":"","tags":["面"],"exclude_tags":["猪肉"],"avoid":"猪肉,花生","keywords":"","scope":"dish"}
例4 "我在5号楼，想吃碗十块钱左右的面" → {"campus":"","canteen":"","floor":"","shop":"","budget":"10","budget_around":true,"building":"5","tags":["面"],"exclude_tags":[],"avoid":"","keywords":"","scope":"dish"}
例5 "有什么吃的没" → {"campus":"","canteen":"","floor":"","shop":"","budget":"","budget_around":false,"building":"","tags":[],"exclude_tags":[],"avoid":"","keywords":"","scope":"window"}
例6 "东食堂二楼有什么好吃的" → {"campus":"","canteen":"东食堂","floor":"二楼","shop":"","budget":"","budget_around":false,"building":"","tags":[],"exclude_tags":[],"avoid":"","keywords":"","scope":"window"}
例7 "天下好面有什么菜" → {"campus":"","canteen":"","floor":"","shop":"天下好面","budget":"","budget_around":false,"building":"","tags":[],"exclude_tags":[],"avoid":"","keywords":"","scope":"shop"}
例8 "你是谁" → {"campus":"","canteen":"","floor":"","shop":"","budget":"","budget_around":false,"building":"","tags":[],"exclude_tags":[],"avoid":"","keywords":"","scope":"window"}
例9 "介绍一下西安石油大学" → {"campus":"","canteen":"","floor":"","shop":"","budget":"","budget_around":false,"building":"","tags":[],"exclude_tags":[],"avoid":"","keywords":"","scope":"window"}
（例1–7 的 intent 都是 food；例8、例9 的 intent 是 chat。**每个 JSON 都要带 intent 字段**）

学生的话：%s
JSON："""

def available():
    return bool(API_KEY)

_CACHE = {}      

_NEG_PREFIX = re.compile(
    r'^(?:不要|不吃|不想吃|不能吃|不爱吃|别放|别加|别要|别|不|忌口|忌|没有|无|'
    r'去掉|去除|去|少放|少|免|omit|no)\s*(.+)$', re.I)

def _split_neg(w):
    """'不要辣' → (True, '辣')；'辣' → (False, '辣')。"""
    m = _NEG_PREFIX.match(w)
    if m and m.group(1).strip():
        return True, m.group(1).strip()
    return False, w

_NEG_BEFORE = (r"(?:不要|不吃|不想吃|不爱吃|不想|别吃|别要|别放|别加|别推荐|别来|别给|"
               r"别上|不来|忌口|忌|没有|无|少放|少|免|去掉|去除|去)")

_ALL_TAGS = tuple(dict.fromkeys((TAG_VOCAB + " " + EXCLUDE_VOCAB).split()))

def _negated_in_question(question, tag):
    """用户原句里这个标签是不是被否定的？

    关键：否定词的宾语是它后面**最先出现的那个标签**。
        "不吃辣的面"  → 否定"辣"，**不是**"面"（用户要的是面）
        "不要辣的面"  → 同上
        "我不想吃太辣的" → 否定"辣"
    第一版实现允许否定词与标签之间夹 0–3 个任意字，结果把 "不吃辣的面" 里的
    "面" 也判成被否定（跨过了"辣"）—— 这里改成"窗口内最先出现的标签才算"。
    """
    if not question or not tag:
        return False
    for m in re.finditer(_NEG_BEFORE, question):
        seg = question[m.end(): m.end() + 8]
        seg = re.split(r"[，。！？、,;；\s]", seg, 1)[0]      
        if tag not in seg:
            continue
        hits = [(seg.find(v), v) for v in _ALL_TAGS if v in seg]
        if hits and min(hits)[1] == tag:                     
            return True
    if re.search(r"不" + re.escape(tag), question):           
        return True
    return False

_QTY_RE = re.compile(
    r'(?:\d+|[一二两二三四五六七八九十]+)\s*(?:个|道|份|款|样|种|碗|杯|碟|盘)'
    r'|几(?:个|道|份|款|样|种)'
    r'|来点|整点|推荐道|来道|个菜|道菜')

def _reconcile_top_k(question, parsed):
    """数量闸（2026-10-17）：原句里没有数量线索 → 不认 LLM 给的 top_k。

    背景：真模型实测把「想吃面 / 想吃清淡的 / 10块以内的面」都抽成 top_k=1，
    于是只返回一张卡 —— 用户会以为"系统只找到一道菜"。
    数量是**强约束**（直接决定展示条数），比一般槽位更需要原句依据，
    所以宁可退回"没提数量"的默认展示条数，也不要按模型臆测砍列表。
    """
    if not parsed.get("top_k"):
        return parsed
    if _QTY_RE.search(question or ""):
        return parsed
    out = dict(parsed)
    out["top_k"] = ""
    out["_top_k_dropped"] = True
    return out

def _reconcile_with_question(question, parsed):
    """LLM 若把用户明确否定的标签放进 tags，强制挪到 exclude_tags。"""
    tags = [t for t in str(parsed.get("tags") or "").split(",") if t]
    excl = [t for t in str(parsed.get("exclude_tags") or "").split(",") if t]
    keep, moved = [], []
    for t in tags:
        (moved if _negated_in_question(question, t) else keep).append(t)
    if not moved:
        return parsed
    out = dict(parsed)
    out["tags"] = ",".join(keep)
    out["exclude_tags"] = ",".join(excl + [t for t in moved if t not in excl])
    return out

def extract_slots(question):
    """调 LLM 抽槽位（带缓存）。返回 dict（字段与 nlu.parse_question 对齐）或 None。"""
    if question in _CACHE:
        return _CACHE[question]
    r = _extract(question)
    if r is not None:            
        _CACHE[question] = r
    return r

def _extract(question):
    if not available() or not question or len(question) > MAX_Q_LEN:
        return None
    body = json.dumps({
        "model": MODEL,
        "messages": [{"role": "user", "content": PROMPT % (TAG_VOCAB, EXCLUDE_VOCAB, AVOID_VOCAB, question)}],
        "temperature": 0, "max_tokens": 300,
    }).encode("utf-8")
    req = urllib.request.Request(
        BASE_URL.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + API_KEY})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
        text = data["choices"][0]["message"]["content"].strip()
    except Exception:
        return None
    parsed = _parse(text)

    if parsed is None:
        return None
    parsed = _reconcile_with_question(question, parsed)

    parsed = _reconcile_top_k(question, parsed)

    ev, dropped = _validate_evidence(question, parsed)
    parsed['evidence'] = ev
    parsed['evidence_dropped'] = dropped
    return parsed

def _parse(text):
    """从 LLM 回复里抠出 JSON（容忍 ```json 包裹与前后废话）。"""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        o = json.loads(m.group(0))
    except Exception:
        return None

    def _vocab_hits(w, vocab):
        """词表命中：优先整词，否则取被包含的词表词（"鸡蛋面"→面/蛋）。

        2026-10-13 修：原实现用 `set(vocab.split())` 建集合再遍历，**输出顺序随
        PYTHONHASHSEED 变化** → 同一句话在不同进程/重启后得到 tags 顺序不同
        （"面,蛋,清淡" vs "蛋,面,清淡"），normalized.tags 与 slots_text 跟着变，
        与"同问同答"的承诺相抵触。改用 list 保序遍历。
        """
        allowed = vocab.split()
        if w in allowed:
            return [w]
        return [v for v in allowed if v in w]

    def _as_list(v):
        """LLM 有时把数组写成字符串（"tags":"清淡"）——按字符串迭代会逐字拆开，
        "清淡"→"清"+"淡" 两个都不在词表，条件直接丢失。这里统一成 list。"""
        if v is None:
            return []
        if isinstance(v, (list, tuple)):
            return v
        return [x for x in re.split(r'[,，、;；\s]+', str(v)) if x.strip()]

    def _as_text(v):
        """反向归一：把可能返回成**数组**的字段拼成逗号串。

        2026-10-18 修：实测模型对 keywords 有时返回 ["茶叶蛋"]，原来 str() 得到
        "['茶叶蛋']" → 引擎按菜名找 "['茶叶蛋']" 永远匹配不到，于是"库里有 4 处茶叶蛋"
        却被回答成"没找到茶叶蛋"。tags/avoid 同理。
        """
        if v is None:
            return ""
        if isinstance(v, (list, tuple)):
            return ",".join(str(x).strip() for x in v if str(x).strip())
        return str(v)

    tags, excl, dropped = [], [], []
    for w in _as_list(o.get("tags")):
        w = str(w).strip()
        if not w:
            continue
        neg, core = _split_neg(w)

        (excl if neg else tags).extend(_vocab_hits(core, EXCLUDE_VOCAB if neg else TAG_VOCAB))
    for w in _as_list(o.get("exclude_tags")):
        w = str(w).strip()
        if not w:
            continue
        _neg, core = _split_neg(w)          
        hits = _vocab_hits(core, EXCLUDE_VOCAB)
        if hits:
            excl.extend(hits)
        else:

            dropped.append(core)

    def _dedupe(seq):
        seen, out = set(), []
        for x in seq:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    tags, excl = _dedupe(tags), _dedupe(excl)

    tags = [t for t in tags if t not in set(excl)]

    avoid_words = [str(a).strip() for a in _as_list(o.get("avoid")) if str(a).strip()]
    avoid_words += [d for d in dropped if d not in avoid_words]

    return {
        "campus": str(o.get("campus") or ""),
        "canteen": str(o.get("canteen") or ""),
        "floor": str(o.get("floor") or ""),
        "shop": str(o.get("shop") or ""),
        "budget": str(o.get("budget") or "").replace("元", ""),

        "budget_around": "1" if str(o.get("budget_around")).strip().lower()
                              in ("1", "true", "yes", "是", "对") else "",
        "building": str(o.get("building") or "").replace("号楼", "").replace("号", "").strip(),
        "tags": ",".join(tags),
        "exclude_tags": ",".join(excl),

        "avoid": ",".join(avoid_words),

        "keywords": _as_text(o.get("keywords")),

        "top_k": (str(int(o.get("top_k"))) if str(o.get("top_k") or "").strip().isdigit()
                  and 1 <= int(o.get("top_k")) <= 5 else ""),

        "scope": str(o.get("scope") or "").strip().lower()
                 if str(o.get("scope") or "").strip().lower() in ("window", "dish", "shop") else "",

        "only_window": "1" if str(o.get("only_window")).strip().lower()
                              in ("1", "true", "yes", "是", "对") else "",

        "_intent": "chat" if str(o.get("intent") or "").strip().lower() == "chat" else "food",

        "confidence": _clamp_conf(o.get("confidence")),

        "evidence": o.get("evidence") if isinstance(o.get("evidence"), dict) else {},

        "modifier": ",".join(_norm_modifiers(o.get("modifier"))),
    }

MODIFIERS = ('cheaper', 'more_spicy', 'lighter', 'quicker', 'random')

def _norm_modifiers(v):
    """把 LLM 给的 modifier 归一成白名单内的有序去重列表；非法值丢掉。"""
    if v is None:
        return []
    if isinstance(v, str):
        v = [x for x in v.replace('，', ',').split(',')]
    out = []
    for x in (v or []):
        t = str(x or '').strip().lower()
        if t in MODIFIERS and t not in out:
            out.append(t)
    return out

def _clamp_conf(v):
    """置信度归一：只接受 0~1 的数字/数字串，其余返回空串。"""
    try:
        f = float(str(v).strip())
    except (TypeError, ValueError):
        return ""
    if f != f:                                  
        return ""
    return round(min(max(f, 0.0), 1.0), 2)

_EVIDENCE_KEYS = ('tags', 'exclude_tags', 'avoid', 'keywords', 'scope', 'top_k',
                  'canteen', 'floor', 'shop', 'budget', 'building')

def _validate_evidence(question, parsed):
    """把 LLM 自报的 evidence 逐个对质用户原句：**值必须是原句的逐字子串**。

    为什么这么严：模型最容易"顺手编一个看起来合理的证据"。
    只要要求它逐字摘抄，编造就会当场暴露（对不上就丢）。
    返回 (保留的 evidence dict, 丢弃数)。
    """
    ev = parsed.get('evidence') or {}
    if not isinstance(ev, dict):
        return {}, 0
    q = question or ''
    chosen = set()
    for k in ('tags', 'exclude_tags', 'avoid', 'keywords'):
        for t in str(parsed.get(k) or '').split(','):
            if t.strip():
                chosen.add(t.strip())
    for k in ('scope', 'top_k', 'canteen', 'floor', 'shop', 'budget', 'building'):
        if str(parsed.get(k) or '').strip():
            chosen.add(str(parsed[k]).strip())
    keep, dropped = {}, 0
    for label, snippet in ev.items():
        s = str(snippet or '').strip()
        label = str(label or '').strip()

        if not s or s not in q or (label not in chosen and label not in _EVIDENCE_KEYS):
            dropped += 1
            continue
        keep[label] = s
    return keep, dropped
