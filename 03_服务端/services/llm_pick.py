
"""候选池挑选器（LLM 放权 L2）——让推荐别再"只会按销量排"。

定位（与项目一贯口径一致）
--------------------------------------------------------------------
规则引擎负责**能不能吃**（忌口/过敏/预算/放宽/小食过滤，全部确定性、可复核），
本模块只负责**先给哪几道**——在规则已经筛好的**封闭候选池**里挑选并排序。

为什么这是安全的（"放权"但不失控）：
  1. **池子封闭**：候选由 `recommend.py` 算好（最多 60 道），LLM 只能从里面挑；
     它没有能力凭空造出一道菜 —— 返回的 dish_id 不在池里就被丢弃。
  2. **只改顺序，不改事实**：价格、档口、标签全部来自规则结果，LLM 一个字都改不了。
  3. **失败即回退**：无 key / 超时 / 非 JSON / id 全不合法 → 返回空列表 →
     引擎保持规则排序（等同没启用），演示不会挂。
  4. **同问同答**：temperature 0 + 磁盘缓存（键含问题与候选池指纹），
     同一句话+同一候选池 → 同一个顺序。
  5. `pick_source` 字段如实记录本次到底是 `llm` 还是 `rule` 排的，答辩可核对。

对比退役的 v1（`services/llm_recommend.py`，让 LLM 自己硬过滤+挑菜）：
那一版把**过滤**也交给了 LLM，于是"为什么这道被排除了"说不清。这里过滤永远在规则层。
"""
import hashlib
import json
import os
import re
import sys

_APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _APP not in sys.path:
    sys.path.insert(0, _APP)

MAX_POOL = 60          
MIN_POOL = 6           
PICK_CAP = 12          

SYSTEM = """你是大学食堂推荐助手。系统已经按用户的硬性条件（忌口、过敏、预算、楼层等）
筛好了一批候选菜品，你只需要**从这批候选里**挑出最该先展示的几道，并按推荐先后排序。

硬性要求：
1. 只能从给定的候选里选，**绝对不许编造**任何 dish_id、菜名或价格；
2. 输出严格 JSON：{"pick":["D0123","D0456"],"note":"一句15字内理由"}，不要输出其它文字；
3. 优先考虑：和用户意图最贴合（想吃面就给面，想清淡就别给重口）、档口多样性
   （别连着给同一家），以及候选的先后（排在前面的规则分更高，别大幅推翻）；
4. 最多挑 %d 道，少了没关系，宁可少挑也别凑数。
候选行里的价格是**实际价格（元）**，不是价格档；只能引用候选里出现的事实。""" % PICK_CAP

def _line(d):

    tags = d.get('tags')
    if isinstance(tags, (set, list, tuple)):
        tagtxt = '、'.join(sorted(t for t in tags if t)[:6])
    else:
        tagtxt = ','.join([t for t in (d.get('tags_str') or '').split(',') if t][:6])

    try:
        _price = ('%g元' % float(d.get('price'))) if d.get('price') is not None else '—'
    except (TypeError, ValueError):
        _price = '—'
    return '%s | %s | %s | %s | %s' % (
        d.get('dish_id'), d.get('name'), d.get('shop'), _price, tagtxt)

def available():
    try:
        from services import llm
        return bool(llm.status().get('enabled'))
    except Exception:                                          
        return False

def make_picker(question, slots=None):
    """返回一个 picker 回调（喂给 recommend(recommend 的 picker=...)），
    不可用时返回 None（引擎就走规则排序）。"""
    if not available():
        return None

    def _pick(pool):
        if not pool or len(pool) < MIN_POOL:
            return []
        rows = pool[:MAX_POOL]
        lines = '\n'.join(_line(d) for d in rows)
        intent = []
        if slots:
            if slots.get('tags'):
                intent.append('想吃：%s' % '+'.join(slots['tags']))
            if slots.get('exclude'):
                intent.append('不吃：%s' % '+'.join(slots['exclude']))
            if slots.get('budget'):
                intent.append('预算：%s 元' % slots['budget'])
            if slots.get('canteen_q'):
                intent.append('食堂：%s' % slots['canteen_q'])
        try:
            from services import llm

            sig = hashlib.sha256(
                ('%s||%s' % (question or '', '|'.join(
                    '%s:%s' % (d['dish_id'], d.get('price')) for d in rows))).encode('utf-8')
            ).hexdigest()[:16]
            text = llm.chat(
                [{'role': 'system', 'content': SYSTEM},
                 {'role': 'user', 'content': '用户说的是：%s\n%s\n\n候选菜品：\n%s'
                  % (question or '', ('；'.join(intent) if intent else ''), lines)}],
                max_tokens=300, use_cache_key='pick:' + sig, json_mode=True)
        except Exception as e:                                 
            sys.stderr.write('[pick] 调用失败，回落规则排序：%s: %s\n' % (type(e).__name__, e))
            return []
        m = re.search(r'\{.*\}', text, re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except Exception:                                      
            return []
        picks = obj.get('pick')
        if not isinstance(picks, list):
            return []
        return [str(x).strip() for x in picks if str(x).strip()]

    return _pick
