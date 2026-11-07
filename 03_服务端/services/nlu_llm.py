
"""用 LLM 做「口语 → 槽位」（替换脆弱的正则层），失败自动降级。

为什么这样切分：
  - **LLM 只做翻译**：把"不要辣的""饿了随便来点""早上有啥"翻成结构化槽位；
  - **决策仍是确定性代码**：槽位交给 services/slots.py 归一化、services/recommend.py 过滤打分，
    所以"同一句话问两次结果一致"仍然成立（且相同问法走本地缓存，不重复请求）。
  - 词表白名单：LLM 抽出的标签必须在我们 tags.json 里，越界的记到 `tags_unmatched` → 由回复层诚实告知。

用法：
    from services import nlu_llm
    slots_raw, meta = nlu_llm.parse(text)      # meta: {source, llm_ms, unmatched_tags, error}
"""
import json
import time

from dao import db
from services import llm

SYSTEM = """你是「校园食堂点餐助手」的信息抽取模块。**只做抽取，不做推荐、不给解释、不闲聊**。
必须输出一个 JSON 对象，字段固定为：
{"campus":"", "canteen":"", "floor":"", "shop":"", "budget":"", "tags":[], "exclude_tags":[], "avoid":[], "keywords":[], "only_window":false}

抽取规则：
1. canteen 只能取「东食堂 / 西食堂」；口语里的"东区/东边/东餐厅"都算东食堂，"西区/西边"算西食堂。
2. floor 只能取「一楼 / 二楼 / 三楼」；"3楼/三层/2F"归一成"三楼/二楼"。
3. budget 只填数字（"15块以内"→15，"十块"→10）；没说就给空串。
4. tags 是"想要"的标签，**必须从下面的词表里挑**，挑不到就放 tags_unmatched（数组，另加字段）。
5. exclude_tags 是"明确不要"的标签：**"不要辣/别放辣/不辣的"→exclude_tags:["辣"]**；
   "不吃猪肉"这类主料忌口同时写进 avoid 与 exclude_tags（如果词表里有该主料词）。
6. avoid 收口语忌口/过敏原文（"不吃香菜"→["香菜"]，"对海鲜过敏"→["海鲜过敏"]）。
7. keywords 只放**具体菜名**（"羊肉泡馍"、"油泼面"）；"有什么好吃的""随便来点"这类泛指不填。
8. 没提到的字段一律空串或空数组，**不要猜测、不要补全**。
9. only_window：用户明确只要窗口/档口、不要菜时填 true，否则 false。

词表（只能用这些值）：
{catalog}
"""

def _catalog():
    """从数据库拉词表：食堂/楼层/标签（按维度）/过敏原类别"""
    tags = db.query('SELECT dimension, tag FROM tag ORDER BY dimension, tag')
    dims = {}
    for t in tags:
        dims.setdefault(t['dimension'], []).append(t['tag'])
    floors = [r['floor'] for r in db.query('SELECT DISTINCT floor FROM shop ORDER BY floor')]
    canteens = [r['canteen'] for r in db.query('SELECT DISTINCT canteen FROM shop ORDER BY canteen')]
    lines = ['- 食堂：' + '、'.join(canteens), '- 楼层：' + '、'.join(floors)]
    for d, ts in dims.items():
        lines.append('- %s：%s' % (d, '、'.join(ts)))
    lines.append('- 过敏原类别：海鲜/甲壳类、鱼类、蛋类、奶类、花生、坚果/芝麻、大豆、麸质（小麦）')
    return '\n'.join(lines)

def _tag_universe():
    return {r['tag'] for r in db.query('SELECT tag FROM tag')}

def parse(text):
    """返回 (槽位 dict 喂给 slots.normalize_slots, meta)。失败抛异常由上层降级。"""
    t0 = time.time()
    system = SYSTEM.replace('{catalog}', _catalog())
    obj = llm.ask_json(system, text, cache_key=text, max_tokens=500)
    if not isinstance(obj, dict):
        raise llm.LLMError('LLM 返回的不是对象')

    uni = _tag_universe()

    def arr(v):
        if isinstance(v, str):
            v = [x for x in v.replace('，', ',').split(',') if x.strip()]
        return [str(x).strip() for x in (v or []) if str(x).strip()]

    tags = arr(obj.get('tags'))
    excl = arr(obj.get('exclude_tags'))
    unmatched = [t for t in tags + excl if t not in uni]
    tags = [t for t in tags if t in uni]
    excl = [t for t in excl if t in uni]

    budget = obj.get('budget')
    if isinstance(budget, str):
        budget = ''.join(ch for ch in budget if ch.isdigit() or ch == '.')
    try:
        b = float(budget)
        b = b if 0 < b <= 200 else ''
    except (TypeError, ValueError):
        b = ''

    raw = {
        'campus': str(obj.get('campus') or ''),
        'canteen': str(obj.get('canteen') or ''),
        'floor': str(obj.get('floor') or ''),
        'shop': str(obj.get('shop') or ''),
        'budget': b,
        'tags': ','.join(tags),
        'exclude_tags': ','.join(excl),
        'avoid': ','.join(arr(obj.get('avoid'))),
        'keywords': ','.join(arr(obj.get('keywords'))),
        'only_window': bool(obj.get('only_window')),
    }
    meta = {'source': 'llm', 'llm_ms': int((time.time() - t0) * 1000),
            'unmatched_tags': unmatched, 'model': llm.load_config()['model']}
    return raw, meta
