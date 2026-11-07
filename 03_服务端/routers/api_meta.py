
"""路由层 · 元数据：给前端一次拿到 校区→食堂→楼层→档口 层级树 + 计数。

对应前端启动时要的东西（原先是把 TREE 常量内联进 HTML，现在改为 HTTP 取）。
"""
from dao import db

def handle(params):
    m = db.meta()
    m['ok'] = True
    m['source'] = 'sqlite:04_数据库/数据/app.db'

    m['sign_shops'] = db.one('SELECT COUNT(*) c FROM shop WHERE sign_img <> ""')['c']
    return m
