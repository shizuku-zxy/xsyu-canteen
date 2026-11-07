# -*- coding: utf-8 -*-
"""用户口味画像（2026-11 新增：账号"越吃越懂你"个性化推荐）。

数据流：前端把推荐结果里的菜品/窗口点击 + 每次提问上报到 Supabase user_events
（routers/api_event.py 落库）→ 本模块把近 60 天事件聚合成画像 → 推荐引擎加权。

设计要点：
  · **事件是唯一事实源**，画像现算不落表（改权重不用迁移数据）
  · **时间衰减**：w × 0.5^(距今天数/14)，半衰期 14 天 —— "随着买的饭加深"且老口味会淡忘
  · **冷启动保护**：有效事件 < 3 条不启用画像（新用户与匿名体验完全一致）
  · **缓存**：画像与曝光映射按 user_id 缓存 120s（一次 ask 只打一次 Supabase）
  · **铁律**：画像只做加分项，永不做过滤项；显式意图（点名食堂/预算/忌口）永远压过画像
"""
import datetime
import json
import threading
import time

from dao import db
from services import sb_client

EVENT_DAYS = 60.0          # 画像回看窗口（天）
DECAY_HALF_DAYS = 14.0     # 半衰期
MIN_EVENTS = 3             # 冷启动门槛
CACHE_TTL = 120.0          # 画像/曝光缓存
KIND_W = {'dish_click': 1.0, 'window_click': 0.6, 'ask': 0.3}

_CACHE = {'prof': {}, 'pen': {}}
_LOCK = threading.Lock()


def _now_ts():
    return time.time()


def _parse_ts(v):
    """PostgREST timestamptz → 时间戳（失败返回 None）。"""
    try:
        s = str(v or '').strip()
        if not s:
            return None
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        dt = datetime.datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.timezone.utc)
        return dt.timestamp()
    except Exception:
        return None


def _url_since(days):
    """URL 安全的 UTC 时间戳（isoformat 的 +00:00 在 URL 里会被读成空格 → 400）。"""
    return (datetime.datetime.now(datetime.timezone.utc)
            - datetime.timedelta(days=days)).strftime('%Y-%m-%dT%H:%M:%SZ')


def _fetch_events(user_id):
    """近 60 天事件（倒序，最多 300 条）。失败返回 None（网络/凭证问题 → 无画像）。"""
    q = ('user_id=eq.%s&created_at=gte.%s&order=created_at.desc&limit=300'
         '&select=kind,campus,canteen,floor,shop,dish,spec,price,payload,created_at'
         % (user_id, _url_since(EVENT_DAYS)))
    return sb_client.select('user_events', q, retries=1)


def _norm01(d):
    """权重表 → 0~1（除以最大值）。"""
    if not d:
        return {}
    mx = max(d.values())
    if mx <= 0:
        return {}
    return {k: v / mx for k, v in d.items() if v > 0}


def _decay_weight(ev_ts, kind):
    w = KIND_W.get(kind)
    if not w:
        return 0.0
    age_days = max(0.0, (_now_ts() - ev_ts) / 86400.0)
    return w * (0.5 ** (age_days / DECAY_HALF_DAYS))


def _band_text(price):
    """画像价位 → 展示文案（对齐 recommend.band_of_price 的档位）。"""
    from services import recommend as R
    b = R.band_of_price(price)
    return b or ''


def load_profile(user_id):
    """事件 → 画像 dict（canteen_w / shop_w / tags / price_p / note）；冷启动或失败返回 None。"""
    user_id = str(user_id or '')
    if not user_id:
        return None
    now = _now_ts()
    with _LOCK:
        hit = _CACHE['prof'].get(user_id)
        if hit and hit[0] > now:
            return hit[1]
    events = _fetch_events(user_id)
    prof = None
    if events is not None:
        eff = 0
        canteen_w, shop_w, tag_w = {}, {}, {}
        price_sum, price_n = 0.0, 0
        for ev in events:
            kind = str(ev.get('kind') or '')
            ts = _parse_ts(ev.get('created_at'))
            if ts is None:
                continue
            w = _decay_weight(ts, kind)
            if w <= 0:
                continue
            eff += 1
            canteen = str(ev.get('canteen') or '')
            if canteen:
                canteen_w[canteen] = canteen_w.get(canteen, 0.0) + w
            shop = str(ev.get('shop') or '')
            if shop:
                shop_w[shop] = shop_w.get(shop, 0.0) + w
            # 口味标签：只有菜品点击才反映"口味"（提问里的标签是显式条件，不掺进来）
            if kind == 'dish_click':
                dish = str(ev.get('dish') or '')
                if dish:
                    ts_str = db.dish_tags(dish, ev.get('shop') or None)
                    if ts_str:
                        for t in [x.strip() for x in ts_str.replace('，', ',')
                                  .replace('、', ',').split(',') if x.strip()]:
                            tag_w[t] = tag_w.get(t, 0.0) + w / 3.0   # 一道菜多个标签，均摊
            try:
                p = ev.get('price')
                if p is not None and float(p) > 0:
                    price_sum += float(p) * w
                    price_n += 1
            except (TypeError, ValueError):
                pass
        if eff >= MIN_EVENTS:
            price_p = round(price_sum / price_n, 1) if price_n else None
            tags = dict(sorted(_norm01(tag_w).items(), key=lambda x: -x[1])[:8])
            prof = {
                'canteen_w': _norm01(canteen_w),
                'shop_w': _norm01(shop_w),
                'tags': tags,
                'price_p': price_p,
                'note': _compose_note(_norm01(canteen_w), tags, price_p),
                'events': eff,
            }
    with _LOCK:
        if len(_CACHE['prof']) > 256:
            _CACHE['prof'].clear()
        _CACHE['prof'][user_id] = (now + CACHE_TTL, prof)
    return prof


def _compose_note(canteen_w, tags, price_p):
    """透明可解释：'按你最近的口味：常去东食堂 · 偏好面/辣 · 常点5–10元'。"""
    bits = []
    cs = [c for c, w in sorted(canteen_w.items(), key=lambda x: -x[1]) if w >= 0.25][:2]
    if cs:
        bits.append('常去' + '、'.join(cs))
    tg = [t for t, w in tags.items() if w >= 0.25][:2]
    if tg:
        bits.append('偏好' + '、'.join(tg))
    if price_p:
        band = _band_text(price_p)
        if band:
            bits.append('常点' + band.replace('元', '') + '元')
    if not bits:
        return ''
    return '按你最近的口味：' + ' · '.join(bits)


def user_penalty_map(user_id):
    """按人隔离的曝光惩罚表（登录用户不再吃全局曝光）。

    从近 3 天 ask 事件的 payload['shown'] 里取真正展示过的 ref_id（dish:Dxx / shop:xx），
    惩罚公式对齐 services/exposure.py：age<1 天 = 0；1~3 天线性从 1 → 0。
    失败/无事件返回 {}（等效无惩罚，不影响推荐主流程）。
    """
    user_id = str(user_id or '')
    if not user_id:
        return {}
    now = _now_ts()
    with _LOCK:
        hit = _CACHE['pen'].get(user_id)
        if hit and hit[0] > now:
            return hit[1]
    pen = {}
    q = ("user_id=eq.%s&kind=eq.ask&created_at=gte.%s&order=created_at.desc&limit=60"
         "&select=payload,created_at" % (user_id, _url_since(3.0)))
    rows = sb_client.select('user_events', q, retries=0)
    if rows:
        newest = {}
        for ev in rows:
            ts = _parse_ts(ev.get('created_at'))
            if ts is None:
                continue
            payload = ev.get('payload')
            if not isinstance(payload, (dict, list)):
                try:
                    payload = json.loads(payload) if payload else None
                except Exception:
                    payload = None
            shown = []
            if isinstance(payload, dict):
                shown = payload.get('shown') or []
            elif isinstance(payload, list):
                shown = payload
            for rid in shown:
                if rid:
                    newest[str(rid)] = max(newest.get(str(rid), 0.0), ts)
        for rid, ts in newest.items():
            age = (now - ts) / 86400.0
            if age < 1.0 or age >= 3.0:
                continue
            pen[rid] = max(0.0, 1.0 - (age - 1.0) / 2.0)
    with _LOCK:
        if len(_CACHE['pen']) > 256:
            _CACHE['pen'].clear()
        _CACHE['pen'][user_id] = (now + CACHE_TTL, pen)
    return pen


def record_ask(user_id, q, shown, canteen='', campus=''):
    """记录一次提问事件（后台线程调用；shown = 本次真正展示的 ref_id 列表）。"""
    if not user_id:
        return False
    row = {
        'user_id': user_id, 'kind': 'ask',
        'campus': campus or '', 'canteen': canteen or '',
        'floor': '', 'shop': '', 'dish': '', 'spec': '', 'price': None,
        'payload': {'q': (q or '')[:120], 'shown': [str(x) for x in (shown or [])][:24]},
    }
    ok = sb_client.insert('user_events', row, retries=1)
    _invalidate(user_id, pen_only=False)
    return ok


def insert_click(user_id, kind, campus='', canteen='', floor='', shop='',
                 dish='', spec='', price=None):
    """菜品/窗口点击事件（api_event 上报用，已在上报线程内）。"""
    if kind not in ('dish_click', 'window_click'):
        return False
    row = {
        'user_id': user_id, 'kind': kind,
        'campus': campus or '', 'canteen': canteen or '', 'floor': floor or '',
        'shop': shop or '', 'dish': dish or '', 'spec': spec or '',
        'price': price, 'payload': None,
    }
    ok = sb_client.insert('user_events', row, retries=1)
    _invalidate(user_id, pen_only=False)
    return ok


def clear_user(user_id):
    """清除口味记忆：删该用户全部事件 + 本地缓存。"""
    ok = sb_client.delete_all('user_events', 'user_id=eq.%s' % user_id, retries=1)
    _invalidate(user_id, pen_only=False)
    return ok


def _invalidate(user_id, pen_only=True):
    with _LOCK:
        _CACHE['prof'].pop(user_id, None)
        if not pen_only:
            _CACHE['pen'].pop(user_id, None)
