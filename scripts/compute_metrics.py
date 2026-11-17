# -*- coding: utf-8 -*-
"""
指标计算（metrics_spec.md 口径）—— 纯标准库，Decimal 精度，确定性输出

    python scripts/compute_metrics.py

输入：deliverables/orders_clean.csv、data/out/clean_stats.json
输出：deliverables/metrics.json

口径要点（每一条都对应文档里的一个"常见错误"）：
  O = 清洗后全部订单（含退款），R = refund_flag=1 的订单，E = O - R
  GMV        = Σ amount，**仅 E**（不扣退款；退款另算）
  有效订单数  = distinct order_id，**仅 E**（不是行数，也不是 O）
  AOV        = GMV / 有效订单数（分母是订单，不是行）
  退款率      = distinct order_id(R) / distinct order_id(O)   ← 分母是 O
  毛利额      = Σ(amount - qty*cost_price)，仅 E；成本取主数据（不是 discount！）
"""
import csv
import json
import os
import sys
from collections import OrderedDict
from decimal import Decimal, ROUND_HALF_UP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLEAN = os.path.join(ROOT, "deliverables", "orders_clean.csv")
STATS = os.path.join(ROOT, "data", "out", "clean_stats.json")
OUT = os.path.join(ROOT, "deliverables", "metrics.json")

Q2 = Decimal("0.01")


def d(v):
    s = str(v or "").strip()
    return Decimal(s) if s else Decimal("0")


def r2(x):
    return float(Decimal(x).quantize(Q2, rounding=ROUND_HALF_UP))


def r4(x):
    return float(Decimal(x).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def main():
    with open(CLEAN, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))

    # 订单级聚合：一单多行时 GMV 按行累加，订单数按 order_id 去重
    per_order = OrderedDict()
    for r in rows:
        o = per_order.setdefault(r["order_id"], {"amt": Decimal("0"), "gp": Decimal("0"),
                                                 "rf": 0, "store": r["store_id"]})
        o["amt"] += d(r["amount"])
        o["gp"] += d(r["amount"]) - d(r["qty"]) * d(r["cost_price"])
        o["rf"] = max(o["rf"], int(d(r["refund_flag"])))

    O = list(per_order.keys())
    R = [k for k in O if per_order[k]["rf"] == 1]
    E = [k for k in O if per_order[k]["rf"] == 0]

    gmv = sum((per_order[k]["amt"] for k in E), Decimal("0"))
    gross_profit = sum((per_order[k]["gp"] for k in E), Decimal("0"))
    refund_total = sum((Decimal(x["refund_amount"]) for x in
                        json.load(open(STATS, encoding="utf-8"))["refunds_kept"]), Decimal("0"))

    metrics = OrderedDict()
    metrics["period"] = "2026-07-01~2026-09-30"
    metrics["gmv"] = r2(gmv)
    metrics["valid_orders"] = len(E)
    metrics["all_checked_orders"] = len(O)
    metrics["aov"] = r2(gmv / len(E)) if E else 0.0
    metrics["refund_orders"] = len(R)
    metrics["refund_rate"] = r4(Decimal(len(R)) / Decimal(len(O))) if O else 0.0
    metrics["refund_amount"] = r2(refund_total)
    metrics["gross_profit"] = r2(gross_profit)
    metrics["gross_margin_rate"] = r4(gross_profit / gmv) if gmv else 0.0
    metrics["row_count"] = len(rows)

    # by_store：GMV / 有效订单数 / AOV，GMV 降序、同值 store_id 升序
    store = {}
    for k in E:
        s = store.setdefault(per_order[k]["store"], {"gmv": Decimal("0"), "orders": 0})
        s["gmv"] += per_order[k]["amt"]
        s["orders"] += 1
    metrics["by_store"] = OrderedDict(
        (sid, {"gmv": r2(v["gmv"]), "orders": v["orders"],
               "aov": r2(v["gmv"] / v["orders"]) if v["orders"] else 0.0})
        for sid, v in sorted(store.items(), key=lambda kv: (-kv[1]["gmv"], kv[0])))

    # by_category：GMV / qty，仅 E，GMV 降序、同值类目名升序
    cat = {}
    for r in rows:
        if per_order[r["order_id"]]["rf"] == 1:
            continue
        c = cat.setdefault(r["category"], {"gmv": Decimal("0"), "qty": 0})
        c["gmv"] += d(r["amount"])
        c["qty"] += int(d(r["qty"]))
    metrics["by_category"] = OrderedDict(
        (k, {"gmv": r2(v["gmv"]), "qty": v["qty"]})
        for k, v in sorted(cat.items(), key=lambda kv: (-kv[1]["gmv"], kv[0])))

    # weekly_gmv：W01..W13，仅 E，缺失周补 0（13 周必须齐全）
    weekly = {"W%02d" % i: Decimal("0") for i in range(1, 14)}
    for r in rows:
        if per_order[r["order_id"]]["rf"] == 1:
            continue
        weekly[r["week"]] += d(r["amount"])
    metrics["weekly_gmv"] = OrderedDict((k, r2(weekly[k])) for k in sorted(weekly))

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(metrics, f, ensure_ascii=False, indent=2)

    print("GMV=%.2f  有效订单=%d  AOV=%.2f  退款率=%.4f  毛利率=%.4f  退款金额=%.2f"
          % (metrics["gmv"], metrics["valid_orders"], metrics["aov"],
             metrics["refund_rate"], metrics["gross_margin_rate"], metrics["refund_amount"]))
    print("已写出 %s" % os.path.relpath(OUT, ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
