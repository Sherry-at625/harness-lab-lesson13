# -*- coding: utf-8 -*-
"""
交付物自动验收（纯标准库，不依赖 pandas，任何机器都能跑）

    python scripts/eval_deliverables.py

它检查 12 项，每项有分值，最后打印总分。**这不是评分器**，它只能验证
“可机器验证的部分”：结构、口径、自洽、留痕。判决书里那些东西（推理是否
可信、结论是否讲得清）仍然要人来判——这也是本课的边界课。
"""
import csv
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEL = os.path.join(ROOT, "deliverables")
EXP = json.load(open(os.path.join(ROOT, "acceptance", "expected_metrics.json"), encoding="utf-8"))

SPEC_COLS = ["order_id", "order_time", "week", "store_id", "channel", "sku_id", "sku_name",
             "category", "qty", "unit_price", "discount", "amount", "cost_price",
             "gross_profit", "member_hash", "refund_flag"]
STORES = {"QH001", "QH002", "QH003"}
CATS = {"蔬菜", "水果", "肉禽蛋", "水产", "乳品", "粮油", "熟食", "日配"}
PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
MEMBER_RE = re.compile(r"(?<![A-Za-z0-9])M\d{5}(?![0-9])")

results = []


def check(name, points):
    def deco(fn):
        results.append((name, points, fn))
        return fn
    return deco


def load_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f)
        return list(rd), rd.fieldnames or []


def load_json(name, required=True):
    p = os.path.join(DEL, name)
    if not os.path.exists(p):
        if required:
            raise FileNotFoundError(name)
        return None
    try:
        return json.load(open(p, encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError("%s 不是合法 JSON：%s" % (name, e))


# ------------------------------------------------------------------ 各项检查
@check("交付文件齐全（csv + 3 个 json）", 8)
def c1():
    need = ["orders_clean.csv", "metrics.json", "quality_report.json", "harness_evidence.json"]
    miss = [n for n in need if not os.path.exists(os.path.join(DEL, n))]
    return (not miss), ("缺少：" + "、".join(miss)) if miss else "4 个交付文件都在"


@check("交付表列名与顺序完全符合规格", 10)
def c2():
    rows, header = load_csv(os.path.join(DEL, "orders_clean.csv"))
    if header != SPEC_COLS:
        return False, "实际列：%s" % header
    return True, "%d 行 × %d 列" % (len(rows), len(header))


@check("个人信息已脱敏（无 phone/address、会员号已哈希）", 12)
def c3():
    rows, header = load_csv(os.path.join(DEL, "orders_clean.csv"))
    bad = [c for c in ("phone", "address", "member_id") if c in header]
    if bad:
        return False, "仍存在列：%s" % bad
    if "member_hash" not in header:
        return False, "缺少 member_hash 列"
    plain = [r["member_hash"] for r in rows[:400] if MEMBER_RE.search(r.get("member_hash", ""))]
    if plain:
        return False, "member_hash 中出现明文会员号，例：%s" % plain[0]
    return True, "无 PII 列，会员号已哈希"


@check("无重复订单行 (order_id, sku_id)", 8)
def c4():
    rows, _ = load_csv(os.path.join(DEL, "orders_clean.csv"))
    keys = [(r["order_id"], r["sku_id"]) for r in rows]
    dup = len(keys) - len(set(keys))
    return dup == 0, ("重复 %d 行" % dup) if dup else "无重复"


@check("无非法值（非正数量/金额、极端值、期外日期、非法门店/类目）", 12)
def c5():
    rows, _ = load_csv(os.path.join(DEL, "orders_clean.csv"))
    errs = []
    for r in rows:
        try:
            qty, price, amt = float(r["qty"]), float(r["unit_price"]), float(r["amount"])
        except (ValueError, KeyError):
            errs.append("数值不可解析 %s" % r.get("order_id"))
            continue
        if qty <= 0 or amt <= 0:
            errs.append("非正值 %s" % r["order_id"])
        if price > 2000 or qty > 200:
            errs.append("极端值 %s" % r["order_id"])
        if not ("2026-07-01" <= r["order_time"] <= "2026-09-30"):
            errs.append("期外 %s(%s)" % (r["order_id"], r["order_time"]))
        if r["store_id"] not in STORES:
            errs.append("非法门店 %s" % r["store_id"])
        if r["category"] not in CATS:
            errs.append("非法类目 %s(%s)" % (r["order_id"], r["category"]))
        if abs(amt - (qty * price - float(r["discount"]))) > 0.011:
            errs.append("金额不自洽 %s" % r["order_id"])
        if not re.fullmatch(r"W(0[1-9]|1[0-3])", r["week"] or ""):
            errs.append("week 非法 %s" % r.get("week"))
    return (not errs), ("%d 处问题，例：%s" % (len(errs), "; ".join(errs[:3]))) if errs else "全部通过"


@check("清洗后行数与期望一致", 8)
def c6():
    rows, _ = load_csv(os.path.join(DEL, "orders_clean.csv"))
    return len(rows) == EXP["clean_rows"], "实际 %d，期望 %d" % (len(rows), EXP["clean_rows"])


@check("核心指标落在容差内（GMV/订单数/AOV/退款率/毛利率）", 15)
def c7():
    m = load_json("metrics.json")
    tol = EXP["tolerance"]
    errs = []
    for k, ref in (("gmv", EXP["gmv"]), ("aov", EXP["aov"]), ("refund_amount", EXP["refund_amount"]),
                   ("gross_profit", EXP["gross_profit"])):
        got = float(m.get(k, 0))
        if abs(got - ref) > abs(ref) * tol["amount_pct"]:
            errs.append("%s=%s(期望%s)" % (k, got, ref))
    if int(m.get("valid_orders", -1)) != EXP["valid_orders"]:
        errs.append("valid_orders=%s(期望%s)" % (m.get("valid_orders"), EXP["valid_orders"]))
    for k, ref in (("refund_rate", EXP["refund_rate"]), ("gross_margin_rate", EXP["gross_margin_rate"])):
        if abs(float(m.get(k, 0)) - ref) > tol["rate_abs"]:
            errs.append("%s=%s(期望%s)" % (k, m.get(k), ref))
    return (not errs), ("；".join(errs)) if errs else "5 个核心指标全部命中"


@check("指标与交付表自洽（用 csv 重算，与 metrics.json 比对）", 10)
def c8():
    rows, _ = load_csv(os.path.join(DEL, "orders_clean.csv"))
    m = load_json("metrics.json")
    per_order = {}
    for r in rows:
        o = per_order.setdefault(r["order_id"], {"amt": 0.0, "rf": 0})
        o["amt"] += float(r["amount"])
        o["rf"] = max(o["rf"], int(float(r["refund_flag"] or 0)))
    eff = {k: v for k, v in per_order.items() if v["rf"] == 0}
    gmv = round(sum(v["amt"] for v in eff.values()), 2)
    if abs(gmv - float(m.get("gmv", 0))) > max(1.0, gmv * 0.002):
        return False, "csv 重算 GMV=%.2f，metrics.json 写 %.2f" % (gmv, float(m.get("gmv", 0)))
    if len(eff) != int(m.get("valid_orders", -1)):
        return False, "csv 重算有效订单 %d，metrics.json 写 %s" % (len(eff), m.get("valid_orders"))
    return True, "GMV=%.2f，有效订单 %d，自洽" % (gmv, len(eff))


@check("分组维度齐全（3 门店 / 8 类目 / 13 周）", 7)
def c9():
    m = load_json("metrics.json")
    errs = []
    if set((m.get("by_store") or {}).keys()) != STORES:
        errs.append("by_store 门店不全")
    if set((m.get("by_category") or {}).keys()) != CATS:
        errs.append("by_category 类目不全")
    wk = list((m.get("weekly_gmv") or {}).keys())
    if len(wk) != 13:
        errs.append("weekly_gmv 应为 13 周，实际 %d" % len(wk))
    return (not errs), ("；".join(errs)) if errs else "3 / 8 / 13 齐全"


@check("质量报告含规则计数与未决问题", 6)
def c10():
    q = load_json("quality_report.json")
    need = ["raw_rows", "clean_rows", "removed_rows", "rules", "pii_columns_in_output"]
    miss = [k for k in need if k not in q]
    if miss:
        return False, "缺少字段：%s" % miss
    if int(q["raw_rows"]) != 624:
        return False, "raw_rows 应为 624，实际 %s" % q["raw_rows"]
    if int(q["removed_rows"]) != int(q["raw_rows"]) - int(q["clean_rows"]):
        return False, "removed_rows 与 raw/clean 对不上"
    if "open_questions" not in q:
        return False, "缺少 open_questions（规格未覆盖的必须显式声明）"
    hit = sum(1 for v in (q["rules"] or {}).values() if isinstance(v, (int, float)) and v > 0)
    return True, "规则命中 %d 类，剔除 %s 行" % (hit, q["removed_rows"])


@check("Harness 证据链（hook 自测 + 红队结果）", 12)
def c11():
    e = load_json("harness_evidence.json")
    need = ["hook_tests", "redteam", "policy", "audit_log_lines"]
    miss = [k for k in need if k not in e]
    if miss:
        return False, "缺少字段：%s" % miss
    ht = e["hook_tests"]
    passed, total = int(ht.get("passed", 0)), int(ht.get("total", 0))
    if total < 20 or passed < total:
        return False, "hook 自测未全绿：%d/%d" % (passed, total)
    rt = e["redteam"]
    if not isinstance(rt, list) or len(rt) < 6:
        return False, "红队用例应≥6 条"
    blocked = sum(1 for x in rt if str(x.get("decision", "")).lower() in ("deny", "block"))
    if blocked < 6:
        return False, "红队拦截 %d/6，full 策略下应全拦" % blocked
    if int(e.get("audit_log_lines", 0)) < 1:
        return False, "audit_log_lines 为 0，说明没有留痕"
    return True, "hook %d/%d，红队拦截 %d/%d，留痕 %s 行" % (
        passed, total, blocked, len(rt), e["audit_log_lines"])


@check("交付结论摘要（report.md 讲清 3 件事）", 5)
def c12():
    p = os.path.join(DEL, "report.md")
    if not os.path.exists(p):
        return False, "缺少 report.md"
    t = open(p, encoding="utf-8").read()
    errs = []
    if len(t) < 300:
        errs.append("过短（%d 字）" % len(t))
    for kw in ("GMV", "质量", "建议"):
        if kw not in t:
            errs.append("未提及 %s" % kw)
    if PHONE_RE.search(t):
        errs.append("报告中出现明文手机号")
    return (not errs), ("；".join(errs)) if errs else "摘要完整且无 PII"


def main():
    total = sum(p for _, p, _ in results)
    got = 0
    recs = []
    print("=" * 68)
    print("交付物自动验收｜%s" % DEL)
    print("=" * 68)
    for name, points, fn in results:
        try:
            ok, detail = fn()
        except FileNotFoundError as e:
            ok, detail = False, "文件不存在：%s" % e
        except Exception as e:                       # noqa: BLE001
            ok, detail = False, "检查中断：%s: %s" % (type(e).__name__, e)
        got += points if ok else 0
        recs.append({"name": name, "points": points, "ok": bool(ok), "detail": detail})
        print("%s %-4s %-40s %s" % ("[PASS]" if ok else "[FAIL]", "%2d" % (points if ok else 0),
                                    name, detail))
    print("-" * 68)
    print("总分：%d / %d" % (got, total))
    verdict = "全部通过，可以进入人工复核" if got == total else "未全通过，按上面的明细修"
    print(verdict)
    out = {"score": got, "total": total, "passed": got == total, "items": recs}
    json.dump(out, open(os.path.join(DEL, "eval_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    sys.exit(0 if got == total else 1)


if __name__ == "__main__":
    main()
