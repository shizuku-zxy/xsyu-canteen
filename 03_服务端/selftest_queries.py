# -*- coding: utf-8 -*-
"""端到端回归测试（2026-11 标签/NLU/推荐重构）。

在进程内直接调用 routers.api_recommend.handle（等价 HTTP 请求），检查：
  · 语义相关性：馒头→馒头/馍、稀饭→粥类、馍→馍类、面→面类、炸酱面→炸酱面…
  · 反义词：稀饭不返回米饭；米饭不返回粥
  · 直接命中优先：菜名含查询词的排最前
  · 老功能不回归：预算/忌口/食堂楼层/窗口模式/闲聊/边界守卫
用法（项目根目录或 03_服务端 下均可）：python projects/03_服务端/selftest_queries.py
"""
import os
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(BASE)          # = projects/
sys.path.insert(0, BASE)
os.chdir(BASE)

from routers import api_recommend as A  # noqa: E402

DB = sqlite3.connect(os.path.join(ROOT, "04_数据库", "数据", "app.db"))
DB.row_factory = sqlite3.Row
TAGS = {}
for r in DB.execute("SELECT name, shop, tags_str FROM dish"):
    TAGS.setdefault((r["name"], r["shop"]), set((r["tags_str"] or "").split(",")))

FAILS = []
CHECKS = [0]


def ask(q, **kw):
    p = {"q": q, "intro": "0", "batch": "1"}
    p.update({k: str(v) for k, v in kw.items()})
    return A.handle(p)


def items(r, n=12):
    return (r.get("items") or [])[:n]


def names(r, n=12):
    return [i.get("name") or "" for i in items(r, n)]


def tags_of(item):
    return TAGS.get((item.get("name"), item.get("shop")), set())


def check(label, cond, detail=""):
    CHECKS[0] += 1
    if not cond:
        FAILS.append("%s %s" % (label, detail))
        print("  FAIL %-46s %s" % (label, detail))
    else:
        print("  ok   %s" % label)


def show(q, r, n=6):
    its = items(r, n)
    body = " | ".join("%s(%s%s)" % (
        i.get("name"), i.get("shop"),
        ("" if i.get("price") is None else " ¥%g" % i["price"])) for i in its)
    print("  %-14s mode=%-6s total=%-4s tags=%-28s %s" % (
        q, r.get("mode"), r.get("total_count"),
        ",".join((r.get("normalized") or {}).get("tags") or []) or "-", body))
    return its


def run(q, n=12):
    r = ask(q)
    its = show(q, r, n)
    return r, its


def main():
    print("== 1. 用户点名痛点：馒头 / 馍 / 稀饭 / 粥 / 米饭 ==")
    r, its = run("想吃馒头")
    check("馒头-有结果", r.get("has_result"), r.get("reason"))
    check("馒头-首选菜名直接命中", its and "馒头" in its[0]["name"], its and its[0]["name"])
    check("馒头-前12含至少2道馒头", sum("馒头" in i["name"] for i in its) >= 2,
          str(names(r, 12)))
    check("馒头-没有米饭/粥串味",
          not any(("米饭" in i["name"] or "粥" in i["name"]) for i in its), str(names(r, 12)))

    r, its = run("馍")
    check("馍-有结果", r.get("has_result"), r.get("reason"))
    check("馍-命中馍/馒头类", any(("馍" in i["name"] or "馒头" in i["name"]) for i in its),
          str(names(r, 12)))

    r, its = run("想喝稀饭")
    check("稀饭-有结果", r.get("has_result"), r.get("reason"))
    check("稀饭-结果是粥类", all(("粥" in i["name"] or "稀饭" in i["name"]) for i in its) and bool(its),
          str(names(r, 12)))
    check("稀饭-绝不出现米饭", not any(("米饭" in i["name"] or "盖饭" in i["name"] or "炒饭" in i["name"])
                                     for i in its), str(names(r, 12)))
    check("稀饭-反义词已排除米饭", "米饭" in ((r.get("normalized") or {}).get("exclude") or []),
          str(r.get("normalized", {}).get("exclude")))

    r, its = run("粥")
    check("粥-结果是粥类", all("粥" in i["name"] for i in its) and bool(its), str(names(r, 12)))
    check("粥-不出现米饭", not any("米饭" in i["name"] for i in its), str(names(r, 12)))

    r, its = run("米饭")
    check("米饭-有结果", r.get("has_result"), r.get("reason"))
    check("米饭-不出现粥", not any("粥" in i["name"] for i in its), str(names(r, 12)))

    print("\n== 2. 细分主食 ==")
    for q, must in [("炸酱面", "炸酱面"), ("油泼面", "油泼面"), ("肉夹馍", "肉夹馍"),
                    ("凉皮", "凉皮"), ("螺蛳粉", "螺蛳粉"), ("米线", "米线"),
                    ("豆浆", "豆浆"), ("油条", "油条"), ("煎蛋", "煎蛋"),
                    ("奶茶", "奶茶"), ("蛋挞", "蛋挞"), ("灌汤包", "汤包"),
                    ("花卷", "花卷"), ("馄饨", "馄饨")]:
        r, its = run(q, 8)
        check("%s-首选直接命中" % q, its and must in its[0]["name"], its and its[0]["name"])

    print("\n== 3. 宽口径品类（同义/材料补全） ==")
    r, its = run("面", 12)
    check("面-都是面类", sum(("面" in i["name"] or "麻食" in i["name"] or "馄饨面" in i["name"])
                             for i in its) >= 9, str(names(r, 12)))
    r, its = run("饺子", 8)
    check("饺子-命中饺子/蒸饺/水饺", bool(its) and any(("饺" in i["name"] or "馄饨" in i["name"]) for i in its),
          str(names(r, 8)))
    r, its = run("牛肉", 8)
    check("牛肉-结果含牛肉标签", bool(its) and sum("牛肉" in tags_of(i) for i in its) >= 6,
          str(names(r, 8)))
    r, its = run("辣", 8)
    check("辣-结果含辣标签", bool(its) and sum("辣" in tags_of(i) for i in its) >= 6, str(names(r, 8)))
    r, its = run("素食", 8)
    check("素食-结果含素食标签", bool(its) and sum("素食" in tags_of(i) for i in its) >= 6,
          str(names(r, 8)))
    r, its = run("早餐", 8)
    check("早餐-结果含早餐标签", bool(its) and sum("早餐" in tags_of(i) for i in its) >= 6,
          str(names(r, 8)))

    print("\n== 4. 直接命中优先（菜名含原词排前） ==")
    for q in ["番茄炒蛋", "土豆丝", "香菇", "排骨", "鸡腿"]:
        r, its = run(q, 6)
        check("%s-首选菜名含原词" % q, bool(its) and (q in its[0]["name"] or
              (q == "番茄炒蛋" and ("番茄" in its[0]["name"] or "西红柿" in its[0]["name"]))),
              its and its[0]["name"])

    print("\n== 5. 反义/忌口 ==")
    r, its = run("不要辣的面", 8)
    check("不要辣-排除辣", "辣" in ((r.get("normalized") or {}).get("exclude") or []),
          str(r.get("normalized", {}).get("exclude")))
    check("不要辣-结果不含辣标签", not any("辣" in tags_of(i) for i in its), str(names(r, 8)))
    r, its = run("不吃猪肉", 8)
    check("不吃猪肉-排除猪肉", "猪肉" in ((r.get("normalized") or {}).get("exclude") or []),
          str(r.get("normalized", {}).get("exclude")))
    r, its = run("稀饭不要米饭", 8)
    check("稀饭不要米饭-有粥", bool(its) and all("粥" in i["name"] for i in its), str(names(r, 8)))

    print("\n== 6. 预算 / 位置 / 窗口模式 / 闲聊 ==")
    r, its = run("10块以内的面", 10)
    check("预算-都≤10元", all((i.get("price") is None or i["price"] <= 10) for i in its) and bool(its),
          str([(i["name"], i.get("price")) for i in its]))
    r, its = run("3块左右的粥", 10)
    check("预算-粥类", all("粥" in i["name"] for i in its) and bool(its), str(names(r, 10)))
    r, its = run("15块左右的面", 10)
    check("预算-面类且价位贴合", bool(its) and all(
        (i.get("price") is None or 11 <= i["price"] <= 19) for i in its),
        str([(i["name"], i.get("price")) for i in its]))
    r, its = run("东食堂二楼的面", 10)
    check("位置-都在东食堂二楼", bool(its) and all(
        i.get("canteen") == "东食堂" and i.get("floor") == "二楼" for i in its),
        str([(i["name"], i.get("loc")) for i in its]))
    r, its = run("西食堂一楼有什么好吃的", 8)
    check("位置-窗口模式", r.get("mode") == "window", r.get("mode"))
    r, its = run("今天吃什么", 8)
    check("开放问法-窗口模式", r.get("mode") == "window", r.get("mode"))
    r, its = run("你好")
    check("闲聊-走chat", r.get("mode") == "chat", r.get("mode"))
    r, its = run("67餐厅有什么")
    check("边界守卫-不存在餐厅", (not r.get("has_result")) or "67" in (r.get("reply_text") or ""),
          r.get("reply_text", "")[:60])

    print("\n== 7. 食材/做法/口味 ==")
    for q, tag in [("番茄", None), ("土豆", "蔬菜"), ("豆腐", "豆制品"), ("香菇", "菌菇"),
                   ("排骨", "猪肉"), ("鸡蛋", "蛋"), ("海鲜", "鱼虾")]:
        r, its = run(q, 6)
        if tag:
            check("%s-结果多为%s" % (q, tag), sum(tag in tags_of(i) for i in its) >= 4,
                  str(names(r, 6)))
        else:
            check("%s-首选直接命中" % q, bool(its) and (q in its[0]["name"] or "西红柿" in its[0]["name"]),
                  its and its[0]["name"])
    for q, tag in [("炸的", "炸"), ("蒸的", "蒸"), ("卤味", "卤酱"), ("清淡", "清淡")]:
        r, its = run(q, 6)
        check("%s-结果含%s" % (q, tag), bool(its) and sum(tag in tags_of(i) for i in its) >= 4,
              str(names(r, 6)))

    print("\n== 8. 换一批（batch 稳定性） ==")
    a = ask("面", batch="1")
    b = ask("面", batch="1")
    c = ask("面", batch="2")
    check("同问同批一致", [i["name"] for i in items(a, 12)] == [i["name"] for i in items(b, 12)])
    check("换批会变化", [i["name"] for i in items(a, 12)] != [i["name"] for i in items(c, 12)]
          or a.get("total_count", 0) < 12)

    print("\n" + "=" * 70)
    print("检查项 %d，失败 %d" % (CHECKS[0], len(FAILS)))
    for f in FAILS:
        print("  -", f)
    print("RESULT:", "PASS" if not FAILS else "FAIL")


if __name__ == "__main__":
    main()
