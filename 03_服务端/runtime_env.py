# -*- coding: utf-8 -*-
"""运行环境路径：源码运行与 PyInstaller 单文件 exe 共用。"""
import hashlib
import os
import shutil
import sys

FROZEN = bool(getattr(sys, 'frozen', False))
BASE = getattr(sys, '_MEIPASS', os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(sys.executable) if FROZEN else BASE
DATA = os.environ.get('XSYU_DATA_DIR') or (
    os.path.join(os.environ.get('LOCALAPPDATA') or ROOT, 'XSYU-Canteen')
    if FROZEN else ROOT)

os.makedirs(os.path.join(DATA, '04_数据库', '数据'), exist_ok=True)
os.makedirs(os.path.join(DATA, '03_服务端'), exist_ok=True)


def resource(*parts):
    return os.path.join(BASE, *parts)


def data(*parts):
    return os.path.join(DATA, *parts)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def ensure_db():
    src = resource('04_数据库', '数据', 'app.db')
    dst = data('04_数据库', '数据', 'app.db')
    if os.path.abspath(src) == os.path.abspath(dst):
        return dst
    marker = data('04_数据库', '数据', '.app_db_sha256')
    want = _sha256(src)
    have = ''
    try:
        if os.path.exists(marker):
            have = open(marker, encoding='utf-8').read().strip()
    except OSError:
        pass
    if not os.path.exists(dst) or os.path.getsize(dst) == 0 or have != want:
        if os.path.exists(dst) and os.path.getsize(dst):
            try:
                shutil.copy2(dst, dst + '.bak')
            except OSError:
                pass
        shutil.copy2(src, dst)
        try:
            open(marker, 'w', encoding='utf-8', newline='\n').write(want + '\n')
        except OSError:
            pass
    return dst


def llm_config_path():
    external = data('03_服务端', 'llm_config.json')
    if os.path.exists(external):
        return external
    return resource('03_服务端', 'llm_config.json')
