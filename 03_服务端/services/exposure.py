
"""曝光记录 + 稳定抖动（多样性的基础设施）

设计要点（用户 2026-10-15 决策：四层全做，接受"同日同答 + 换一批"）：
  · **曝光表**：记录每道菜/每个档口最近被推荐过没有 → 最近推过的"压一压"，
    让 1158 道**没有销量数据、永远沉底**的菜也能轮上来。
    存放位置刻意**不放 app.db**：app.db 每次 build_db 都会重建，放进去会被清空 ✗。
    这里用 `03_服务端/exposure.json`（跟 llm_cache.json 一个位置，运行期状态）。
  · **稳定抖动**：用 md5(seed + id) 生成 0–1 的伪随机数——**不是全局随机**，
    所以"同一天 + 同一问题 + 同一批次"永远算出同一个数（同日幂等 ✓ 可测试 ✓）。
"""
import hashlib
import io
import json
import os
import time

from runtime_env import data

_PATH = data('03_服务端', 'exposure.json')
os.makedirs(os.path.dirname(_PATH), exist_ok=True)
_CACHE = None
_DIRTY = False
_WINDOW_DAYS = 3.0          

def jitter(seed_key, ref_id):
    """0–1 的稳定伪随机数：同一 (seed, id) 永远同值。"""
    h = hashlib.md5(('%s|%s' % (seed_key or '', ref_id or '')).encode('utf-8')).hexdigest()
    return int(h[:6], 16) / float(0xFFFFFF)

def make_seed(*parts):
    """把（问题、日期、批次）折成一个种子字符串。"""
    h = hashlib.md5('|'.join(str(p or '') for p in parts).encode('utf-8')).hexdigest()
    return h[:12]

def _load():
    global _CACHE
    if _CACHE is None:
        try:
            _CACHE = json.load(io.open(_PATH, encoding='utf-8'))
        except Exception:                                    
            _CACHE = {}
    return _CACHE

def _save():
    global _DIRTY
    if not _DIRTY:
        return
    try:
        tmp = _PATH + '.tmp'
        io.open(tmp, 'w', encoding='utf-8', newline='\n').write(
            json.dumps(_CACHE, ensure_ascii=False, separators=(',', ':')))
        os.replace(tmp, _PATH)
        _DIRTY = False
    except Exception:                                        
        pass

def penalty(ref_id, days=_WINDOW_DAYS):
    """0–1 的"最近推过"惩罚：刚推过 → 接近 1；超过窗口 → 0。

    线性衰减：0 天 = 1.0，days 天 = 0.0。只影响排序，不影响任何过滤。
    """
    rec = _load().get(ref_id)
    if not rec:
        return 0.0
    ts = float(rec.get('ts') or 0)
    if ts <= 0:
        return 0.0
    age_days = max(0.0, (time.time() - ts) / 86400.0)

    if age_days < 1.0:
        return 0.0
    if age_days >= days:
        return 0.0
    span = max(1e-9, days - 1.0)
    return max(0.0, 1.0 - (age_days - 1.0) / span)

def bump(ref_ids, now=None):
    """把这次真正展示出去的条目记一笔（幂等：同一次请求只记一次）。"""
    global _DIRTY
    data = _load()
    now = now or time.time()
    for rid in ref_ids:
        if not rid:
            continue
        rec = data.get(rid) or {'n': 0, 'ts': 0}
        rec['n'] = int(rec.get('n') or 0) + 1
        rec['ts'] = now
        data[rid] = rec
    _DIRTY = True
    _save()

def stats():
    d = _load()
    return {'tracked': len(d), 'total_shows': sum(int(v.get('n') or 0) for v in d.values())}
