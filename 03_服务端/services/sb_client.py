# -*- coding: utf-8 -*-
"""Supabase REST 基础设施（services 层，routers 可复用）。

职责：
  · credentials() / service_key()：凭证多来源加载（env → workload SDK 回填）
  · verify_token(token)：GoTrue 验证用户 JWT → user_id（进程内缓存 10 分钟）
  · select / insert / delete：PostgREST（service_role）增删查，全部带超时与容错

分层纪律：本模块是 services 层唯一的 Supabase 网络出口；
routers/api_supabase.py 的注册接口也从这里拿凭证。
"""
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_TIMEOUT = 6
_VERIFY_TTL = 600.0
_VERIFY_CACHE = {}
_LOCK = threading.Lock()


def credentials():
    """(url, anonKey)；来源：env → coze_workload_identity SDK 回填 env。"""
    url = os.environ.get('COZE_SUPABASE_URL')
    anon = os.environ.get('COZE_SUPABASE_ANON_KEY')
    if url and anon:
        return url, anon
    try:
        from coze_workload_identity import Client as WorkloadClient
        client = WorkloadClient()
        try:
            env_vars = client.get_project_env_vars()
        finally:
            client.close()
        for ev in env_vars:
            if not os.environ.get(ev.key):
                os.environ[ev.key] = ev.value
        url = os.environ.get('COZE_SUPABASE_URL')
        anon = os.environ.get('COZE_SUPABASE_ANON_KEY')
    except Exception:
        pass
    if not url or not anon:
        raise RuntimeError('Supabase 凭证未配置（COZE_SUPABASE_URL / COZE_SUPABASE_ANON_KEY）')
    return url, anon


def service_key():
    """service_role key（仅后端使用，严禁下发前端）。"""
    key = os.environ.get('COZE_SUPABASE_SERVICE_ROLE_KEY')
    if not key:
        credentials()  # 触发 workload 回填
        key = os.environ.get('COZE_SUPABASE_SERVICE_ROLE_KEY')
    if not key:
        raise RuntimeError('Supabase service_role_key 未配置')
    return key


def _request(url, data=None, headers=None, method=None, retries=0):
    """单次 HTTP；5xx 时可选重试一次（GoTrue 冷启动偶发）。返回 (status, body_dict|None)。"""
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, method=method or ('POST' if data is not None else 'GET'))
        if data is not None:
            req.data = json.dumps(data, ensure_ascii=False).encode('utf-8')
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
                raw = r.read()
                body = None
                if raw:
                    try:
                        body = json.loads(raw.decode('utf-8'))
                    except Exception:
                        body = None
                return r.status, body
        except urllib.error.HTTPError as e:
            status = e.code
            body = None
            try:
                body = json.loads((e.read() or b'').decode('utf-8'))
            except Exception:
                pass
            if 500 <= status < 600 and attempt < retries:
                time.sleep(0.8)
                continue
            return status, body
        except Exception:
            if attempt < retries:
                time.sleep(0.8)
                continue
            return 0, None
    return 0, None


def verify_token(token):
    """验证用户 JWT → user_id；失败/过期返回 None。结果缓存 10 分钟（sha256(token) 为键）。"""
    token = (token or '').strip()
    if not token:
        return None
    tk = token[7:] if token.lower().startswith('bearer ') else token
    if not tk:
        return None
    h = hashlib.sha256(tk.encode('utf-8')).hexdigest()
    now = time.time()
    with _LOCK:
        hit = _VERIFY_CACHE.get(h)
        if hit and hit[0] > now:
            return hit[1]
    try:
        url, anon = credentials()
    except Exception:
        return None
    # GoTrue 约定：apikey 必须是平台密钥（anonKey），用户 token 走 Authorization
    status, body = _request('%s/auth/v1/user' % url.rstrip('/'),
                            headers={'apikey': anon, 'Authorization': 'Bearer ' + tk},
                            retries=1)
    uid = None
    if status == 200 and isinstance(body, dict):
        uid = str(body.get('id') or '') or None
    with _LOCK:
        if len(_VERIFY_CACHE) > 512:
            _VERIFY_CACHE.clear()
        _VERIFY_CACHE[h] = (now + _VERIFY_TTL, uid)
    return uid


def _rest_headers():
    return {'apikey': service_key(),
            'Authorization': 'Bearer ' + service_key(),
            'Content-Type': 'application/json'}


def select(table, query, retries=0):
    """PostgREST 查询：query 形如 'user_id=eq.xxx&select=a,b'。返回行列表（失败 None）。"""
    try:
        url = '%s/rest/v1/%s?%s' % (credentials()[0].rstrip('/'), table, query)
    except Exception:
        return None
    status, body = _request(url, headers=_rest_headers(), retries=retries)
    if status == 200 and isinstance(body, list):
        return body
    return None


def insert(table, row, retries=1):
    """PostgREST 插入（return=minimal）。返回 True/False。"""
    try:
        url = '%s/rest/v1/%s' % (credentials()[0].rstrip('/'), table)
        st, _ = _request(url, data=row,
                         headers=dict(_rest_headers(), Prefer='return=minimal'),
                         retries=retries)
        return st in (200, 201)
    except Exception:
        return False


def delete_all(table, query, retries=1):
    """PostgREST 批量删除：query 形如 'user_id=eq.xxx'。返回 True/False。"""
    try:
        url = '%s/rest/v1/%s?%s' % (credentials()[0].rstrip('/'), table, query)
        st, _ = _request(url, headers=_rest_headers(), method='DELETE', retries=retries)
        return st in (200, 204)
    except Exception:
        return False
