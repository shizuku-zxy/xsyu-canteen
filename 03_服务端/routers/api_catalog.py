
"""路由层 · 目录数据：档口列表 / 档口详情 / 结构化筛选 / 全文检索。

对齐前端界面需要的四件事（两列档口卡、详情页、筛选面板、搜索框）。
"""
from dao import db

def shops(params):
    """档口列表；带 `q` 时走**档口搜索**（分层匹配 + 人气降序，供前端顶层搜索直达档口）"""

    canteen = str(params.get('canteen') or '').strip() or None
    floor = str(params.get('floor') or '').strip() or None
    campus = str(params.get('campus') or '').strip() or None
    q = str(params.get('q') or '').strip()
    if q:
        rows = db.search_shops(q, limit=_safe_int(params.get('limit'), 40))
        return {'ok': True, 'q': q, 'count': len(rows), 'items': rows}
    rows = db.shops(canteen=canteen, floor=floor, campus=campus)
    return {'ok': True, 'count': len(rows), 'items': rows}

def shop(params):
    name = str(params.get('name') or params.get('shop') or '').strip()
    if not name:
        return {'ok': False, 'error': '缺少档口名 name'}
    d = db.shop_detail(name, canteen=(params.get('canteen') or None), floor=(params.get('floor') or None))
    if not d:
        return {'ok': False, 'error': '没有这个档口：%s' % name}
    return {'ok': True, 'shop': d}

def _ints(v):
    return [x for x in (v or '').replace('，', ',').split(',') if x.strip()]

def _safe_int(v, default, lo=1, hi=500):
    """2026-10-13 修：`int(params.get('limit') or 30)` 遇到 ?limit=abc 会抛 ValueError
    → 经 app.py 变成 HTTP 500。参数脏不该让接口挂掉，夹到合法区间即可。"""
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))

def dishes(params):
    """结构化筛选（前端筛选面板直接对接；精确条件交给 SQL）"""
    budget = params.get('budget')
    budget_raw = budget
    try:
        budget = float(budget) if budget not in (None, '', 'null') else None
    except (TypeError, ValueError):

        return {'ok': False, 'error': '预算 budget 不是数字：%s' % budget_raw}
    rows = db.dish_rows(canteen=(params.get('canteen') or None), floor=(params.get('floor') or None),
                        shop=(params.get('shop') or None), budget=budget,
                        tags=_ints(params.get('tags')), exclude=_ints(params.get('exclude')))

    return {'ok': True, 'count': len(rows), 'truncated': len(rows) >= 1000, 'items': rows}

def search(params):
    q = str(params.get('q') or '').strip()
    if not q:
        return {'ok': False, 'error': '缺少关键词 q'}
    limit = _safe_int(params.get('limit'), 30, hi=200)
    rows = db.fts(q, limit=limit)
    return {'ok': True, 'q': q, 'count': len(rows), 'items': rows}
