# -*- coding: utf-8 -*-
"""全库菜品标签标注 / 合并 / 派生刷新脚本（2026-11，"标签越多，回应越接近"）。

三件事：
  1. ``--baseline-dir DIR``  按 lexicon 规则生成全库基线标签，导出到各楼层 JSON（供子代理复核）
  2. ``--merge-dir DIR``     把子代理复核后的各楼层 JSON 合并回全库（未覆盖的菜用 lexicon 基线兜底）
  3. ``--apply``             写回 app.db：dish.tags_str + tag 表 + dish_fts 重建 + shop 统计刷新

用法（在项目根目录）：
    python projects/03_服务端/tag_dishes.py --baseline-dir _work/tags_baseline
    python projects/03_服务端/tag_dishes.py --merge-dir _work/tags --apply

子代理输出 JSON 格式：{"floor":"东食堂-一楼","tags":{"D0001":["面",...]},"new_tags":{"标签":"细分"}}
"""
import argparse
import glob
import json
import os
import sqlite3
import sys
from collections import Counter

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from services import lexicon  # noqa: E402

DB_PATH = os.path.join(BASE, "..", "04_数据库", "数据", "app.db")


def floor_slug(canteen, floor):
    c = (canteen or "").strip() or "未知食堂"
    f = (floor or "").strip()
    return "%s-%s" % (c, f if f else "street")


def load_dishes(con):
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT dish_id, shop, campus, canteen, floor, category, name, price, spec, "
        "tags_str, is_addon, is_side, is_recommendable, img FROM dish ORDER BY dish_id").fetchall()
    return [dict(r) for r in rows]


def build_baseline(dishes):
    out, changed, errs = {}, 0, []
    for d in dishes:
        old = [t for t in (d.get("tags_str") or "").split(",") if t.strip()]
        try:
            new = lexicon.tags_for_dish(d["name"], d.get("category") or "", old, d.get("shop"))
        except Exception as e:  # 单条脏数据不能拖垮整库
            errs.append((d["dish_id"], d["name"], "%s: %s" % (type(e).__name__, e)))
            new = old
        out[d["dish_id"]] = list(new)
        if list(old) != list(new):
            changed += 1
    return out, changed, errs


def write_baseline(dishes, result, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    groups = {}
    for d in dishes:
        groups.setdefault(floor_slug(d["canteen"], d["floor"]), []).append(d)
    manifest = {}
    for slug, rows in groups.items():
        payload = [{
            "dish_id": d["dish_id"], "name": d["name"], "shop": d["shop"],
            "canteen": d["canteen"], "floor": d["floor"], "category": d["category"],
            "price": d["price"], "spec": d["spec"], "is_addon": d["is_addon"],
            "old_tags": [t for t in (d["tags_str"] or "").split(",") if t],
            "new_tags": result[d["dish_id"]],
        } for d in rows]
        path = os.path.join(out_dir, slug + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        manifest[slug] = {"count": len(payload), "path": path}
    with open(os.path.join(out_dir, "_manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    return manifest


def merge_floor_tags(result, merge_dir):
    """把子代理的楼层终稿并进 result；返回 (覆盖道数, 未知标签, 声明的 new_tags)。"""
    covered, unknown = 0, Counter()
    declared = {}
    files = sorted(glob.glob(os.path.join(merge_dir, "*.json")))
    merged_floors = []
    for path in files:
        if os.path.basename(path).startswith("_"):
            continue
        try:
            doc = json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print("  [skip] %s 读取失败：%s" % (path, e))
            continue
        tags_map = doc.get("tags") if isinstance(doc, dict) else None
        if not isinstance(tags_map, dict):
            print("  [skip] %s 没有 tags 对象" % path)
            continue
        for t, dim in (doc.get("new_tags") or {}).items():
            declared[str(t)] = str(dim)
        floor = doc.get("floor") or os.path.basename(path)[:-5]
        n = 0
        for did, tags in tags_map.items():
            if did not in result:
                continue
            if not isinstance(tags, list):
                continue
            clean = []
            for t in tags:
                t = str(t or "").strip()
                if t and t not in clean:
                    clean.append(t)
            if not clean:
                continue
            for t in clean:
                if t not in lexicon.TAG_DIM and t not in declared:
                    unknown[t] += 1
            result[did] = clean
            n += 1
        covered += n
        merged_floors.append((floor, n))
    return covered, unknown, declared, merged_floors


def sync_tag_table(con, declared):
    """把 lexicon.TAG_DIM + 子代理声明的 new_tags 补进 tag 表。"""
    con.row_factory = sqlite3.Row
    have = {r["tag"]: r["dimension"] for r in con.execute("SELECT tag, dimension FROM tag")}
    max_id = con.execute("SELECT COALESCE(MAX(tag_id),'T00') m FROM tag").fetchone()["m"]
    try:
        n = int(str(max_id)[1:])
    except ValueError:
        n = 0
    todo = dict(lexicon.TAG_DIM)
    for t, dim in declared.items():
        if dim in lexicon.DIM_ORDER:
            todo.setdefault(t, dim)
    added = updated = 0
    for tag, dim in todo.items():
        if tag in have:
            if have[tag] != dim:
                con.execute("UPDATE tag SET dimension=? WHERE tag=?", (dim, tag))
                updated += 1
            continue
        n += 1
        con.execute("INSERT INTO tag (tag_id, dimension, tag) VALUES (?,?,?)",
                    ("T%02d" % n, dim, tag))
        added += 1
    return added, updated


def rebuild_fts(con):
    con.row_factory = sqlite3.Row
    n = con.execute("SELECT COUNT(*) c FROM dish_fts").fetchone()["c"]
    con.execute("DELETE FROM dish_fts")
    rows = con.execute("SELECT dish_id, name, COALESCE(tags_str,'') tags_str, "
                       "COALESCE(shop,'') shop, COALESCE(category,'') category FROM dish").fetchall()
    con.executemany("INSERT INTO dish_fts (dish_id, name, tags_str, shop, category) VALUES (?,?,?,?,?)",
                    [(r["dish_id"], r["name"], r["tags_str"], r["shop"], r["category"]) for r in rows])
    return n, len(rows)


# 统计刷新时不参与 shop.tags 的过泛标签（否则每个档口都带这几个，搜索区分度变差）
_SHOP_TAG_SKIP = {"主食", "快餐", "小吃饼类"}


def refresh_shops(con, topn=18):
    """按 dish 表重算 shop 的菜数/价格区间/标签画像（front-end 档口卡与档口搜索用）。"""
    con.row_factory = sqlite3.Row
    shops = con.execute("SELECT shop_id, shop, canteen, floor FROM shop").fetchall()
    changed = 0
    for s in shops:
        rows = con.execute(
            "SELECT tags_str, price, is_recommendable FROM dish WHERE shop=? AND canteen=? AND floor=?",
            (s["shop"], s["canteen"], s["floor"])).fetchall()
        if not rows:
            continue
        rec = [r for r in rows if str(r["is_recommendable"]) == "1"]
        prices = [float(r["price"]) for r in rec if r["price"] is not None]
        cnt = Counter()
        taste = Counter()
        for r in rec:
            for t in (r["tags_str"] or "").split(","):
                t = t.strip()
                if t and t not in _SHOP_TAG_SKIP:
                    cnt[t] += 1
                if t in ("辣", "麻", "酸", "甜", "咸", "鲜", "清淡"):
                    taste[t] += 1
        tags = ",".join(t for t, _ in cnt.most_common(topn))
        n_rec = max(1, len(rec))
        prof = "辣%d%% 麻%d%% 酸%d%% 甜%d%% 咸%d%% 清淡%d%% 荤%d%% 素%d%%" % (
            round(100.0 * taste["辣"] / n_rec), round(100.0 * taste["麻"] / n_rec),
            round(100.0 * taste["酸"] / n_rec), round(100.0 * taste["甜"] / n_rec),
            round(100.0 * taste["咸"] / n_rec), round(100.0 * taste["清淡"] / n_rec),
            round(100.0 * sum(1 for r in rec if "荤菜" in (r["tags_str"] or "")) / n_rec),
            round(100.0 * sum(1 for r in rec if "素食" in (r["tags_str"] or "")) / n_rec))
        con.execute(
            "UPDATE shop SET dish_count=?, dish_count_rec=?, price_min=?, price_max=?, price_avg=?, "
            "tags=?, taste_profile=? WHERE shop_id=?",
            (len(rows), len(rec), min(prices) if prices else None,
             max(prices) if prices else None,
             round(sum(prices) / len(prices), 4) if prices else None,
             tags, prof, s["shop_id"]))
        changed += 1
    return changed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="写回 app.db")
    ap.add_argument("--baseline-dir", default="", help="输出各楼层基线 JSON 的目录")
    ap.add_argument("--merge-dir", default="", help="读入子代理终稿 JSON 的目录")
    ap.add_argument("--limit", type=int, default=0, help="只处理前 N 条（调试用）")
    args = ap.parse_args()

    if not os.path.exists(DB_PATH):
        sys.exit("找不到数据库：%s" % DB_PATH)
    con = sqlite3.connect(DB_PATH)
    dishes = load_dishes(con)
    if args.limit:
        dishes = dishes[:args.limit]
    result, changed, errs = build_baseline(dishes)
    print("基线：%d 道菜、%d 个标签（平均 %.1f 个/道），相对旧数据变化 %d 道"
          % (len(dishes), sum(len(v) for v in result.values()),
             sum(len(v) for v in result.values()) / max(1, len(dishes)), changed))
    for did, nm, err in errs[:10]:
        print("  [err] %s %s -> %s" % (did, nm, err))

    declared = {}
    if args.merge_dir:
        covered, unknown, declared, floors = merge_floor_tags(result, args.merge_dir)
        print("合并子代理终稿：覆盖 %d 道" % covered)
        for floor, n in floors:
            print("  - %s：%d 道" % (floor, n))
        if unknown:
            print("  [warn] 未声明的标签（按 细分 维度入库，请人工复核）：",
                  dict(unknown.most_common(20)))
            for t in unknown:
                declared.setdefault(t, "细分")

    n_tags = sum(len(v) for v in result.values())
    cnt = Counter(t for v in result.values() for t in v)
    empty = [k for k, v in result.items() if not v]
    print("最终：%d 道菜、%d 个标签（平均 %.1f 个/道），空标签 %d 道"
          % (len(result), n_tags, n_tags / max(1, len(result)), len(empty)))
    print("高频标签:", " ".join("%s:%d" % (t, n) for t, n in cnt.most_common(25)))

    if args.baseline_dir:
        m = write_baseline(dishes, result, args.baseline_dir)
        print("基线已写出：", {k: v["count"] for k, v in m.items()})

    if args.apply:
        con.execute("BEGIN")
        try:
            con.executemany("UPDATE dish SET tags_str=? WHERE dish_id=?",
                            [(",".join(v), k) for k, v in result.items()])
            added, updated = sync_tag_table(con, declared)
            _old, n_fts = rebuild_fts(con)
            n_shop = refresh_shops(con)
            con.commit()
        except Exception:
            con.rollback()
            raise
        print("已写库：tags_str %d 条；tag 表 +%d/~%d；FTS 重建 %d 条；shop 统计刷新 %d 个"
              % (len(result), added, updated, n_fts, n_shop))
    else:
        print("（dry-run：未写库；加 --apply 写回）")
    con.close()


if __name__ == "__main__":
    main()
