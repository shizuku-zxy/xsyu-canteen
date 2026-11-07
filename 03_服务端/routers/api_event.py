# -*- coding: utf-8 -*-
"""行为上报接口（登录用户画像的事实来源）。

POST /api/event
  · {action:'clear'}                     → 清除口味记忆（删该用户全部 user_events）
  · {kind:'dish_click'|'window_click',
     campus, canteen, floor, shop, dish, spec, price} → 行为事件落库

鉴权：Authorization: Bearer <access_token>（app.py _params 注入 params['_auth']），
GoTrue 验证（10 分钟缓存）。未登录返回 ok:false，前端静默忽略。
"""
import threading

from services import profile as PROF
from services import sb_client


def handle(params):
    uid = sb_client.verify_token(params.get('_auth') or '')
    import sys as _sys
    _sys.stderr.write('[event] _auth=%r → uid=%r\n' % ((params.get('_auth') or '')[:24], uid))
    if not uid:
        return {'ok': False, 'error': 'not_signed_in'}

    if str(params.get('action') or '').strip() == 'clear':
        ok = PROF.clear_user(uid)
        return {'ok': bool(ok), 'cleared': bool(ok)} if ok \
            else {'ok': False, 'error': 'clear_failed'}

    kind = str(params.get('kind') or '').strip()
    if kind not in ('dish_click', 'window_click'):
        return {'ok': False, 'error': 'bad_kind'}

    def _num(v):
        try:
            f = float(str(v))
            return f if f > 0 else None
        except Exception:
            return None

    row = {
        'kind': kind,
        'campus': str(params.get('campus') or '')[:32],
        'canteen': str(params.get('canteen') or '')[:32],
        'floor': str(params.get('floor') or '')[:32],
        'shop': str(params.get('shop') or '')[:64],
        'dish': str(params.get('dish') or '')[:64],
        'spec': str(params.get('spec') or '')[:32],
        'price': _num(params.get('price')),
    }

    def _bg():
        try:
            PROF.insert_click(uid, **row)
        except Exception:
            pass
    threading.Thread(target=_bg, daemon=True).start()
    return {'ok': True}
