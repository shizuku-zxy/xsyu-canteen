
"""数据访问层（DAO）——**整个项目里唯一写 SQL 的地方**。

设计对齐 longshu-canteen 的 `dao/` 层：上层（services/routers）只调这里的函数，
不直接碰 SQL；将来换数据库（SQLite → PostgreSQL）只改这一层。
"""
import os
import sqlite3
import time

from runtime_env import data

def db_path():
    return data('04_数据库', '数据', 'app.db')

def connect():
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    return con

def query(sql, args=()):
    con = connect()
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    finally:
        con.close()

def one(sql, args=()):
    r = query(sql, args)
    return r[0] if r else None

def dish_tags(name, shop=None):
    """菜名（+档口）→ tags_str（用户画像聚合用；找不到返回 ''）。"""
    if not name:
        return ''
    if shop:
        r = one('SELECT tags_str FROM dish WHERE name=? AND shop=? '
                'ORDER BY is_recommendable DESC LIMIT 1', (name, shop))
    else:
        r = one('SELECT tags_str FROM dish WHERE name=? '
                'ORDER BY is_recommendable DESC LIMIT 1', (name,))
    return (r or {}).get('tags_str') or ''

DISH_COLS = ('dish_id, shop_id, shop, campus, canteen, floor, category, name, price, spec, '
             'tags_str, price_band, is_addon, is_side, is_recommendable, price_confidence, '
             'allergen_status, allergen_inferred, source, updated_at, img, sold')

def recommendable_rows(campus=None, canteen=None):
    """取"可推荐主菜"行（给推荐引擎用；字段名与服务的行结构一致）"""
    sql = 'SELECT %s FROM dish WHERE is_recommendable = "1"' % DISH_COLS
    args = []
    if campus:
        sql += ' AND campus = ?'
        args.append(campus)
    if canteen:
        sql += ' AND canteen = ?'
        args.append(canteen)
    return query(sql, args)

def dish_rows(canteen=None, floor=None, shop=None, budget=None, tags=None, exclude=None, limit=1000):
    """结构化筛选（给前端的筛选面板用；精确条件交给 SQL）"""
    sql = 'SELECT %s FROM dish WHERE is_recommendable = "1"' % DISH_COLS
    args = []
    if canteen:
        sql += ' AND canteen = ?'
        args.append(canteen)
    if floor:
        sql += ' AND floor = ?'
        args.append(floor)
    if shop:
        sql += ' AND shop = ?'
        args.append(shop)
    if budget is not None:
        sql += ' AND price IS NOT NULL AND price <= ?'
        args.append(budget)
    for t in (tags or []):
        sql += ' AND ("," || tags_str || ",") LIKE ?'
        args.append('%%,%s,%%' % t)
    for t in (exclude or []):
        sql += ' AND ("," || tags_str || ",") NOT LIKE ?'
        args.append('%%,%s,%%' % t)
    sql += ' ORDER BY (price IS NULL), price, dish_id LIMIT ?'
    args.append(limit)
    return query(sql, args)

def shop_detail(name, canteen=None, floor=None):
    sql = 'SELECT * FROM shop WHERE shop = ?'
    args = [name]
    if canteen:
        sql += ' AND canteen = ?'
        args.append(canteen)
    if floor:
        sql += ' AND floor = ?'
        args.append(floor)
    s = one(sql, args)
    if not s:
        return None
    d = query('SELECT %s FROM dish WHERE shop = ? AND canteen = ? AND floor = ? '
              'ORDER BY CAST(is_addon AS INTEGER), (price IS NULL), price, dish_id' % DISH_COLS,
              [name, s['canteen'], s['floor']])
    s['dishes'] = d
    return s

def shops(canteen=None, floor=None, campus=None):
    sql = ('SELECT s.*, '
           '(SELECT MIN(price) FROM dish d WHERE d.shop = s.shop AND d.canteen = s.canteen '
           ' AND d.floor = s.floor AND d.is_recommendable = "1" AND d.price IS NOT NULL) AS min_price '
           'FROM shop s WHERE 1=1')
    args = []
    if canteen:
        sql += ' AND s.canteen = ?'
        args.append(canteen)
    if floor:
        sql += ' AND s.floor = ?'
        args.append(floor)
    if campus:
        sql += ' AND s.campus = ?'
        args.append(campus)
    sql += ' ORDER BY s.canteen, s.floor, s.shop'
    return query(sql, args)

def search_shops(q, limit=40):
    """档口（窗口）搜索（2026-10-14 新增，供前端**顶层搜索**用）。

    需求来源：以前顶层只能搜菜品，找到某个窗口要点三层（校区→食堂→楼层）。
    现在按下面分层匹配，**同级按人气降序**，前端直接跳转到该档口：
      0 店名完全相同 → 1 店名以关键词开头 → 2 店名包含 → 3 品类包含 → 4 档口标签包含
    （标签那层是新的：搜「清淡」能直接找到清淡档口，用的是 2026-10-14 新增的 shop.tags 列）
    """
    q = (q or '').strip()
    if not q:
        return []
    like = '%' + q + '%'
    starts = q + '%'
    sql = ('SELECT s.*, '
           '(SELECT MIN(price) FROM dish d WHERE d.shop = s.shop AND d.canteen = s.canteen '
           ' AND d.floor = s.floor AND d.is_recommendable = 1 AND d.price IS NOT NULL) AS min_price, '
           'CASE WHEN s.shop = ? THEN 0 WHEN s.shop LIKE ? THEN 1 WHEN s.shop LIKE ? THEN 2 '
           '     WHEN COALESCE(s.category, "") LIKE ? THEN 3 '
           '     WHEN COALESCE(s.tags, "") LIKE ? THEN 4 ELSE 9 END AS tier '
           'FROM shop s '
           'WHERE s.shop LIKE ? OR COALESCE(s.category, "") LIKE ? OR COALESCE(s.tags, "") LIKE ? '
           'ORDER BY tier, (s.sold_verified IS NULL OR s.sold_verified = 0), '
           's.sold_total DESC, s.dish_count_rec DESC, s.shop '
           'LIMIT ?')
    rows = query(sql, [q, starts, like, like, like, like, like, like, limit])
    if not rows:
        return rows

    rank = {r['shop']: i for i, r in enumerate(
        query('SELECT shop FROM shop WHERE sold_total IS NOT NULL AND sold_verified = 1 '
              'ORDER BY sold_total DESC, shop'), 1)}
    for r in rows:
        r['pop_rank'] = rank.get(r['shop'])
        r['tier'] = r.get('tier')
    return rows

def meta():
    """给前端的元数据：层级树 + 计数（对齐我们的界面：校区→食堂→楼层→档口）"""
    rows = query('SELECT campus, canteen, floor, shop, category, dish_count, photo, sign_img '
                 'FROM shop ORDER BY campus, canteen, floor, shop')
    tree = {}
    for r in rows:
        tree.setdefault(r['campus'], {}).setdefault(r['canteen'], {}).setdefault(r['floor'], []).append(
            {'name': r['shop'], 'cat': r['category'], 'cnt': r['dish_count'],
             'photo': r['photo'], 'sign_img': r['sign_img']})
    n_dish = one('SELECT COUNT(*) c FROM dish WHERE is_recommendable = "1"')['c']
    n_addon = one('SELECT COUNT(*) c FROM dish WHERE is_recommendable = "0"')['c']
    return {'tree': tree, 'shops': len(rows), 'dishes': n_dish, 'addons': n_addon,
            'updated_at': (one('SELECT MAX(updated_at) u FROM dish') or {}).get('u')}

def fts(q, limit=30):
    """FTS5 检索：菜名权重最高，其次标签/档口/品类（思路同 longshu 的字段加权）"""
    sql = ('SELECT d.%s, bm25(dish_fts, 0.0, 6.0, 3.0, 2.0, 1.5) AS rank '
           'FROM dish_fts JOIN dish d ON d.dish_id = dish_fts.dish_id '
           'WHERE dish_fts MATCH ? ORDER BY rank LIMIT ?' % DISH_COLS.replace(', ', ', d.'))
    try:
        return query(sql, [q, limit])
    except sqlite3.OperationalError:
        like = '%%%s%%' % q
        return query('SELECT %s FROM dish WHERE name LIKE ? OR tags_str LIKE ? OR shop LIKE ? '
                     'ORDER BY (price IS NULL), price LIMIT ?' % DISH_COLS, [like, like, like, limit])

def log_qa(question, slots_text, result_count, top_dish, allergen_excluded, latency_ms,
           evidence=None, confidence=None):
    """写一条问答留痕。

    2026-10-17（P1）：新增 evidence / confidence —— 模型"凭什么这么理解"的原文证据，
    答辩时可直接查证。evidence 传 dict 会被序列化成 JSON；两者都可缺省（老调用不受影响）。
    """
    ev = ''
    if isinstance(evidence, dict) and evidence:
        try:
            import json as _json
            ev = _json.dumps(evidence, ensure_ascii=False)
        except Exception:
            ev = ''
    elif isinstance(evidence, str):
        ev = evidence
    try:
        conf = float(confidence) if str(confidence or '').strip() != '' else None
    except (TypeError, ValueError):
        conf = None
    con = connect()
    try:
        con.execute('INSERT INTO qa_log (created_at, question, slots_text, result_count, top_dish, '
                    'allergen_excluded, latency_ms, evidence, confidence) '
                    'VALUES (?,?,?,?,?,?,?,?,?)',
                    [time.strftime('%Y-%m-%d %H:%M:%S'), question, slots_text, result_count,
                     top_dish or '', allergen_excluded or 0, latency_ms or 0, ev, conf])
        con.commit()
    finally:
        con.close()
