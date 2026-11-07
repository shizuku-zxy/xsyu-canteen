
"""路由层 · 推荐：一句话 / 结构化槽位 → 推荐结果。

架构（2026-10-12 v2：LLM 理解 + 规则推荐，两层解耦）：
    理解层：学生口语 ──LLM(豆包)槽位抽取──▶ 模版化勾选后端标签
            LLM 不可用/超时/解析失败 → 规则 NLU（nlu.parse_question）兜底
    推荐层：normalize_slots 归一 → 本地规则引擎（过滤/打分/放宽/过敏安全门）
            八条承诺不变量全部在本地，LLM 无法破坏。
    （v1 的 llm_recommend「LLM 从候选池挑菜」路径退役，文件保留备查；
     需要语义级挑选时可重新启用。）

契约（与前端、与线上大模型版完全一致）：
  has_result / total_count / shop_count / stage / relax_note / kw_note
  allergy_note / allergen_excluded / allergen_note_text
  items[{name,price,shop,loc}] / reply_text / reason / slots_text / normalized / ms
  engine: llm_slots（LLM理解+规则推荐）/ rule（纯规则）
"""
import json
import random
import re
import sqlite3
import sys
import threading
import time

from dao import db
from services import nlu
from services import exposure
from services import lexicon
from services import recommend as R
from services import slots as S
from services import llm_fallback

SLOT_KEYS = ('campus', 'canteen', 'floor', 'shop', 'budget', 'budget_lo', 'budget_around', 'building', 'top_k',
             'modifier', 
             'tags', 'exclude_tags', 'avoid', 'keywords')

AGENT_NAME = '小油'          

def _llm_label():
    """用户可见的智能体自称。**写成常量、不写死模型名**：
    用户是在跟"小油"对话，用的是哪个大模型属于实现细节（放在 llm_model 字段里备查）。"""
    return AGENT_NAME

def _llm_model():
    """实际调用的大模型（透明字段，答辩时可核验："换模型不影响决策"）。"""
    try:
        if llm_fallback.available():
            return llm_fallback.MODEL or ''
    except Exception:
        pass
    try:
        from services import llm
        if llm.available():
            return llm.load_config().get('model') or ''
    except Exception:
        pass
    return ''

_SOFT_CAT_TAGS = ('面', '粉', '米饭', '快餐', '粥汤', '汤', '小吃饼类', '水饺包子', '汉堡披萨',
                  '麻辣烫香锅', '甜品', '饮品', '轻食', '早餐', '辣', '甜', '酸', '清淡', '素食', '素菜', '荤菜')

_NEG_EVIDENCE_RE = re.compile(r'不[要吃不想要喝]|不[辣甜酸咸]|别[放加要给]?|忌口|忌|过敏|除[了掉]|拒绝|讨厌|受不了|少[放加点]|(?<!有)没有|不含|不加|不能吃|不可以吃')
_CORRECTION_RE = re.compile(r'(?:我没说|没说|没有说|我不是说|不是说|我没要|没要|不是要|不想要)\s*(?:\d+|[一两二三四五])\s*(?:个|道|份|种|样)?')
_REL_MORE_RE = re.compile(r'多推荐|再推荐|多来|再来|多给|继续推荐|再多')
_EXPLICIT_QTY_RE = re.compile(r'(?:[一两二三四五]|\d)\s*(?:个|道|份|种|样|家)?\s*(?:窗口|档口|菜|菜品)')
_CORRECTION_REPLIES = [
    '明白，不按这个数量来。你想看几个窗口？',
    '收到，那不算这个数量。想让我推荐几个窗口？',
    '好，数量听你的。想看窗口还是菜品？',
    '明白，不按刚才的数量。要几个窗口，或者直接说想吃啥？',
]

def _drop_unevidenced_exclude(slots, question):
    """把"没有否定证据"的品类排除项删掉（A 的闸门）。返回被删掉的词。"""
    q = question or ''
    if _NEG_EVIDENCE_RE.search(q):
        return []
    ex = [x.strip() for x in str((slots.get('exclude_tags') or '')).split(',') if x.strip()]
    if not ex:
        return []
    keep = [x for x in ex if x not in _SOFT_CAT_TAGS]
    gone = [x for x in ex if x in _SOFT_CAT_TAGS]
    if gone:
        slots['exclude_tags'] = ','.join(keep)
    return gone

def _merge_slots(llm, rule, question=''):
    """LLM 槽位为主，规则 NLU 补空（2026-10-13 修）。

    问题：LLM 抽空（判成闲聊/没听懂）时，原来把规则 NLU 的结果**整个丢掉**。
    于是「有羊肉泡馍吗」这种**点名一道库里没有的菜**的问法，规则侧明明已经抽到
    keywords=羊肉泡馍（能据此如实回答"没有这道菜"），却因为 LLM 回了空槽位而丢失，
    结果答非所问地推出一堆无关菜 —— 这正是要防的"偏离"。

    合并策略（保守，只做加法，且只补"LLM 没给"的字段）：
      · 补空标量：campus / canteen / floor / budget / budget_around / building；
        （budget_around 尤其重要：规则侧对"左右/上下/大概"识别很稳，
          而 LLM 漏了它就等于把"10块左右"悄悄变成"10块以内"，改了用户的条件）
      · keywords 只在"LLM 整体没听懂"（tags 和 keywords 都空）时才兜底 ——
        规则 NLU 的关键词抽取偏激进（"帮我推荐个早饭" 会抽出"个早饭"），
        已有其它信号时硬并进去会产生"关键词未直接命中菜名"的噪音提示；
      · 并集"负向"约束：exclude_tags / avoid（只会排除更多，方向天然安全）；
      · 绝不并 tags（正向偏好）：多一个标签就可能把用户没要的东西强加上去；
      · 绝不补 shop：规则 NLU 的店名抽取偏激进（"麻辣香锅"会被当成档口名）。

    这样既修好"点名不存在的菜"的诚实回答，也不影响
    D10（LLM 判闲聊全空 → 规则开放式兜底仍有结果）与 I1 确定性。
    """
    out = dict(llm)
    for k in ('campus', 'canteen', 'floor', 'budget', 'budget_around', 'building'):
        if not str(out.get(k) or '').strip() and str(rule.get(k) or '').strip():
            out[k] = rule[k]

    if not str(out.get('tags') or '').strip() and not str(out.get('keywords') or '').strip() \
            and str(rule.get('keywords') or '').strip():
        out['keywords'] = rule['keywords']

    if not str(out.get('tags') or '').strip() and str(rule.get('tags') or '').strip():
        _qt = [x.strip() for x in str(rule['tags']).split(',') if x.strip()]
        _keep = [x for x in _qt if x and x in (question or '')]
        if _keep:
            out['tags'] = ','.join(_keep)

    if not str(out.get('shop') or '').strip() and str(rule.get('shop') or '').strip():
        _s = str(rule['shop']).strip()
        _short = _s.split('·')[0].split('（')[0].strip()
        if _s in (question or '') or (_short and _short in (question or '')):
            out['shop'] = _s

            if str(out.get('keywords') or '').strip():
                out['keywords'] = ''
    for k in ('exclude_tags', 'avoid'):
        a = [x for x in str(out.get(k) or '').split(',') if x.strip()]
        b = [x for x in str(rule.get(k) or '').split(',') if x.strip()]
        out[k] = ','.join(a + [x for x in b if x not in a])
    return out

def _shop_thumb_map():
    """档口 → 招牌/实拍图 URL（菜品无图时兜底）。db.query 返回 dict 行。"""
    try:
        return {r['shop']: ('/' + r['thumb']) for r in db.query('SELECT shop, thumb FROM shop')
                if r.get('thumb')}
    except Exception:
        return {}

def _items_from_out(out, shop_thumbs):
    """优先用 dishes_json（真实行，带图带导航），无则退回 display_text 解析。"""
    items = []
    if out['has_result']:
        try:
            for d in json.loads(out.get('dishes_json') or '[]'):
                img = ('/dishimg/' + d['img']) if d.get('img') else shop_thumbs.get(d.get('shop') or '', '')
                band = R.band_of_price(d.get('price')) or ''
                items.append({
                    'dish_id': d.get('dish_id'), 'name': d.get('name') or '',
                    'spec': d.get('spec') or '', 'price': d.get('price'), 'price_band': band, 'img': img,
                    'shop': d.get('shop') or '', 'loc': (d.get('canteen') or '') + (d.get('floor') or ''),
                    'campus': d.get('campus') or '', 'canteen': d.get('canteen') or '',
                    'floor': d.get('floor') or '',
                })
        except Exception:
            items = []
    if items:
        return items

    if not out['has_result']:
        return []
    for ln in out['display_text'].split('\n'):
        parts = ln.split(' · ', 2)
        loc, shop, rest = parts if len(parts) == 3 else ('', '', ln)
        name, price = (rest.rsplit(' ', 1) + ['—'])[:2] if ' ' in rest else (rest, '—')
        items.append({'dish_id': None, 'name': name, 'price_band': price,
                      'img': shop_thumbs.get(shop, ''), 'shop': shop, 'loc': loc,
                      'campus': '', 'canteen': '', 'floor': ''})
    return items

_LOC_Q_RE = re.compile(r'哪里|哪儿|哪有|在哪|哪个食堂|哪个窗口|哪个档口|什么地方|哪层|哪楼')

def _loc_answer(items, total=None):
    """模板化位置回答：**每个地点都必须写全「食堂+楼层·档口」**（小吃街没有楼层就写小吃街）。

    例：有这两处：西食堂一楼·杂粮渔粉（水煮鸡蛋 ¥1）；东食堂一楼·基本大伙窗口（水煮鸡蛋 ¥1）
    确定性生成，不经过 LLM —— 所以永远不会漏楼层、不会编地点。
    """
    parts, seen = [], set()
    for it in (items or []):
        loc = (it.get('loc') or '').strip() or (it.get('canteen') or '').strip()
        shop = (it.get('shop') or '').strip()
        key = (loc, shop)
        if not loc or key in seen:
            continue
        seen.add(key)
        name = (it.get('name') or '').strip()

        try:
            _p = float(it.get('price'))
            price = ('%g元' % _p) if it.get('price') is not None else ''
        except (TypeError, ValueError):
            price = ''
        tail = '（%s%s）' % (name, (' ' + price) if price else '')
        parts.append('%s·%s%s' % (loc, shop or '档口', tail))
        if len(parts) >= 3:
            break
    if not parts:
        return ''
    n = total if isinstance(total, int) and total > 0 else len(parts)
    more = ' 等 %d 处' % n if n > len(parts) else ''
    if len(parts) == 1 and not more:
        return '只有这一处：' + parts[0]
    return '有这几处：' + '；'.join(parts) + more

def _full_resp(q, out, params, ms):
    """完整响应（LLM 理解与纯规则共用同一契约）。"""
    sl = out.get('slots') or {}
    items = _items_from_out(out, _shop_thumb_map())
    _tk = sl.get('top_k')
    if _tk:                                   
        items = items[:_tk]
    resp = {
        'ok': True, 'q': q, 'mode': 'dish',
        'has_result': out['has_result'], 'total_count': out['total_count'],
        'shop_count': out['shop_count'], 'stage': out['stage'],
        'relax_note': out['relax_note'], 'kw_note': out['kw_note'],
        'allergy_note': out['allergy_note'],
        'allergen_excluded': out.get('allergen_excluded', 0),
        'allergen_note_text': out.get('allergen_note_text', ''),
        'avoid_note': out.get('avoid_note', ''),
        'budget_hint': out.get('budget_hint', ''),
        'profile_note': out.get('profile_note', ''),

        'loc_note': out.get('loc_note', ''),
        'breakfast_note': out.get('breakfast_note', ''),
        'llm_note': '',
        'engine': out.get('engine', 'rule'),
        'top_k': _tk,                              
        'llm_fallback': out.get('llm_fallback', 'off'),

        'pick_source': out.get('pick_source', 'rule'),
        'llm_slots': out.get('llm_slots') or {},
        'slots_text': out.get('slots_text', ''),
        'budget_bands': R.budget_bands(sl) if sl else [],
        'budget_label': R.budget_label(sl) if sl else '',
        'normalized': {'canteen': (sl.get('canteen_q') or '') if sl else '',
                       'floor': (sl.get('floor_q') or '') if sl else '',
                       'budget': (sl.get('budget') or '') if sl else '',
                       'budget_lo': (sl.get('budget_lo') if sl else None),
                       'budget_around': bool(sl.get('budget_around')) if sl else False,
                       'tags': (sl.get('tags') or []) if sl else [],
                       'exclude': (sl.get('exclude') or []) if sl else [],
                       'avoid_allergen': (sl.get('avoid_allergen') or []) if sl else []},
        'items': items,
        'reply_text': nlu.compose_reply(out),

        'reason': '' if out['has_result'] else (out.get('reason') or R.NO_HIT_TEXT),
        'ms': ms,
    }

    try:
        from services import llm_intro
        want = str(params.get('intro') or '1').strip() not in ('0', 'false', 'no', 'off')
        resp['llm_intro'] = llm_intro.compose(q, out, items, enabled=want)
    except Exception as e:                                   
        resp['llm_intro'] = ''
        sys.stderr.write('[intro] 调用失败：%s: %s\n' % (type(e).__name__, e))

    if not resp.get('llm_intro') and items and _LOC_Q_RE.search(q or ''):
        resp['llm_intro'] = _loc_answer(items, resp.get('total_count'))

    try:
        _session_ctx_put(str(params.get('session') or '')[:64], out.get('slots') or {})
    except Exception:
        pass
    return resp

def _window_mode(q, slots, t0, llm_state, llm_slots, scope='window', top_k=None,
                 only_window=False):
    """窗口层（商家层）推荐 —— 2026-10-13 升级为"两层结构"的第一层。

    变更（用户决策）：
      ① **开放问法也走窗口榜**。以前触发条件是"必须有位置"，于是
         「有什么吃的没 / 今天吃什么」这类没有任何位置的问法永远进不来，
         直接掉到菜品层甩 1749 道菜 —— 这正是"推荐太底层、太僵硬"的根源。
         现在只要**没有菜品级偏好**（标签/忌口/预算/关键词/便宜）就走这一层。
      ② **无位置时按食堂分组**：东食堂 / 西食堂 / 小吃街 各给人气最高的几个窗口，
         回答的是"去哪吃"而不是"吃哪道"。
      ③ 每张窗口卡带**招牌菜**（sig）、**价格带**、**人气或兜底排序依据**。
      ④ 排序改成**综合分**（见 _rank_key）——因为 124 个档口里只有 63 个有
         外卖人气数据，只按人气排会把另外 61 个（含东食堂三楼）全部沉底。
         榜单说明文案随之改成"综合人气与菜品丰富度排序"，不再谎称"按人气排"。
    """
    canteen_filter = slots.get('canteen_q') or (
        slots.get('building_pref') if slots.get('building') else None)
    breakfast_now = R._in_breakfast_window()
    campus_q = slots.get('campus_q') or '鄠邑校区'
    try:
        sql = ('SELECT shop, canteen, floor, thumb, sold_total, sold_verified, signature, hours, '
               'dish_count_rec, price_min, price_max FROM shop WHERE campus=?')
        args = [campus_q]
        if canteen_filter:
            sql += ' AND canteen=?'
            args.append(canteen_filter)
        if slots.get('floor_q'):
            sql += ' AND floor=?'
            args.append(slots['floor_q'])
        if breakfast_now:
            sql += (' AND shop IN (SELECT shop FROM dish '
                    "WHERE is_recommendable=1 AND tags_str LIKE '%早餐%')")
        shops = db.query(sql, args) or []
    except sqlite3.OperationalError as e:
        sys.stderr.write('[warn] 窗口榜单查询失败，已回落菜品推荐：%s\n' % e)
        shops = []
    if not shops:
        return None

    def _pop(r):
        """参与排序的人气：不可信就当没有（返回 None）。"""
        return r['sold_total'] if (r.get('sold_verified') and r.get('sold_total') is not None) else None

    n_verified = sum(1 for r in shops if _pop(r) is not None)
    pure_popular = (n_verified == len(shops))
    max_sold = max([_pop(r) or 0 for r in shops] or [1]) or 1
    max_dish = max([r['dish_count_rec'] or 0 for r in shops] or [1]) or 1

    _seed_w = slots.get('seed_key') or ''
    _sess_w = slots.get('session_seen') or set()
    _canteen_w = (slots.get('profile') or {}).get('canteen_w') or {}
    _epm = slots.get('exp_pen_map')

    def _rank_key(r):
        if pure_popular:
            base = (_pop(r) or 0) / float(max_sold)
        else:
            sold = (_pop(r) or 0) / float(max_sold)
            rich = (r['dish_count_rec'] or 0) / float(max_dish)
            base = 0.6 * sold + 0.4 * rich
        if _epm is not None:                                   # 登录用户：曝光按人隔离
            pen = _epm.get('shop:%s' % r['shop'], 0.0)
        else:
            pen = exposure.penalty('shop:%s' % r['shop'])
        base += 0.25 * _canteen_w.get(r.get('canteen') or '', 0.0)   # 常去食堂加权（画像加分项）
        sess_pen = 1.0 if r['shop'] in _sess_w else 0.0
        return (-(base - 0.5 * pen - 0.9 * sess_pen
                  + 0.15 * exposure.jitter(_seed_w, r['shop'])), r.get('shop') or '')

    shops.sort(key=_rank_key)

    sold_rank = {}
    for i, r in enumerate(sorted([x for x in shops if _pop(x) is not None],
                                 key=lambda x: (-(_pop(x) or 0), x.get('shop') or '')), 1):
        sold_rank[r['shop']] = i

    thumbs = _shop_thumb_map()
    per_canteen = 12 if canteen_filter else 4      
    CANTEEN_ORDER = ('东食堂', '西食堂', '小吃街')

    NON_SIGNATURE = ('米饭', '白饭', '大米饭', '米饭菜', '方便面', '炸干', '黄金豆')

    def _card(r):
        sig = (r.get('signature') or '').split('、')[0]

        if sig and R._is_burger_shop(r.get('shop')) and R._is_standalone_drink({'name': sig}):
            sig = ''

        if sig and (sig.startswith('加') or R.EGG_SNACK_RE.fullmatch(sig)
                    or sig in R.STAPLE_SMALL_EXACT or sig.endswith('包')
                    or sig in NON_SIGNATURE):
            sig = ''

        band = ''

        rank = sold_rank.get(r['shop'])
        pop = ('人气第 %d' % rank) if rank else (
            ('约 %s 道菜' % r['dish_count_rec']) if r.get('dish_count_rec') else '点开看菜')
        return {
            'kind': 'window', 'dish_id': None,
            'name': r['shop'], 'spec': '',
            'rank_text': pop,                          
            'price_band': '',                          
            'sold_total': r.get('sold_total'),          
            'pop_rank': rank,
            'img': thumbs.get(r['shop'], ''),
            'shop': r['shop'], 'loc': (r['canteen'] or '') + (r['floor'] or ''),
            'campus': campus_q, 'canteen': r['canteen'] or '', 'floor': r['floor'] or '',
            'sig': ('招牌 ' + sig) if sig else (r.get('hours') or '点开看菜品和价格'),
            'band': band, 'group': r['canteen'] or '',
        }

    if top_k:                                 
        shown = shops[:int(top_k)]
    elif canteen_filter:
        shown = shops[:per_canteen]
    else:

        shown, seen = [], {}
        for r in shops:
            k = r['canteen'] or ''
            seen[k] = seen.get(k, 0) + 1
            if seen[k] <= per_canteen:
                shown.append(r)
        shown.sort(key=lambda r: (CANTEEN_ORDER.index(r['canteen'])
                                  if r['canteen'] in CANTEEN_ORDER else 9, _rank_key(r)))
    items = [_card(r) for r in shown]

    dish_cards = []
    _dish_shops = [] if only_window else (
        shown[:max(1, min(int(top_k), 3))] if top_k else shown[:3])
    try:
        for r in _dish_shops:

            _cand = db.query(
                'SELECT dish_id, name, price, spec, img, sold, category FROM dish WHERE shop=? '
                'AND is_recommendable="1" ORDER BY price ASC LIMIT 30', [r['shop']]) or []
            if R._is_burger_shop(r.get('shop')):

                _cand = [d for d in _cand if not R._is_standalone_drink(d)]
                _cand.sort(key=lambda d: (
                    0 if R._is_burger_staple(d) else 1,
                    d.get('price') if d.get('price') is not None else 999.0))
            def _dscore(_d):
                _did = _d.get('dish_id')
                return (exposure.jitter(_seed_w, _did)
                        - 0.5 * (exposure.penalty('dish:%s' % _did) if _did else 0.0))
            _cand = sorted(_cand, key=lambda d: (
                0 if (R._is_burger_shop(r.get('shop')) and R._is_burger_staple(d)) else 1,
                -_dscore(d)))
            _pick = [d for d in _cand if d.get('sold') is None][:1]      
            for d in _cand:
                if len(_pick) >= 2:
                    break
                if d not in _pick:
                    _pick.append(d)
            for drow in _pick[:2]:
                dish_cards.append({
                    'kind': 'dish', 'dish_id': drow.get('dish_id'),
                    'name': drow['name'], 'spec': drow.get('spec') or '',
                    'price': drow.get('price'),
                    'price_band': R.band_of_price(drow.get('price')) or '',
                    'img': ('/dishimg/' + drow['img']) if drow.get('img') else thumbs.get(r['shop'], ''),
                    'shop': r['shop'], 'loc': (r['canteen'] or '') + (r['floor'] or ''),
                    'campus': campus_q, 'canteen': r['canteen'] or '', 'floor': r['floor'] or '',
                    'from_window': True, 'group': 'dish',
                })
    except sqlite3.OperationalError as e:
        sys.stderr.write('[warn] 窗口层取菜失败（只影响第二层）：%s\n' % e)
    items = items + dish_cards

    _rot = '，并按新鲜度轮换' if _seed_w else ''
    basis = (('按外卖平台人气排序' if n_verified == len(shops)
              else '综合人气与菜品丰富度排序') + _rot)
    loc_name = (canteen_filter or '') + (slots.get('floor_q') or '')
    bld = slots.get('building')
    loc_prefix = ('你在%s号楼，离%s更近——' % (bld, canteen_filter)) if (
        bld and not slots.get('canteen_q') and canteen_filter) else ''
    ms = int((time.time() - t0) * 1000)
    ranked = '\n'.join(
        '%d. %s%s' % (i, r['shop'],
                      ('（人气第 %s）' % sold_rank[r['shop']]) if r['shop'] in sold_rank else '')
        for i, r in enumerate(shown[:5], 1))
    where = loc_name or '全校'

    dish_lines = '\n'.join(
        '%d. %s%s — %s' % (i, c['name'],
                            ((' %g元' % c['price']) if c.get('price') is not None else ''),
                            c['shop'])
        for i, c in enumerate(dish_cards, 1))
    _more_like = bool(_REL_MORE_RE.search(q or ''))
    _explicit_qty = bool(_EXPLICIT_QTY_RE.search(q or ''))
    if top_k:
        if _more_like:
            head = '再给你挑 %d 个窗口（%s）：' % (int(top_k), basis)
        elif _explicit_qty:
            head = '先看窗口（你要的 %d 个，%s）：' % (int(top_k), basis)
        else:
            head = '先看窗口（给你挑 %d 个，%s）：' % (int(top_k), basis)
    else:
        head = ('先看窗口（%s）：' % basis)
    if only_window:
        reply = ('%s%s\n%s' % (loc_prefix, head, ranked))
    else:
        reply = ('%s%s\n%s\n\n再看菜：\n%s'
                 % (loc_prefix, head, ranked,
                    dish_lines or '（这几个窗口暂无可推荐菜品）'))
    if breakfast_now:
        reply = ('现在是早餐时段（6:30–9:30），只列了供应早餐的窗口。\n' + reply)
    return {
        'ok': True, 'q': q, 'mode': 'window',
        'layers': ['window'] if only_window else ['window', 'dish'],   
        'window_count': len(shown), 'dish_count': len(dish_cards),
        'top_k': int(top_k) if top_k else None,        
        'has_result': True, 'total_count': len(shops), 'shop_count': len(shops),
        'stage': 0, 'relax_note': '', 'kw_note': '', 'allergy_note': False,
        'allergen_excluded': 0, 'allergen_note_text': '', 'avoid_note': '',
        'budget_hint': '',
        'loc_note': loc_prefix.rstrip('—').strip(),
        'breakfast_note': '现在是早餐时段（6:30–9:30），只列了供应早餐的窗口。' if breakfast_now else '',
        'profile_note': '',

        'llm_note': '',
        'engine': 'llm_slots' if llm_state == 'used' else 'rule',
        'llm_fallback': llm_state, 'llm_slots': llm_slots or {},
        'slots_text': R._slots_text(slots),
        'budget_bands': R.budget_bands(slots),
        'budget_label': R.budget_label(slots),
        'normalized': {'canteen': slots.get('canteen_q') or '',
                       'floor': slots.get('floor_q') or '',
                       'budget': slots.get('budget') or '',
                       'budget_lo': slots.get('budget_lo'),
                       'budget_around': bool(slots.get('budget_around')),
                       'tags': [], 'exclude': [], 'avoid_allergen': []},
        'items': items, 'reply_text': reply, 'reason': '', 'ms': ms,

        'rank_basis': basis, 'rank_mixed': n_verified != len(shops),
        'scope': scope,

        'llm_intro': '',
    }

def _chat_response(q, t0, trigger, text_override=''):
    """闲聊层响应（mode='chat'，不带任何窗口/菜品卡）。trigger 记录是谁判的，便于核对。"""
    from services import llm_chat
    text, chat_src = llm_chat.reply(q)
    if text_override:                     
        text, chat_src = text_override, 'rule'
    ms = int((time.time() - t0) * 1000)
    try:
        db.log_qa(q, '闲聊/介绍（%s/%s）' % (trigger, chat_src), 0, '', 0, ms)
    except Exception:
        pass
    return {
        'ok': True, 'q': q, 'mode': 'chat', 'layers': [],
        'chat_text': text, 'chat_source': chat_src, 'chat_trigger': trigger,
        'has_result': False, 'total_count': 0, 'shop_count': 0,
        'window_count': 0, 'dish_count': 0, 'stage': 0,
        'relax_note': '', 'kw_note': '', 'allergy_note': False,
        'allergen_excluded': 0, 'allergen_note_text': '',
        'avoid_note': '', 'budget_hint': '', 'llm_note': '',
        'loc_note': '', 'breakfast_note': '', 'profile_note': '',
        'engine': 'chat', 'llm_fallback': 'off', 'llm_slots': {}, 'llm_intro': '',
        'slots_text': '', 'normalized': {'canteen': '', 'floor': '', 'budget': '',
                                         'tags': [], 'exclude': [], 'avoid_allergen': []},
        'items': [], 'reply_text': text, 'reason': '', 'ms': ms,
    }

_NON_DISH_KW = ('道菜', '个菜', '份菜', '点菜', '些菜', '来菜', '菜品', '菜就行', '就行',
                '一个菜', '一道菜', '两个菜', '三个菜', '个窗口', '个档口')

def _sanitize_kw(kw):
    """把"数量/泛指"混进来的假菜名剔掉（约束层，永远生效）。"""
    if not kw:
        return kw
    if isinstance(kw, (list, tuple)):
        return [k for k in kw if not any(b in k for b in _NON_DISH_KW)]
    return '' if any(b in str(kw) for b in _NON_DISH_KW) else kw

def _canon_shop(name):
    """把任意档口写法归一到库里的**规范全名**；对不上就返回 ''（约束层，防 LLM 编档口）。

    2026-10-15（D）实测：问「米渣渣咖喱饭有啥」时 LLM 返回 shop='米渣渣咖喱饭'，
    而库里叫「米渣渣咖喱饭·特色米饭」→ 精确过滤必然 0 结果。这里按
    「全名 → · 前短名 → 唯一包含」三级归一到规范名。
    """
    s = (name or '').strip()
    if not s:
        return ''
    try:
        names = list(nlu._load_shop_dict())
    except Exception:                                     
        return s
    if not names:
        return s
    if s in names:
        return s
    for n in names:                                       
        if n.split('·')[0].split('（')[0].strip() == s:
            return n
    hit = [n for n in names if s in n]                    
    if len(hit) == 1:
        return hit[0]
    if hit:                                               
        return min(hit, key=len)
    return ''

_SESSION_SEEN = {}
_SESSION_ORDER = []

_SESSION_CTX = {}
_SESSION_CTX_ORDER = []
_CTX_SKIP = {'seed_key', 'batch', 'session_seen', 'session_id', 'slots_text',
             'top_k', 'modifier', 'kw_note', 'relax_note',
             'pref', 'pref_kw', 'profile', 'profile_note', 'exp_pen_map', 'uid'}

def _session_seen(sid):
    return _SESSION_SEEN.get(sid) or set()

def _ctx_has(v):
    return v not in (None, '', [], (), False)

def _session_ctx_put(sid, slots):
    if not sid or not slots:
        return
    keep = {k: v for k, v in slots.items() if k not in _CTX_SKIP}
    _SESSION_CTX[sid] = keep
    if sid in _SESSION_CTX_ORDER:
        _SESSION_CTX_ORDER.remove(sid)
    _SESSION_CTX_ORDER.append(sid)
    while len(_SESSION_CTX_ORDER) > 200:
        _SESSION_CTX.pop(_SESSION_CTX_ORDER.pop(0), None)

def _merge_session_ctx(sid, slots):
    """把上一轮条件接到本轮；本轮明确说了的条件优先。"""
    prev = _SESSION_CTX.get(sid) or {}
    if not sid or not prev:
        return slots
    out = dict(slots)
    new_canteen = slots.get('canteen_q')
    new_floor = slots.get('floor_q')
    new_shop = slots.get('shop_q')
    new_budget = slots.get('budget')

    if new_canteen and prev.get('canteen_q') and new_canteen != prev.get('canteen_q'):
        out['floor_q'] = None
        out['shop_q'] = None
    elif new_floor and not new_shop:
        out['shop_q'] = None
    if new_budget is not None:

        for k in ('budget_lo', 'budget_around'):
            out[k] = slots.get(k)
    for k in ('campus_q', 'canteen_q', 'floor_q', 'shop_q', 'building',
              'building_pref', 'budget', 'budget_lo', 'budget_around', 'cheap'):
        if not _ctx_has(out.get(k)) and _ctx_has(prev.get(k)):
            out[k] = prev[k]

    if not _ctx_has(out.get('tags')) and _ctx_has(prev.get('tags')):
        out['tags'] = prev['tags']
    if not _ctx_has(out.get('kw')) and _ctx_has(prev.get('kw')):
        out['kw'] = prev['kw']

    for k in ('exclude', 'avoid_raw', 'avoid_allergen', 'avoid_unmapped'):
        merged = list(out.get(k) or [])
        for x in (prev.get(k) or []):
            if x not in merged:
                merged.append(x)
        out[k] = merged
    return out

def _session_touch(sid, ids):
    if not sid or not ids:
        return
    s = _SESSION_SEEN.setdefault(sid, set())
    s.update(ids)
    if sid in _SESSION_ORDER:
        _SESSION_ORDER.remove(sid)
    _SESSION_ORDER.append(sid)
    while len(_SESSION_ORDER) > 200:
        _SESSION_SEEN.pop(_SESSION_ORDER.pop(0), None)

def _bump_exposure(items, sid='', uid=None):
    """把真正展示出去的条目记一笔曝光（窗口记档口、菜品记 dish_id）。

    登录用户（uid）不吃全局曝光：他们的"已推过"由自己的 ask 事件（payload.shown）
    推导（services/profile.user_penalty_map），全局表就不掺和了。
    """
    if not items:
        return
    shops = ['shop:%s' % x['name'] for x in items if x.get('kind') == 'window' and x.get('name')]
    dishes = ['dish:%s' % x['dish_id'] for x in items if x.get('dish_id')]
    if not uid:
        exposure.bump(shops + dishes)
    _session_touch(sid, {x['name'] for x in items if x.get('kind') == 'window' and x.get('name')}
                   | {x['dish_id'] for x in items if x.get('dish_id')})


def _shown_refs(items):
    """本次响应真正展示的 ref_id 列表（写进 ask 事件，供按人曝光隔离）。"""
    refs = []
    for x in items or []:
        did = x.get('dish_id')
        if did:
            refs.append('dish:%s' % did)
        elif x.get('name'):
            refs.append('shop:%s' % x['name'])
    return refs[:24]


def _log_user_ask(uid, q, resp):
    """登录用户的提问事件落库（后台线程；失败静默，不影响主流程）。"""
    if not uid:
        return
    items = resp.get('items') or []

    def _bg():
        try:
            from services import profile as PROF
            canteen = next((x.get('canteen') for x in items if x.get('canteen')), '')
            campus = next((x.get('campus') for x in items if x.get('campus')), '')
            PROF.record_ask(uid, q, _shown_refs(items), canteen=canteen, campus=campus)
        except Exception:
            pass
    threading.Thread(target=_bg, daemon=True).start()

_CN_DIGIT = {'一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5, '六': 6,
             '七': 7, '八': 8, '九': 9, '十': 10,
             '1': 1, '2': 2, '3': 3, '4': 4, '5': 5, '6': 6, '7': 7, '8': 8, '9': 9}
_FLOOR_NUM = {1: '一楼', 2: '二楼', 3: '三楼'}
_CF_CACHE = None

def _db_canteen_floors():
    """库内真实存在哪些食堂、各自有哪些楼层（缓存；只用于边界守卫的判断）。"""
    global _CF_CACHE
    if _CF_CACHE is not None:
        return _CF_CACHE
    out = {}
    try:
        for r in db.query("SELECT DISTINCT canteen, floor FROM shop"):
            c = (r['canteen'] or '').strip()
            if not c:
                continue
            out.setdefault(c, set())
            f = (r['floor'] or '').strip()
            if f:
                out[c].add(f)
    except Exception as e:                                   
        sys.stderr.write('[guard] 读取食堂/楼层失败：%s\n' % e)
    _CF_CACHE = {c: sorted(v) for c, v in out.items()}
    return _CF_CACHE

def _nonexistent_restaurant(q):
    """「67餐厅 / 六十七餐厅」这类库里不存在的餐厅：先澄清，不要直接推菜。"""
    m = re.search(r'(?:第\s*)?([一二两三四五六七八九十百零\d]{1,4})\s*号?\s*餐厅', q or '')
    if not m:
        return ''
    n = m.group(1)
    if n in ('一', '二', '两', '1', '2'):     
        return ''
    return '学校没有%s餐厅，只有西食堂、东食堂和小吃街' % n

def _edge_guard(q, slots):
    """边界守卫：问不存在的地点 / 非法预算时返回一句**如实说明**；否则返回 None。"""
    q = q or ''
    cf = _db_canteen_floors()

    if not slots.get('canteen_q'):
        for m in re.finditer(r'第?\s*([一二两三四五六七八九十\d]{1,3})\s*食堂', q):
            n = m.group(1)
            if n in cf:                                      
                continue
            return '学校只有%s（没有%s食堂）' % ('、'.join(cf) or '食堂', n)

    fat = re.search(r'(?:第\s*)?([一二两三四五六七八九十]|\d{1,2})\s*(?:楼|层)(?![盘梯道])', q)
    if fat:

        ctx = any(k in q for k in ('食堂', '餐厅', '档口', '窗口', '小吃街')) \
            or slots.get('canteen_q')
        num = _CN_DIGIT.get(fat.group(1))
        if ctx and num:
            canteen = slots.get('canteen_q')
            if not canteen:
                canteen = next((c for c in cf if c in q), None)
            if num > 3:
                return '学校食堂只有一楼、二楼、三楼（小吃街没有楼层）'
            floor = _FLOOR_NUM[num]
            if canteen and canteen in cf:
                floors = cf[canteen]
                if not floors:
                    return '%s没有楼层，直接看档口就行' % canteen
                if floor not in floors:
                    return '%s只有%s' % (canteen, '、'.join(floors))

    b = slots.get('budget')
    if b is not None:
        try:
            bf = float(b)
        except (TypeError, ValueError):
            bf = None
        if bf is not None and bf <= 0:
            return '预算要填大于 0 元的数（比如 5、10、15）'
    return None

def handle(params):
    t0 = time.time()

    q = str(params.get('q') or '').strip()
    explicit = any(params.get(k) for k in SLOT_KEYS)

    _sid0 = str(params.get('session') or '')[:64]
    if _sid0 and re.search(r'重新开始|重新来|清空条件|清空上下文|重置|换个话题', q or ''):
        _SESSION_CTX.pop(_sid0, None)

    _bad_rest = _nonexistent_restaurant(q)
    if _bad_rest:
        return _chat_response(q, t0, 'rule', text_override=_bad_rest)

    if q and not explicit and _CORRECTION_RE.search(q):
        return _chat_response(q, t0, 'rule',
                              text_override=random.choice(_CORRECTION_REPLIES))

    from services import llm_chat

    if q and not explicit and llm_chat.is_strong_chat(q):
        return _chat_response(q, t0, 'rule')

    llm_state, llm_slots, intent = 'off', None, None
    if q and not explicit:
        if llm_fallback.available():
            fresh = llm_fallback.extract_slots(q)
            if fresh is not None:
                llm_slots, llm_state = fresh, 'used'
                intent = fresh.get('_intent')

                raw = _merge_slots(fresh, nlu.parse_question(q), q)
            else:                                   
                llm_state = 'failed'
                raw = nlu.parse_question(q)
        else:
            llm_state = 'no_key'
            raw = nlu.parse_question(q)
    else:
        raw = nlu.parse_question(q) if q else {k: '' for k in SLOT_KEYS}
    _cs = _canon_shop(raw.get('shop'))                    
    if raw.get('shop') and _cs != raw.get('shop'):
        if _cs:
            raw['shop'] = _cs
        else:

            _rs = _canon_shop(nlu.parse_question(q).get('shop')) if q else ''
            raw['shop'] = _rs or ''
    # 2026-11：LLM 路径也补「直接命中词」——LLM 只给标签，用户原话里的食物词
    # （馒头/稀饭/馍…）从 lexicon 里再扫一遍，打分时直接命中菜名的排前面
    if q:
        try:
            _scan = lexicon.scan_query(q)
            _t = [x for x in str(raw.get('terms') or '').split(',') if x]
            for _x in _scan['terms']:
                if _x not in _t:
                    _t.append(_x)
            if _t:
                raw['terms'] = ','.join(_t)
            _ft = [x for x in str(raw.get('food_terms') or '').split(',') if x]
            for _x in _scan['food_terms']:
                if _x not in _ft:
                    _ft.append(_x)
            if _ft:
                raw['food_terms'] = ','.join(_ft)
        except Exception:
            pass
    _gone = _drop_unevidenced_exclude(raw, q)              
    if _gone:
        sys.stderr.write('[slots] LLM 把品类抽成了排除，已撤销：%s\n' % _gone)
    raw['keywords'] = _sanitize_kw(raw.get('keywords'))   
    raw.setdefault('campus', '')

    if q and not explicit and llm_chat.is_hours_q(q):
        return _chat_response(q, t0, 'rule', text_override=llm_chat.hours_reply())

    if q and not explicit and intent == 'chat':
        if llm_chat.has_food_signal(q):
            sys.stderr.write('[chat] LLM 判 chat 但句子有明显食物信号，改走推荐：%s\n' % q[:40])
        else:
            return _chat_response(q, t0, 'llm')

    if q and not explicit and intent is None and llm_chat.is_chat(q, params):
        return _chat_response(q, t0, 'rule-fallback')

    for k in SLOT_KEYS:
        if params.get(k):
            raw[k] = params[k]

    if q and re.search(r'便宜|实惠|低价|省钱', q):
        raw['cheap'] = '1'

    slots = S.normalize_slots(raw)

    _sid = str(params.get('session') or '')[:64]
    if _sid and re.search(r'重新开始|重新来|清空条件|清空上下文|重置|换个话题', q or ''):
        _SESSION_CTX.pop(_sid, None)
    slots = _merge_session_ctx(_sid, slots)

    if re.search(r'不限预算|预算不限|别管预算|预算随便', q or ''):
        for _k in ('budget', 'budget_lo', 'budget_around'):
            slots[_k] = None
        if _sid in _SESSION_CTX:
            for _k in ('budget', 'budget_lo', 'budget_around'):
                _SESSION_CTX[_sid].pop(_k, None)

    import datetime as _dt
    _batch = str(params.get('batch') or '1')[:6]
    slots['seed_key'] = exposure.make_seed(q, _dt.date.today().isoformat(), _batch)
    slots['batch'] = _batch
    slots['session_seen'] = _session_seen(_sid)
    slots['session_id'] = _sid

    # —— 账号画像（2026-11）：登录后推荐"越吃越懂你"；未登录 = 原逻辑一行不变 ——
    _uid = None
    try:
        from services import profile as PROF
        from services import sb_client
        _uid = sb_client.verify_token(params.get('_auth') or '')
        if _uid:
            _prof = PROF.load_profile(_uid)
            if _prof:
                slots['profile'] = {'canteen_w': _prof['canteen_w'],
                                    'shop_w': _prof['shop_w'],
                                    'price_p': _prof['price_p']}
                if _prof['tags']:
                    slots['pref'] = _prof['tags']        # 接 recommend() 预留的画像通道
                slots['profile_note'] = _prof['note']
            slots['exp_pen_map'] = PROF.user_penalty_map(_uid)   # 曝光按人隔离
            slots['uid'] = _uid
    except Exception as e:
        sys.stderr.write('[profile] 画像加载失败（按匿名处理）：%s: %s\n' % (type(e).__name__, e))
        _uid = None

    _guard_text = _edge_guard(q, slots)
    if _guard_text:
        out = R._empty(slots, _guard_text)
        resp = _full_resp(q, out, params, int((time.time() - t0) * 1000))
        try:
            db.log_qa(q or '(边界守卫)', out.get('slots_text', ''), 0, '', 0, resp.get('ms', 0))
        except Exception:
            pass
        resp['guard'] = 'location_or_budget'
        return resp

    scope = None
    if llm_state == 'used' and llm_slots:
        _s = str(llm_slots.get('scope') or '').strip().lower()
        if _s in ('window', 'dish', 'shop'):
            scope = _s

    _llm_evidence = {}
    _llm_conf = None
    if llm_state == 'used' and llm_slots:
        _ev = llm_slots.get('evidence')
        if isinstance(_ev, dict):
            _llm_evidence = _ev
        _llm_conf = llm_slots.get('confidence') or None

    _q_unit = nlu.extract_top_k_unit(q)
    if _q_unit == 'dish':
        scope = 'dish'
    elif _q_unit == 'window' and not scope:
        scope = 'window'

    _no_dish_pref = not (slots.get('tags') or slots.get('exclude') or slots.get('budget')
                         or slots.get('kw') or slots.get('avoid_raw')
                         or slots.get('avoid_allergen') or slots.get('cheap')
                         or slots.get('shop_q'))

    if _no_dish_pref:
        _fb_tags = nlu.fallback_food_tags(q)
        if _fb_tags:
            slots['tags'] = _fb_tags
            _no_dish_pref = False
    _want_window = (scope == 'window') if scope else True

    _only_window = bool(re.search(
        r'只(?:推荐|看|要|给|列|显示)?\s*(?:窗口|档口)'
        r'|仅(?:推荐|看|要|给)?\s*(?:窗口|档口)'
        r'|(?:窗口|档口)(?:就行|即可|就好)'
        r'|不要(?:推荐)?(?:菜|菜品)|不看菜', q or ''))

    _llm_only = str((llm_slots or {}).get('only_window') or raw.get('only_window') or '').strip().lower()
    if _llm_only in ('1', 'true', 'yes', '是', '对'):
        _only_window = True
    if _q_unit == 'window':
        # 用户说“窗口/档口”时默认窗口层，但仍带回菜品层，
        # 前端用同一气泡里的 窗口/菜品 开关切换。
        _want_window = True
        _no_dish_pref = True
    if _no_dish_pref and _want_window and slots.get('campus_ok', True):
        w = _window_mode(q, slots, t0, llm_state, llm_slots, scope=scope or 'window',
                         top_k=slots.get('top_k'), only_window=_only_window)
        if w is not None:
            try:
                db.log_qa(q or '(位置问法)', w.get('slots_text', ''), w.get('total_count', 0),
                          (w.get('items') or [{}])[0].get('shop', ''), 0, w.get('ms', 0),
                          evidence=_llm_evidence, confidence=_llm_conf)
            except Exception:
                pass

            try:
                from services import llm_intro
                want = str(params.get('intro') or '1').strip() not in ('0', 'false', 'no', 'off')
                w['llm_intro'] = llm_intro.compose(q, w, w.get('items') or [], enabled=want)
            except Exception as e:                           
                sys.stderr.write('[intro] 窗口模式调用失败：%s: %s\n' % (type(e).__name__, e))
            _bump_exposure(w.get('items') or [], _sid, uid=_uid)
            w['profile_note'] = slots.get('profile_note') or ''
            _log_user_ask(_uid, q, w)
            w['batch'] = _batch
            w['sort_note'] = '按匹配度与人气排序，并让最近没推过的轮上来（同日同问结果一致）'
            return w

    rows = db.recommendable_rows(campus=slots.get('campus_q') or None)     

    picker = None
    try:
        if slots.get('shop_q') and R._is_burger_shop(slots.get('shop_q')):
            picker = None          
        elif llm_state == 'used' and str(params.get('picker') or '1').strip() \
                not in ('0', 'false', 'no', 'off'):
            from services import llm_pick
            picker = llm_pick.make_picker(q, slots)
    except Exception as e:                                                
        sys.stderr.write('[pick] 初始化失败，回落规则排序：%s: %s\n' % (type(e).__name__, e))
        picker = None
    out = R.recommend(rows, slots, picker=picker)                          
    ms = int((time.time() - t0) * 1000)

    try:
        db.log_qa(q or '(结构化)', out.get('slots_text', ''), out.get('total_count', 0),
                  out.get('top_dish', ''), out.get('allergen_excluded', 0), ms,
                  evidence=_llm_evidence, confidence=_llm_conf)
    except Exception:
        pass          

    if not out['has_result'] and slots.get('shop_q') and slots.get('budget'):
        try:
            r0 = db.query('SELECT name, price FROM dish WHERE shop=? AND is_recommendable=1 '
                          'ORDER BY (price IS NULL), price LIMIT 1', (slots['shop_q'],))
            if r0 and r0[0]['price'] is not None:
                _band = R.band_of_price(r0[0]['price'])
                out['reason'] = ('%s最便宜的也在 %s 档（%s），超出了 %s 元的预算——要不要放宽一点？'
                                 % (slots['shop_q'], _band, r0[0]['name'],
                                    slots['budget']))
        except Exception:
            pass

    if not out['has_result'] and not (out.get('reason') or '').strip():
        out['reason'] = R.NO_HIT_TEXT

    out['llm_fallback'] = llm_state
    out['llm_slots'] = llm_slots or {}
    out['slots'] = slots
    out['engine'] = 'llm_slots' if llm_state == 'used' else 'rule'

    out['llm_note'] = ''
    out['profile_note'] = slots.get('profile_note') or ''
    _resp = _full_resp(q, out, params, ms)
    _bump_exposure(_resp.get('items') or [], _sid, uid=_uid)
    _log_user_ask(_uid, q, _resp)
    _resp['batch'] = _batch
    _resp['sort_note'] = '按匹配度与人气排序，并让最近没推过的轮上来（同日同问结果一致）'
    return _resp
