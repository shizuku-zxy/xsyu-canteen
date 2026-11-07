
"""「西石大·今天吃啥」服务端（Python 标准库，零第三方依赖）。

分层（对齐开源同类项目 longshu-canteen / bee-api 的结构）：
    app.py            ← 入口：HTTP 服务 + 路由（对应它们的 app.py / main.go）
    routers/          ← 接口层：参数校验与返回，不写业务
    services/         ← 业务层：槽位归一化 / 推荐引擎 / 口语理解（不碰 SQL）
    dao/db.py         ← 数据访问层：唯一写 SQL 的地方
    04_数据库/数据/app.db ← SQLite（由 build_db.py 从 04_数据库/backend 的 JSON 构建）

启动：python 03_服务端/app.py [--port 8100]
"""
import io
import json
import os
import subprocess
import sys
import threading
import time
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from runtime_env import BASE, FROZEN, ROOT, data, ensure_db, resource

os.chdir(ROOT)
sys.path.insert(0, os.path.join(ROOT, '03_服务端'))
sys.path.insert(0, BASE)
ensure_db()

from routers import api_catalog, api_event, api_llm, api_meta, api_recommend, api_supabase

FE = resource('02_前端')
DISH_IMGS = resource('02_前端', 'img', 'dish')

GET_ROUTES = {
    '/api/health': lambda p: {'ok': True, 'db': os.path.exists(data('04_数据库', '数据', 'app.db'))},
    '/api/llm_config': api_llm.status,
    '/api/meta': api_meta.handle,
    '/api/shops': api_catalog.shops,
    '/api/shop': api_catalog.shop,
    '/api/dishes': api_catalog.dishes,
    '/api/search': api_catalog.search,
    '/api/recommend': api_recommend.handle,
    '/api/ask': api_recommend.handle,
    '/api/supabase-config': api_supabase.config,
}
POST_ROUTES = {'/api/recommend': api_recommend.handle, '/api/ask': api_recommend.handle,
               '/api/llm_config': api_llm.save, '/api/auth/register': api_supabase.register,
               '/api/event': api_event.handle}

STATIC = {'/': ('index.html', 'text/html'), '/app': ('index.html', 'text/html')}
MIME = {'.html': 'text/html', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
        '.webp': 'image/webp', '.css': 'text/css', '.js': 'application/javascript',
        '.json': 'application/json', '.svg': 'image/svg+xml'}

def _inside(base, full):
    """目录包含判断（2026-10-13 修）。

    原来用 `full.startswith(base)`：路径已 normpath，所以 `/../` 穿越是被挡住的，
    但**同前缀的兄弟目录**能绕过——base=`…\\02_前端` 时 `…\\02_前端X\\secret` 也 startswith 成立。
    现在按真实包含关系判（`os.path.commonpath`）。
    """
    try:
        return os.path.commonpath([os.path.abspath(base), os.path.abspath(full)]) \
            == os.path.abspath(base)
    except ValueError:                
        return False

class Handler(BaseHTTPRequestHandler):
    server_version = 'XSYU-Canteen/1.0'

    def log_message(self, fmt, *a):
        if os.environ.get('CANTEEN_LOG'):
            sys.stderr.write('%s - %s\n' % (time.strftime('%H:%M:%S'), fmt % a))

    def _send(self, code, body, ctype='application/json; charset=utf-8', extra=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Cache-Control', 'no-cache')  
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if not getattr(self, '_head_only', False):     
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _params(self):
        parsed = urllib.parse.urlsplit(self.path)
        p = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query, keep_blank_values=True).items()}
        if self.command == 'POST':
            n = int(self.headers.get('Content-Length') or 0)
            if n:
                try:
                    body = json.loads(self.rfile.read(n).decode('utf-8'))
                    for k, v in (body or {}).items():
                        p[k] = ','.join(v) if isinstance(v, list) else str(v)
                except Exception:
                    pass
        # Supabase access token ~790 字符，上限须容纳完整 JWT（2048 留余量）
        p['_auth'] = (self.headers.get('Authorization') or '')[:2048]
        return urllib.parse.urlsplit(self.path).path, p

    def _static(self, path):

        if path.startswith('/dishimg/'):
            rel = urllib.parse.unquote(path[len('/dishimg/'):])
            full = os.path.normpath(os.path.join(DISH_IMGS, rel))
            if _inside(DISH_IMGS, full) and os.path.isfile(full):
                ext = os.path.splitext(full)[1].lower()
                with io.open(full, 'rb') as f:
                    return self._send(200, f.read(), MIME.get(ext, 'image/jpeg'))
            return self._send(404, '{"ok":false,"error":"no image"}', 'application/json; charset=utf-8')

        rel = urllib.parse.unquote(path.lstrip('/'))
        for base in (FE,):
            full = os.path.normpath(os.path.join(base, rel))
            if _inside(base, full) and os.path.isfile(full):
                ext = os.path.splitext(full)[1].lower()
                ctype = MIME.get(ext, 'application/octet-stream')
                if ctype.startswith('text/') or ctype == 'application/javascript':
                    ctype += '; charset=utf-8'
                with io.open(full, 'rb') as f:
                    return self._send(200, f.read(), ctype)
        return self._send(404, '{"ok":false,"error":"not found"}', 'application/json; charset=utf-8')

    def do_GET(self):
        path, params = self._params()

        if path == '/api/phone':
            ip = _lan_ip() or ''
            port = getattr(self.server, 'server_port', 8100)
            return self._json({'ok': bool(ip), 'ip': ip, 'port': port,
                               'url': ('http://%s:%d/' % (ip, port)) if ip else ''})
        if path in GET_ROUTES:
            try:
                return self._json(GET_ROUTES[path](params))
            except Exception as e:                       
                return self._json({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}, 500)
        if path in STATIC:
            name, ctype = STATIC[path]
            f = os.path.join(FE, name)
            if os.path.isfile(f):
                with io.open(f, 'rb') as fh:
                    return self._send(200, fh.read(), ctype + '; charset=utf-8')
            return self._send(404, 'not found', 'text/plain; charset=utf-8')
        return self._static(path)

    def do_POST(self):
        path, params = self._params()
        if path in POST_ROUTES:
            try:
                return self._json(POST_ROUTES[path](params))
            except Exception as e:                       
                return self._json({'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}, 500)
        return self._json({'ok': False, 'error': 'not found'}, 404)

    def do_HEAD(self):
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def do_OPTIONS(self):
        self._send(200, b'', 'text/plain; charset=utf-8', extra={
            'Access-Control-Allow-Methods': 'GET, POST, HEAD, OPTIONS',
            'Access-Control-Allow-Headers': 'Content-Type, Authorization'})

def _lan_ip():
    """取局域网 IP（安卓端访问用；拿不到返回 None）。"""
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if not ip.startswith('127.') else None
    except Exception:
        return None

def _already_serving(port):
    """该端口上是否已经跑着我们的服务（用于"双击启动"时友好提示，而不是报错崩掉）"""
    import urllib.request
    try:
        with urllib.request.urlopen('http://127.0.0.1:%d/api/health' % port, timeout=2) as r:
            return b'"ok": true' in r.read()
    except Exception:
        return False

def _open_browser(port):
    if not FROZEN or '--no-browser' in sys.argv:
        return
    url = 'http://127.0.0.1:%d/' % port
    if os.name == 'nt':
        try:
            os.startfile(url)
            print('  浏览器 : 已调用系统默认浏览器')
            return
        except Exception:
            pass
    try:
        if webbrowser.open(url):
            print('  浏览器 : 已调用默认浏览器')
            return
    except Exception:
        pass
    try:
        if os.name == 'nt':
            os.startfile(url)
            print('  浏览器 : 已调用默认浏览器')
            return
        subprocess.Popen(['xdg-open', url])
        print('  浏览器 : 已调用默认浏览器')
    except Exception:
        print('  浏览器 : 未能自动打开，请手动访问 %s' % url)


def _open_browser_later(port):
    if not FROZEN or '--no-browser' in sys.argv:
        return
    def _open():
        time.sleep(0.6)
        _open_browser(port)
    threading.Thread(target=_open, daemon=True).start()


def main():
    port = 8100
    if '--port' in sys.argv:
        port = int(sys.argv[sys.argv.index('--port') + 1])
    dbp = data('04_数据库', '数据', 'app.db')
    if not os.path.exists(dbp):
        ensure_db()
        dbp = data('04_数据库', '数据', 'app.db')
    if not os.path.exists(dbp):
        print('数据库文件缺失：04_数据库/数据/app.db（最终版已内置，请勿删除）')
        return 1

    if _already_serving(port):
        print('服务已经在本机 %d 端口运行了：' % port)
        print('  界面 : http://127.0.0.1:%d/' % port)
        _open_browser(port)
        return 0
    for p in [port] + list(range(port + 1, port + 11)):
        try:
            srv = ThreadingHTTPServer(('0.0.0.0' if '--local' not in sys.argv else '127.0.0.1', p), Handler)
            port = p
            break
        except OSError as e:
            print('端口 %d 被占用（%s），换下一个…' % (p, e))
    else:
        print('8100-8110 都被占用了，请先关掉占用端口的程序，或用 --port 指定其它端口')
        return 1

    lan_ip = _lan_ip()
    print('=' * 56)
    print('「西石大·今天吃啥」服务端已启动')
    print('  界面   : http://127.0.0.1:%d/          （我们自己的前端）' % port)
    if lan_ip:
        print('  安卓端 : http://%s:%d/  （手机连同一WiFi，浏览器/WebView 打开）' % (lan_ip, port))
    print('  接口   : /api/meta /api/shops /api/shop /api/dishes /api/search /api/recommend')
    print('  数据库 : 04_数据库/数据/app.db')
    print('  关闭   : Ctrl+C')
    print('=' * 56)
    _open_browser_later(port)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n已停止')
    return 0

if __name__ == '__main__':
    sys.exit(main())
