
"""LLM 客户端（标准库实现，零第三方依赖，OpenAI 兼容）。

配置优先级：
  1) 环境变量：CANTEEN_LLM_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY（任选其一）
     + 可选 CANTEEN_LLM_BASE_URL / CANTEEN_LLM_MODEL
  2) 配置文件：03_服务端/llm_config.json（照 llm_config.example.json 改）
  3) 都没有 → **不启用**，上层自动退回规则版（离线也能演示）

设计原则（对应答辩口径）：
  - LLM 只做"翻译"：口语↔槽位、槽位↔人话；**决策仍在确定性代码里**；
  - 一切失败（无 key / 超时 / 返回不是 JSON / 字段越界）都**静默降级**，不让演示挂掉；
  - 同问同答：相同输入走本地缓存，不再请求第二次。
"""
import hashlib
import io
import json
import os
import time
import urllib.error
import urllib.request

from runtime_env import ROOT, data, llm_config_path, resource

CACHE = data('03_服务端', 'llm_cache.json')
os.makedirs(os.path.dirname(CACHE), exist_ok=True)
DEFAULT_BASE = 'https://api.deepseek.com/v1'
DEFAULT_MODEL = 'deepseek-chat'

def load_config():
    cfg = {'enabled': False, 'base_url': DEFAULT_BASE, 'model': DEFAULT_MODEL,
           'api_key': '', 'timeout': 15, 'json_mode': True, 'cache': True, 'temperature': 0}
    path = llm_config_path()
    if os.path.exists(path):
        try:
            cfg.update(json.load(io.open(path, encoding='utf-8')) or {})
        except Exception:
            pass
    key = (os.environ.get('CANTEEN_LLM_API_KEY') or os.environ.get('DEEPSEEK_API_KEY')
           or os.environ.get('OPENAI_API_KEY') or '')
    if key:
        cfg['api_key'] = key
        cfg['enabled'] = True
    cfg['base_url'] = (os.environ.get('CANTEEN_LLM_BASE_URL') or cfg['base_url']).rstrip('/')
    cfg['model'] = os.environ.get('CANTEEN_LLM_MODEL') or cfg['model']
    if not cfg.get('api_key'):
        cfg['enabled'] = False
    return cfg

def available():
    c = load_config()
    return bool(c['enabled'] and c['api_key'])

def _ckey(model, prompt, text):
    return hashlib.sha256(('%s|%s|%s' % (model, prompt[:200], text)).encode('utf-8')).hexdigest()[:24]

def cache_get(k):
    try:
        if os.path.exists(CACHE):
            return (json.load(io.open(CACHE, encoding='utf-8')) or {}).get(k)
    except Exception:
        pass
    return None

def cache_put(k, val):
    try:
        d = {}
        if os.path.exists(CACHE):
            d = json.load(io.open(CACHE, encoding='utf-8')) or {}
        d[k] = val
        if len(d) > 500:                                  
            for kk in list(d)[:len(d) - 500]:
                d.pop(kk, None)
        io.open(CACHE, 'w', encoding='utf-8', newline='\n').write(json.dumps(d, ensure_ascii=False, indent=1))
    except Exception:
        pass

class LLMError(Exception):
    pass

def chat(messages, cfg=None, temperature=None, max_tokens=800, use_cache_key=None,
         json_mode=None):
    """返回助手文本；失败抛 LLMError（上层负责降级）

    json_mode：None=跟随配置（默认开）；False=要**自由文本**。
    2026-10-13 补：原来无处可关。写"开场白"这类纯文本时如果还带
    response_format=json_object，服务端会直接 400
    （"Prompt must contain the word 'json'…"）——开场白因此永远生成不出来。
    """
    cfg = cfg or load_config()
    if not (cfg.get('enabled') and cfg.get('api_key')):
        raise LLMError('LLM 未配置（缺 api_key）')

    key = None
    if use_cache_key and cfg.get('cache', True):
        key = _ckey(cfg['model'], messages[0].get('content', '') if messages else '', use_cache_key)
        hit = cache_get(key)
        if hit is not None:
            return hit

    payload = {'model': cfg['model'], 'messages': messages, 'max_tokens': max_tokens,
               'temperature': cfg.get('temperature', 0) if temperature is None else temperature}
    use_json = cfg.get('json_mode', True) if json_mode is None else json_mode
    if use_json:
        payload['response_format'] = {'type': 'json_object'}
    req = urllib.request.Request(
        cfg['base_url'] + '/chat/completions',
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + cfg['api_key']})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=cfg.get('timeout', 15)) as r:
            obj = json.loads(r.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        raise LLMError('HTTP %s: %s' % (e.code, (e.read() or b'')[:200].decode('utf-8', 'ignore')))
    except Exception as e:                                   
        raise LLMError('%s: %s' % (type(e).__name__, e))
    try:
        text = obj['choices'][0]['message']['content']
    except Exception:
        raise LLMError('返回结构异常: %s' % str(obj)[:200])
    if key:
        cache_put(key, text)
    _ = time.time() - t0
    return text

def ask_json(system, user, cfg=None, max_tokens=800, cache_key=None):
    """要一段 JSON：自动去掉 ```json 围栏、解析失败抛 LLMError"""
    text = chat([{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
                cfg=cfg, max_tokens=max_tokens, use_cache_key=cache_key)
    t = text.strip()
    if t.startswith('```'):
        t = t.split('```')[1] if '```' in t[3:] else t[3:]
        t = t.lstrip('json').lstrip()
    i, j = t.find('{'), t.rfind('}')
    if i >= 0 and j > i:
        t = t[i:j + 1]
    try:
        return json.loads(t)
    except Exception as e:                                   
        raise LLMError('不是合法 JSON（%s）：%s' % (e, t[:160]))

def status():
    c = load_config()
    path = llm_config_path()
    return {'enabled': bool(c['enabled'] and c['api_key']), 'model': c['model'],
            'base_url': c['base_url'], 'has_key': bool(c['api_key']),
            'config_file': os.path.relpath(path, ROOT) if os.path.exists(path) else None,
            'cache_file': os.path.relpath(CACHE, ROOT) if os.path.exists(CACHE) else None}

if __name__ == '__main__':
    s = status()
    print('LLM 状态：')
    for k, v in s.items():
        print('  %-12s %s' % (k, v))
    if not s['enabled']:
        print('\n尚未配置。两种启用方式：')
        print('  A) 环境变量：$env:CANTEEN_LLM_API_KEY="sk-..."（临时）')
        print('  B) 复制 03_服务端/llm_config.example.json 为 llm_config.json，填 api_key 后重启服务')
