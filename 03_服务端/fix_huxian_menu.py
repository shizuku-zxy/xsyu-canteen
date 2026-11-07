# -*- coding: utf-8 -*-
"""户县软面档口数据修订（2026-11，用户提供新价目表实拍 IMG_20261105_154413）。

改动内容：
  1. 档口照片：img/shop-IMG_20261008_205431.jpg（520x114 模糊裁切）→
     img/shop-IMG_20261105_154413.jpg（1400x1051 价目表实拍）
  2. 补 4 道面的缺失价格（价目表「小份/大份」右侧一栏 11 元）：
       金汤肥牛面 / 酸菜肉丝面 / 酸辣臊子面 / 三鲜臊子面 → 11 元（confidence=high）
  3. 新增 2 道加料：煎蛋 1.5 元、卤蛋 1.5 元（价目表右下角；参照 D0058/D0059 的建法）

脚本可重复执行（幂等）；只动户县软面（S091）相关行，不动其他数据。
用法（在 03_服务端 目录或项目根目录均可）：
    python fix_huxian_menu.py            # 预览
    python fix_huxian_menu.py --apply    # 写库
"""
import argparse
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "..", "04_数据库", "数据", "app.db")

SHOP = "户县软面"
NEW_PHOTO = "img/shop-IMG_20261105_154413.jpg"
OLD_PHOTO = "img/shop-IMG_20261008_205431.jpg"
SOURCE = "户县软面价目表实拍（IMG_20261105_154413）"

PRICE_FIXES = {
    "金汤肥牛面": 11.0,
    "酸菜肉丝面": 11.0,
    "酸辣臊子面": 11.0,
    "三鲜臊子面": 11.0,
}

NEW_ADDONS = [
    # name, price, category, tags_str, price_band, allergen
    ("煎蛋", 1.5, "小吃饼类", "小吃饼类,煎蛋,蛋,煎烤", "≤5元", "蛋类"),
    ("卤蛋", 1.5, "小吃饼类", "小吃饼类,卤蛋,蛋,卤酱", "≤5元", "蛋类"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认只预览）")
    args = ap.parse_args()
    if not os.path.exists(DB_PATH):
        sys.exit("找不到数据库：%s" % DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        shop = con.execute("SELECT * FROM shop WHERE shop=?", (SHOP,)).fetchone()
        if not shop:
            sys.exit("找不到档口：%s" % SHOP)
        print("档口 S091 photo: %r -> %r" % (shop["photo"], NEW_PHOTO))
        stmts = [
            ("UPDATE shop SET photo=?, thumb=?, signature=? WHERE shop=?",
             (NEW_PHOTO, NEW_PHOTO, "油泼面", SHOP)),
        ]
        for name, price in PRICE_FIXES.items():
            rows = con.execute(
                "SELECT dish_id, price FROM dish WHERE shop=? AND name=? AND price IS NULL",
                (SHOP, name)).fetchall()
            if rows:
                for r in rows:
                    print("补价 %s %s: None -> %s" % (r["dish_id"], name, price))
                stmts.append((
                    "UPDATE dish SET price=?, price_band=?, price_confidence=?, source=?, updated_at=? "
                    "WHERE shop=? AND name=? AND price IS NULL",
                    (price, band(price), "high", SOURCE, "2026-11-05", SHOP, name)))
            else:
                cur = con.execute("SELECT price FROM dish WHERE shop=? AND name=?", (SHOP, name)).fetchone()
                print("跳过 %s（已有价格 %s）" % (name, cur["price"] if cur else "无此行"))
        _max_id = con.execute("SELECT MAX(dish_id) m FROM dish").fetchone()["m"]
        _next_num = int(_max_id[1:])
        for name, price, category, tags, bandtxt, allergen in NEW_ADDONS:
            exists = con.execute(
                "SELECT dish_id FROM dish WHERE shop=? AND name=? AND is_addon=1", (SHOP, name)).fetchone()
            if exists:
                print("跳过加料 %s（已存在 %s）" % (name, exists["dish_id"]))
                continue
            _next_num += 1
            new_id = "D%04d" % _next_num
            print("新增加料 %s %s ¥%s" % (new_id, name, price))
            stmts.append((
                "INSERT INTO dish (dish_id, shop_id, shop, campus, canteen, floor, category, name, "
                "price, spec, tags_str, price_band, is_addon, is_side, is_recommendable, "
                "price_confidence, allergen_status, allergen_inferred, source, updated_at, img, sold) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id, shop["shop_id"], SHOP, shop["campus"], shop["canteen"], shop["floor"],
                 category, name, price, "", tags, bandtxt, 1, 0, 1, "high", "inferred",
                 allergen, SOURCE, "2026-11-05", "", None)))
        if args.apply:
            try:
                for sql, params in stmts:
                    con.execute(sql, params)
                con.commit()
            except Exception:
                con.rollback()
                raise
            print("\n已写库 %d 条语句。" % len(stmts))
        else:
            print("\n（dry-run：%d 条待执行；加 --apply 写库）" % len(stmts))
    finally:
        con.close()


def band(p):
    if p <= 5:
        return "≤5元"
    if p <= 10:
        return "5–10元"
    if p <= 15:
        return "10–15元"
    return "15元+"


if __name__ == "__main__":
    main()
