# -*- coding: utf-8 -*-
"""Supabase 配置下发 + 注册接口：前端登录模块从这里拿 url / anonKey。

凭证加载统一走 services/sb_client.py（env → coze_workload_identity SDK → 报错），
凭证只存于运行时环境，不入源码、不入库、不入 git。
"""
import json
import re
import urllib.error
import urllib.request

from services import sb_client

_credentials = sb_client.credentials
_service_key = sb_client.service_key


def config(params):
    """GET /api/supabase-config → {ok, url, anonKey}"""
    try:
        url, anon = _credentials()
        return {'ok': True, 'url': url, 'anonKey': anon}
    except Exception as e:
        return {'ok': False, 'error': str(e)}


_EMAIL_SUFFIX = '@xsyu-eat.local'
_USER_RE = re.compile(r'^[一-龥A-Za-z0-9_]{2,20}$')


def register(params):
    """POST /api/auth/register {username, password}

    用户名映射为内部邮箱（Supabase Auth 只认邮箱）；service_role 走 admin API
    创建用户并 email_confirm=True 自动确认，规避假邮箱收不到验证邮件的问题。
    """
    username = (params.get('username') or '').strip()
    password = params.get('password') or ''
    if not _USER_RE.match(username):
        return {'ok': False, 'error': '用户名需 2~20 位（字母/数字/中文/下划线）'}
    if len(password) < 6:
        return {'ok': False, 'error': '密码至少 6 位'}
    try:
        url, _ = _credentials()
    except Exception as e:
        return {'ok': False, 'error': str(e)}
    key = _service_key()
    if not key:
        return {'ok': False, 'error': '服务端缺少 service role key'}
    body = json.dumps({
        'email': username.lower() + _EMAIL_SUFFIX,
        'password': password,
        'email_confirm': True,
        'user_metadata': {'username': username},
    }).encode('utf-8')
    req = urllib.request.Request(
        url.rstrip('/') + '/auth/v1/admin/users', data=body, method='POST',
        headers={'apikey': key, 'Authorization': 'Bearer ' + key,
                 'Content-Type': 'application/json'})
    for attempt in range(2):  # GoTrue 冷启动偶发 5xx，重试一次
        try:
            with urllib.request.urlopen(req, timeout=15):
                return {'ok': True}
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8')
            except Exception:
                pass
            if e.code in (400, 422) and re.search(r'already|exists|taken', detail, re.I):
                return {'ok': False, 'error': '该用户名已被注册，直接登录即可'}
            if e.code >= 500 and attempt == 0:
                continue
            return {'ok': False, 'error': '注册失败（HTTP %s），请重试' % e.code}
        except Exception as e:
            if attempt == 0:
                continue
            return {'ok': False, 'error': '注册服务异常：%s' % type(e).__name__}
    return {'ok': False, 'error': '注册服务异常，请重试'}
