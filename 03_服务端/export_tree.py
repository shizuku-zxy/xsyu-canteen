# -*- coding: utf-8 -*-
"""把 app.db 的档口/菜品数据重新生成到 02_前端/index.html 的内联 TREE（2026-11）。

背景：本项目前端是**单文件 SPA**，`const TREE = {...}` 就是浏览/搜索/筛选的数据源
（启动时没有 /api/meta 动态替换逻辑）。所以 DB 里改了价格/标签/图片后，必须重跑本脚本，
页面才会带着新标签、新价格、新照片。脚本只重写 `const TREE = {...};` 这一段，
不碰其余 JS/CSS；JSON 压缩成一行，结构/字段与原快照一致：

    campus → canteen → floor → [ {name, cat, cat_raw, photo, hidden[], dishes[]} ]
    dish: {n 名称, p 价格, sp 规格, i 图片, t 标签数组, cf 价格置信度}
    hidden: 加料/配菜 [{name, price}]

用法（项目根目录）：
    python projects/03_服务端/export_tree.py            # 预览
    python projects/03_服务端/export_tree.py --apply    # 写回 index.html
"""
import argparse
import json
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)
DB_PATH = os.path.join(ROOT, "04_数据库", "数据", "app.db")
HTML_PATH = os.path.join(ROOT, "02_前端", "index.html")

# 档口照片人工覆盖（DB 中 photo 字段更新后，前端快照照片同步；键=档口名）
PHOTO_OVERRIDE = {
    "户县软面": "/img/shop-IMG_20261105_154413.jpg",
}

# 没有历史快照时的兜底：DB 档口 category（档口级）→ 前端粗品类 cat
CAT_FALLBACK = {
    "面食": "面", "米饭快餐": "米饭快餐", "小吃饼类": "小吃饼类", "粉": "粉",
    "水饺包子": "水饺包子", "甜品饮品": "甜品饮品", "汉堡披萨": "汉堡披萨",
    "粥汤": "粥汤", "麻辣烫香锅": "麻辣烫香锅", "轻食": "轻食", "轻食粥品": "轻食",
}


def extract_tree(html):
    marker = "const TREE ="
    i = html.index(marker) + len(marker)
    depth, j = 0, i
    while j < len(html):
        ch = html[j]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    if depth != 0:
        raise SystemExit("index.html 里的 TREE JSON 括号不配对")
    return i, j + 1, json.loads(html[i:j + 1])


def build_tree(old_tree, con):
    con.row_factory = sqlite3.Row
    shops = con.execute("SELECT shop, campus, canteen, floor, category FROM shop "
                        "ORDER BY shop").fetchall()
    dishes = con.execute(
        "SELECT dish_id, shop, canteen, floor, name, price, spec, tags_str, price_confidence, "
        "is_recommendable, img FROM dish ORDER BY dish_id").fetchall()
    by_shop = {}
    for d in dishes:
        by_shop.setdefault((d["shop"], d["canteen"], d["floor"]), {"rec": [], "hidden": []})
        if str(d["is_recommendable"]) == "1":
            by_shop[(d["shop"], d["canteen"], d["floor"])]["rec"].append(d)
        else:
            by_shop[(d["shop"], d["canteen"], d["floor"])]["hidden"].append(d)

    # 旧快照的 cat/cat_raw/photo 尽量沿用（避免整站照片风格被换掉）
    old_meta = {}
    for campus in old_tree:
        for canteen in old_tree[campus]:
            for floor in old_tree[campus][canteen]:
                for sh in old_tree[campus][canteen][floor]:
                    old_meta[(sh.get("name"), canteen, floor)] = sh

    tree = {}
    n_new_shops = n_dish = n_hidden = 0
    for s in shops:
        key = (s["shop"], s["canteen"], s["floor"])
        old = old_meta.get(key, {})
        if not old:
            n_new_shops += 1
        cat = old.get("cat") or CAT_FALLBACK.get(s["category"] or "", s["category"] or "其他")
        cat_raw = old.get("cat_raw") or (s["category"] or "")
        photo = PHOTO_OVERRIDE.get(s["shop"], old.get("photo") or "")
        bucket = by_shop.get(key, {"rec": [], "hidden": []})
        item = {
            "name": s["shop"],
            "cat": cat,
            "cat_raw": cat_raw,
            "photo": photo,
            "hidden": [{"name": d["name"], "price": _num(d["price"])} for d in bucket["hidden"]],
            "dishes": [{
                "n": d["name"],
                "p": _num(d["price"]),
                "sp": d["spec"] or "",
                "i": ("/dishimg/" + d["img"]) if d["img"] else "",
                "t": [t for t in (d["tags_str"] or "").split(",") if t],
                "cf": d["price_confidence"] or "",
            } for d in bucket["rec"]],
        }
        n_dish += len(item["dishes"])
        n_hidden += len(item["hidden"])
        tree.setdefault(s["campus"], {}).setdefault(s["canteen"], {}).setdefault(
            s["floor"], []).append(item)

    # 保持原快照里校区/食堂/楼层的顺序；新出现且有数据的按名字排序补在末尾
    ordered = {}
    for campus in list(old_tree.keys()) + [c for c in tree if c not in old_tree]:
        if campus not in tree:
            ordered[campus] = {}
            continue
        ordered[campus] = {}
        canteens = list(old_tree.get(campus, {}).keys()) + [
            c for c in tree[campus] if c not in old_tree.get(campus, {})]
        for canteen in canteens:
            if canteen not in tree[campus]:
                ordered[campus][canteen] = {}
                continue
            ordered[campus][canteen] = {}
            floors = list(old_tree.get(campus, {}).get(canteen, {}).keys()) + [
                f for f in tree[campus][canteen]
                if f not in old_tree.get(campus, {}).get(canteen, {})]
            for floor in floors:
                if floor not in tree[campus][canteen]:
                    ordered[campus][canteen][floor] = []
                    continue
                old_order = [x.get("name") for x in
                             old_tree.get(campus, {}).get(canteen, {}).get(floor, [])]
                new_list = tree[campus][canteen][floor]
                rank = {nm: i for i, nm in enumerate(old_order)}
                new_list.sort(key=lambda x: (rank.get(x["name"], 10 ** 6), x["name"]))
                ordered[campus][canteen][floor] = new_list
    return ordered, {"shops": len(shops), "dishes": n_dish, "hidden": n_hidden,
                     "new_shops": n_new_shops}


def _num(v):
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return int(f) if f == int(f) else round(f, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 index.html")
    args = ap.parse_args()
    if not os.path.exists(DB_PATH):
        sys.exit("找不到数据库：%s" % DB_PATH)
    html = open(HTML_PATH, encoding="utf-8").read()
    i, j, old_tree = extract_tree(html)
    con = sqlite3.connect(DB_PATH)
    try:
        tree, stat = build_tree(old_tree, con)
    finally:
        con.close()
    payload = json.dumps(tree, ensure_ascii=False, separators=(",", ":"))
    print("TREE：%d 档口 / %d 道菜 / %d 加料；新档口 %d；JSON %d 字符"
          % (stat["shops"], stat["dishes"], stat["hidden"], stat["new_shops"], len(payload)))
    print("户县软面 photo -> %s" % PHOTO_OVERRIDE["户县软面"])
    if args.apply:
        new_html = html[:i] + payload + html[j:]
        open(HTML_PATH, "w", encoding="utf-8", newline="\n").write(new_html)
        print("已写回 %s（%d -> %d 字符）" % (HTML_PATH, len(html), len(new_html)))
    else:
        print("（dry-run：加 --apply 写回）")


if __name__ == "__main__":
    main()
