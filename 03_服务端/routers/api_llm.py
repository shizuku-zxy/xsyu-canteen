# -*- coding: utf-8 -*-
"""前端模型设置：保存用户自己的 Key，不写入源码或安装包。"""
import io
import json
import os

from runtime_env import data
from services import llm_fallback


DEFAULT_BASE = 'https://api.deepseek.com/v1'
DEFAULT_MODEL = 'deepseek-chat'


def _path():
    path = data('03_服务端', 'llm_config.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def status(params=None):
    cfg = llm_fallback._load_cfg()
    return {'ok': True, 'enabled': bool(cfg.get('enabled') and cfg.get('api_key')),
            'model': cfg.get('model') or '', 'base_url': cfg.get('base_url') or '',
            'has_key': bool(cfg.get('api_key'))}


def save(params):
    cur = llm_fallback._load_cfg()
    key = str(params.get('api_key') or '').strip()
    if key:
        base = str(params.get('base_url') or '').strip() or cur.get('base_url') or DEFAULT_BASE
        model = str(params.get('model') or '').strip() or cur.get('model') or DEFAULT_MODEL
    else:
        base = str(params.get('base_url') or '').strip() or DEFAULT_BASE
        model = str(params.get('model') or '').strip() or DEFAULT_MODEL
    cfg = {
        'enabled': bool(key),
        'base_url': base.rstrip('/'),
        'model': model,
        'api_key': key,
        'timeout': 20,
        'json_mode': True,
        'cache': True,
        'temperature': 0,
        '_说明': ['由页面“模型设置”写入本机数据目录；不写入源码，也不随 exe 分发。',
                  '不填 API Key 时自动使用内置规则话术；填写后调用用户自己的 OpenAI 兼容接口。'],
    }
    io.open(_path(), 'w', encoding='utf-8', newline='\n').write(
        json.dumps(cfg, ensure_ascii=False, indent=1) + '\n')
    cache = data('03_服务端', 'llm_cache.json')
    if os.path.exists(cache):
        try:
            os.remove(cache)
        except OSError:
            pass
    llm_fallback.reload_config()
    return {'ok': True, 'enabled': bool(key), 'model': cfg['model'],
            'base_url': cfg['base_url']}
