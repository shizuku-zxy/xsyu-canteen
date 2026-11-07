# -*- coding: utf-8 -*-
"""补齐 38 道缺失价格（2026-11）。

数据来源分两类（evidence 逐条记录）：
  · high —— 档口价目表实拍读数（img/shop-*.jpg，老班长/蜀湘素/铁板煎肉饭/螺蛳粉）
  · low  —— 同档口/同类参考价推算（小吃街 4 个无实拍档口 + 天成小吃/益禾堂部分）
另有 2 条公告/选项类脏数据不填价、不参与推荐：D1919 / D1965。

户县软面 4 道缺价已由 fix_huxian_menu.py 按新价目表补 11 元，本脚本跳过已有价格的菜。
脚本可重复执行（幂等）。用法（项目根目录）：
    python projects/03_服务端/fill_missing_prices.py            # 预览
    python projects/03_服务端/fill_missing_prices.py --apply    # 写库
"""
import argparse
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "..", "04_数据库", "数据", "app.db")

PRICES = {
 "D1147": {
  "price": 8.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155541.jpg（老班长快餐价目表） 右列单品区“珍珠丸子 8.0元/份”"
 },
 "D1148": {
  "price": 8.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155541.jpg（老班长快餐价目表） 右列单品区“米粉蒸肉 8.0元/份”"
 },
 "D1149": {
  "price": 8.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155541.jpg（老班长快餐价目表） 右列单品区“梅菜扣肉 8.0元/份”"
 },
 "D1150": {
  "price": 10.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155541.jpg（老班长快餐价目表） 右列单品区“糯米排骨 10.0元/份”"
 },
 "D1459": {
  "price": 7.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155858.jpg（蜀湘素快餐价目表） 左侧单品框“鸭腿 7元”"
 },
 "D1460": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155858.jpg（蜀湘素快餐价目表） 左侧单品框“鸡腿 5元”"
 },
 "D1461": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155858.jpg（蜀湘素快餐价目表） 左侧单品框“糖醋里脊 5元”"
 },
 "D1462": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155858.jpg（蜀湘素快餐价目表） 左侧单品框“烤肉 5元”"
 },
 "D1463": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155858.jpg（蜀湘素快餐价目表） 左侧单品框“椒麻鸭 5元”"
 },
 "D1403": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 中列“烤肉 5元”"
 },
 "D1404": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 中列“椒麻鸡 5元”"
 },
 "D1405": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 中列“金牌辣子鸡 5元”"
 },
 "D1406": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 中列“黄焖鸡块 5元”"
 },
 "D1407": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 右列“卤鸡腿 5元”"
 },
 "D1408": {
  "price": 7.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 右列“烤鸭腿 7元”"
 },
 "D1409": {
  "price": 7.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 右列“卤鸭腿 7元”"
 },
 "D1410": {
  "price": 5.0,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261010_155648.jpg（铁板煎肉饭价目表） 右列“炸鸡腿 5元”（价格处有贴章遮挡，放大后仍可辨 5 元）"
 },
 "D0059": {
  "price": 1.5,
  "confidence": "high",
  "source": "档口价目表实拍",
  "evidence": "价目表实拍：img/shop-IMG_20261008_205456.jpg（螺蛳粉价目表） “精选小吃”区确有“卤蛋”（与“煎蛋 1.5”同排），标价仅“1.”部分可辨、末位紧贴图片右缘（原图裁切）；结合同排煎蛋 1.5 元及全校 7 家档口卤蛋均 1.5 元（D0212/D0245/D0606/D0690/D0812/D1340/D2065），判定 1.5 元"
 },
 "D0063": {
  "price": 2.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：螺蛳粉价目表右下角有“玉米粥—¥□”，但价格被图片右缘裁切不可读；参照同店最低价（煎蛋 1.5 元）及一粥坊/五谷营养粥同类粥 2.0~3.0 元（D0123/D0979/D0980 等 2.0 元），取 2.0"
 },
 "D1905": {
  "price": 5.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：该档口无价目表实拍（shop.photo 为空，sign_img 仅为菜品图）；基础价参照小吃街同街“鸡蛋花干夹馍 5.0 元”(D1814)、西食堂“菜夹馍 4.0 元”(D0177)、“花干鸡蛋夹馍 5.0 元”(D0639)"
 },
 "D1906": {
  "price": 6.5,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：基础加馍 5.0 元 + 鸡蛋 1.5 元（参照全校卤蛋/鸡蛋 1.5 元，如 D0875）= 6.5"
 },
 "D1907": {
  "price": 7.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：基础加馍 5.0 元 + 烤肠 2.0 元（参照 D0188 烤肠 1.5 元、小吃街加肠类约 +2 元）= 7.0"
 },
 "D1908": {
  "price": 8.5,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：基础加馍 5.0 + 鸡蛋 1.5 + 烤肠 2.0 = 8.5"
 },
 "D1909": {
  "price": 5.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：拌饼与加馍同料同价，按基础 5.0 元（参照 D1814 鸡蛋花干夹馍 5.0 元）"
 },
 "D1910": {
  "price": 6.5,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：拌饼基础 5.0 + 鸡蛋 1.5 = 6.5"
 },
 "D1911": {
  "price": 7.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：拌饼基础 5.0 + 烤肠 2.0 = 7.0"
 },
 "D1912": {
  "price": 8.5,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：拌饼基础 5.0 + 鸡蛋 1.5 + 烤肠 2.0 = 8.5"
 },
 "D1880": {
  "price": 16.9,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：菜品图（img/dish/de164c12…jpg）显示为“人气腿堡尝鲜 2 件套（腿堡任选 + 可乐/七喜）”；单点香辣鸡腿中国汉堡 14.0(D1887) + 冰柠可乐 6.0(D1900) = 20.0，同店三件套“香辣鸡腿堡+鸡块+可乐”20.9(D1883)、“百万人气双堡单人餐”18.9(D1881)，按同类套餐折扣取 16.9"
 },
 "D1634": {
  "price": 10.9,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：同店“灌汤包+现磨豆浆 13.9”(D1643) − 豆浆 3.0 = 10.9；“灌汤包+紫菜蛋花汤 15.9”(D1640) − 蛋花汤 5.0 = 10.9；与“灌汤包+拌面 21.9 − 拌面 12.0”约 9.9 相印证，取 10.9"
 },
 "D1635": {
  "price": 10.9,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：同店“牛肉蒸饺+豆浆 13.9”(D1645) − 豆浆 3.0 = 10.9；“牛肉蒸饺+凉皮 20.0”(D1632) − 凉皮 9.0 ≈ 11.0，取 10.9"
 },
 "D1644": {
  "price": 11.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：同店“灌汤包+肉丸汤 21.9”(D1639) − 灌汤包 10.9 = 11.0；“肉丸汤+方便面 14.0”(D1641) 亦支持 10~11 元区间，取 11.0"
 },
 "D1649": {
  "price": 3.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：同店现磨冰豆浆 3.0 元(D1636/D1654)，现磨豆浆（热）同品同价取 3.0；“灌汤包+现磨豆浆 13.9 − 灌汤包 10.9 = 3.0”互相验证"
 },
 "D1836": {
  "price": 11.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：同店奶绿/茉莉系 6.0~8.0 元（D1830 禾风奶绿 7.0、D1849 薄荷奶绿 7.9、D1833 翠峰茉莉奶盖 8.0）、烧仙草 12.0、霸王桶 14~15；产品图（img/dish/bb7af82f…jpg）为益禾堂“轻乳茶”系列，属中档奶茶，取 11.0"
 },
 "D1861": {
  "price": 11.0,
  "confidence": "low",
  "source": "估价（参考同档口同类）",
  "evidence": "估价：与“四季春轻乳茶”同系列同价，参照同店奶绿/茉莉系 6.0~8.0、烧仙草 12.0，取 11.0"
 }
}

JUNK = ["D1919", "D1965"]


def band(p):
    if p <= 5:
        return "≤5元"
    if p <= 10:
        return "5–10元"
    if p <= 15:
        return "10–15元"
    return "15元+"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写库（默认只预览）")
    args = ap.parse_args()
    if not os.path.exists(DB_PATH):
        sys.exit("找不到数据库：%s" % DB_PATH)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    todo = []
    for did, info in PRICES.items():
        row = con.execute("SELECT dish_id, name, price FROM dish WHERE dish_id=?", (did,)).fetchone()
        if not row:
            print("[skip] %s 不存在" % did)
            continue
        if row["price"] is not None:
            print("[skip] %s %s 已有价格 %s" % (did, row["name"], row["price"]))
            continue
        price = float(info["price"])
        print("%s %s：None -> %s（%s）" % (did, row["name"], price, info["confidence"]))
        todo.append((price, band(price), info["confidence"], info["source"], did))
    print("\n待补 %d 道；脏数据（不参与推荐）：%s" % (len(todo), "、".join(JUNK)))
    if args.apply and todo:
        try:
            con.executemany(
                "UPDATE dish SET price=?, price_band=?, price_confidence=?, source=?, updated_at=? "
                "WHERE dish_id=?",
                [(p, b, c, s, "2026-11-05", did) for (p, b, c, s, did) in todo])
            con.commit()
        except Exception:
            con.rollback()
            raise
        print("已写库 %d 条。" % len(todo))
    elif not args.apply:
        print("（dry-run：加 --apply 写库）")
    con.close()


if __name__ == "__main__":
    main()
