
"""闲聊 / 身份 / 学校介绍 —— 不输出窗口与菜品，只聊天。

为什么要有这一层（用户反馈）：
  以前**任何**问法都进推荐流程，「你是谁」「介绍一下学校」也会被硬塞一份窗口+菜品榜，
  而且身份提示词写死成"小食"。现在：
    · 明显是闲聊/问身份/问学校的 → 走本模块，返回 `mode='chat'`，只给一段自然语言；
    · 找吃的（有预算/口味/食堂/菜名/推荐意图）→ 照旧走推荐流程，不受影响；
    · 没配 LLM key 或调用失败 → 用**内置话术**兜底，离线也能用。

人设与事实都在 services/persona.py（改人设只改那一个文件）。
"""
import random
import re

from services import llm
from services import persona as PA

MAX_LEN = 70            

_STRONG_CHAT = [
    r'你是谁', r'你叫什么', r'你叫啥', r'你的名字', r'你是什么(?:东西|助手|机器人)?',
    r'你(?:会|能|可以)做?(?:什么|啥|些啥)', r'自我介绍', r'介绍(?:一下|下)?你(?:自己)?',
    r'你是(?:人|机器人|AI|人工智能)',
    r'介绍(?:一下|下)?(?:学校|西石大|西安石油大学|我们学校)',
    r'(?:学校|西石大|西安石油大学)(?:怎么样|介绍|简介)',
    r'(?:食堂|餐厅|档口)?(?:都)?(?:几点|什么时候)(?:开|开门|关门|营业|开始)',
    r'(?:营业|开放)时间', r'(?:食堂|餐厅)(?:都)?在哪',

    r'几点(?:有|吃|开|供)?(?:早|午|晚|夜)?(?:饭|餐)', r'(?:早|午|晚|夜)(?:饭|餐)(?:是)?几点',
    r'^[你您]?(?:好|您好|哈喽|hi|hello|在吗|在不在)[!！。~～]?$',
    r'^(?:谢谢|感谢|多谢|辛苦了|再见|拜拜|晚安)[!！。~～]?$',
    r'天气', r'你能(?:不能)?聊天',
]
_CHAT_PATTERNS = _STRONG_CHAT + [r'你(?:会|能|可以)(?:什么|啥)', r'你(?:今天)?(?:开心|难过|心情)']
_STRONG_RE = re.compile('|'.join(_STRONG_CHAT), re.I)
_CHAT_RE = re.compile('|'.join(_CHAT_PATTERNS), re.I)

_FOOD_HINT = re.compile(
    r'(想吃|想喝|推荐|有什么|啥好吃|好吃|便宜的?|贵|预算|块|元|¥|面|饭|粉|粥|汤|菜|肉|辣|清淡|素食|饺子|包子|'
    r'麻辣烫|香锅|米线|早餐|午饭|晚饭|夜宵|食堂|楼层|档口|小吃街)')

_HARD_FOOD = re.compile(
    r'(\d+\s*(?:块|元|钱|¥)|\d+\s*以内|想吃|想喝|想点|推荐|来[一碗份个]|有什么(?:吃|好吃|菜|面|饭)|'
    r'不要辣|别放辣|不吃|忌口|过敏|预算|便宜|快餐|套餐|盖饭|拌面|拉面|米线|麻辣烫|香锅|饺子|包子|早餐|夜宵)')

FALLBACK = {
    'identity': [
        '%s在此，专门帮你解决今天吃啥。' % PA.AGENT_NAME,
        '我是%s，%s的校园吃饭助手。' % (PA.AGENT_NAME, PA.PRODUCT),
        '叫%s就行，今天想吃点啥？' % PA.AGENT_NAME,
    ],
    'ability': [
        '说预算、口味、楼层、忌口都行，我先帮你找窗口，再挑菜。',
        '我能按食堂位置、预算、口味和忌口帮你筛，也能带你看档口价目表。',
        '不知道吃啥的话，先告诉我想吃哪一类，或者让我直接帮你挑几个窗口。',
    ],
    'school': [
        '%s鄠邑校区有三个就餐区：西食堂、东食堂和小吃街，合计约 128 个档口、2063 道在售菜品。' % PA.SCHOOL,
        '目前主要覆盖鄠邑校区的西食堂、东食堂和小吃街，共约 128 个档口。',
        '学校食堂主要分西食堂、东食堂和小吃街，雁塔和明德暂未采集完整菜单。',
    ],
    'hours': [
        '早餐按 6:30–9:30 处理；午晚饭各档口时间不一样，我这边没有实时营业数据，建议以现场为准。',
        '早餐一般按 6:30–9:30 算，午晚饭各窗口开放时间不同，具体以现场为准。',
        '我只有早餐时段的统一口径，其他时段各档口差异较大，现场看一下更准。',
    ],
    'greet': [
        '在的，今天想吃什么？',
        '来了，说个预算或口味，我帮你挑。',
        '我在，先说说想吃哪一类？',
        '在呢，想让我推荐窗口还是直接看菜？',
    ],
    'thanks': [
        '不客气，干饭要紧。',
        '好嘞，吃好喝好。',
        '没事，祝你今天吃得开心。',
    ],
    'unknown': [
        '这句我还没太听懂，换个说法试试？',
        '可以再说具体一点，比如想吃什么、在哪个食堂、预算多少。',
        '我暂时没理解，试试说“推荐几个窗口”或“15块以内的面”。',
        '没太听懂，但你可以直接说想吃的东西或预算。',
    ],
}

def _pick_fallback(key):
    vals = FALLBACK.get(key)
    if isinstance(vals, (list, tuple)):
        return random.choice(vals)
    return vals or FALLBACK['unknown'][0]

def is_strong_chat(text):
    """一眼就是闲聊（问身份/问学校/寒暄/问几点）——规则快路用，省一次 LLM 调用。"""
    t = (text or '').strip()
    return bool(t) and len(t) <= 40 and bool(_STRONG_RE.search(t))

def has_food_signal(text):
    """句子里有"硬"找吃信号（价格/想吃/推荐/点名菜品特征词）。

    用途：LLM 判 chat、但句子明显是在点菜时，**以事实为准**回退到推荐流程。
    只认极窄的模式 —— 单字（饭/菜/面）不算，否则「几点有早饭」会被误拦。"""
    return bool(_HARD_FOOD.search(text or ''))

def is_chat(text, params=None):
    """是否走闲聊层。有结构化参数（前端筛选面板）一律不走。"""
    if not text:
        return False
    if any((params or {}).get(k) for k in ('canteen', 'floor', 'budget', 'tags', 'exclude', 'shop', 'keywords')):
        return False
    t = text.strip()
    if len(t) > 40:                       
        return False
    if _STRONG_RE.search(t):              
        return True
    if _FOOD_HINT.search(t):              
        return False
    return bool(_CHAT_RE.search(t))

def _fallback_kind(t):
    if re.search(r'你是谁|你叫什么|你叫啥|你的名字|介绍(一下|下)?你|自我介绍|你是(人|机器人|AI|人工智能)', t):
        return 'identity'
    if re.search(r'你(会|能|可以)(做)?(什么|啥)', t):
        return 'ability'
    if re.search(r'介绍|学校|西石大|西安石油大学', t):
        return 'school'
    if re.search(r'几点|营业|开放时间|开门|关门', t):
        return 'hours'
    if re.search(r'^(你好|您好|哈喽|hi|hello|在吗|在不在)', t, re.I):
        return 'greet'
    if re.search(r'谢谢|感谢|多谢|辛苦了|再见|拜拜|晚安', t):
        return 'thanks'
    return 'unknown'

HOURS_Q_RE = re.compile(r'(几点(开|关|营业|有饭|开始|结束)|营业时间|开门|关门|开门时间|早点(几点|有)|早餐几点|什么时候开|开到几点)')

def is_hours_q(text):
    """问营业/时间。"""
    return bool(text) and bool(HOURS_Q_RE.search(text))

def hours_reply():
    """营业时间话术——直接用已有的 FALLBACK['hours']（单一出处，不编具体时间）。"""
    return _pick_fallback('hours')

def reply(text, params=None):
    """返回 (话术, 来源)。来源：'llm' | 'fallback'。任何异常都不抛。"""
    t = (text or '').strip()

    if is_hours_q(t):
        return _pick_fallback('hours'), 'fallback' 

    if _fallback_kind(t) == 'unknown':
        return _pick_fallback('unknown'), 'fallback'
    try:
        if llm.available():
            out = llm.chat([{'role': 'system', 'content': PA.chat_system()},
                            {'role': 'user', 'content': t}],
                           max_tokens=300, use_cache_key='chat|' + t,
                           json_mode=False)      
            out = (out or '').strip().strip('"“”')
            if 0 < len(out) <= MAX_LEN:
                return out, 'llm'
    except Exception:                     
        pass
    return _pick_fallback(_fallback_kind(t)), 'fallback'
