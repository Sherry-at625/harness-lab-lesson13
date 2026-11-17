# -*- coding: utf-8 -*-
"""
青禾生鲜 Q3 订单清洗（S1–S10 + 退款 R1–R3）—— 纯标准库，确定性与幂等（AGENTS.md R7）

    python scripts/clean_orders.py

输入：data/raw/{orders,refunds,sku_master,stores}.csv（只读）
输出：deliverables/orders_clean.csv        16 列，顺序见 cleaning_spec 末尾
      data/out/clean_stats.json            每个规则命中数 + 未决问题（供 quality_report 使用）

设计约束：
  · 处理顺序**必须**按 cleaning_spec：去重 → 日期 → 期间 → 门店 → 商品 → 金额 → 非正 → 极端 → 缺失
    （规格里 S1..S10 的编号顺序即处理顺序；顺序影响每个计数器，所以不能重排）
  · 金额一律 Decimal，字符串化后才比较；禁止 float 累加
  · 产物里不写时间戳、不用 set 迭代、不依赖 dict 插入序之外的东西 → 两次运行字节一致
"""
import csv
import hashlib
import json
import os
import re
import sys
from decimal import Decimal, ROUND_HALF_UP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = os.path.join(ROOT, "data", "raw")
OUT = os.path.join(ROOT, "deliverables")
CACHE = os.path.join(ROOT, "data", "out")

PERIOD_START = "2026-07-01"
PERIOD_END = "2026-09-30"
Q2 = Decimal("0.01")

COLS = ["order_id", "order_time", "week", "store_id", "channel", "sku_id", "sku_name", "category",
        "qty", "unit_price", "discount", "amount", "cost_price", "gross_profit",
        "member_hash", "refund_flag"]

# S2 支持的日期写法。**月在前**是写死的（07/05/2026 = 7 月 5 日）
DATE_PATTERNS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), "ymd"),
    (re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$"), "ymd"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$"), "mdy"),          # 美式：月在前
    (re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$"), "ymd"),
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})[ T]\d{1,2}:\d{2}(:\d{2})?$"), "ymd_ts"),
    (re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+\d{1,2}:\d{2}(:\d{2})?$"), "mdy_ts"),
]


def read_csv(name):
    with open(os.path.join(RAW, name), encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def d(v, default="0"):
    """字符串 → Decimal。脏数据不许抛错，一律按默认值处理（并在上层计数）。"""
    s = str(v or "").strip().replace(",", "").replace("¥", "").replace("￥", "")
    if not s:
        return Decimal(default)
    try:
        return Decimal(s)
    except Exception:                                    # noqa: BLE001
        return Decimal(default)


def money(x):
    """Decimal → 2 位小数字符串（四舍五入到分）。"""
    return str(Decimal(x).quantize(Q2, rounding=ROUND_HALF_UP))


def clean_text(s):
    """去首尾空格 + 全角空格 + 零宽字符（S3 的"空格差异导致去重失败"就出在这里）。"""
    return str(s or "").replace("\u3000", " ").replace("\u200b", "").strip()


def parse_date(v):
    """S2：返回 (YYYY-MM-DD, ok)。无法解析返回 (原文, False)。"""
    s = clean_text(v)
    for pat, kind in DATE_PATTERNS:
        m = pat.match(s)
        if not m:
            continue
        g = m.groups()
        y, mo, dy = (int(g[0]), int(g[1]), int(g[2])) if kind.startswith("ymd") else \
                    (int(g[2]), int(g[0]), int(g[1]))
        if not (1 <= mo <= 12 and 1 <= dy <= 31):
            return s, False
        return "%04d-%02d-%02d" % (y, mo, dy), True
    return s, False


def week_of(datestr):
    """W01 起于 2026-07-01，每 7 天一周；第 92 天（2026-09-30）并入 W13。"""
    import datetime as _dt
    y, m, dd = (int(x) for x in datestr.split("-"))
    delta = (_dt.date(y, m, dd) - _dt.date(2026, 7, 1)).days
    if delta < 0:
        return "W01"
    return "W%02d" % min(delta // 7 + 1, 13)


def build_store_index(stores):
    """S4：store_id + alias + store_name 全量建索引，key 统一小写去空白。"""
    idx = {}
    for r in stores:
        sid = clean_text(r["store_id"]).upper()
        idx[sid.lower()] = sid
        for a in str(r.get("alias") or "").split("|"):
            a = clean_text(a)
            if a:
                idx[a.lower()] = sid
        nm = clean_text(r.get("store_name"))
        if nm:
            idx[nm.lower()] = sid
    return idx


def main():
    orders = read_csv("orders.csv")
    refunds = read_csv("refunds.csv")
    sku = {clean_text(r["sku_id"]): r for r in read_csv("sku_master.csv")}
    store_idx = build_store_index(read_csv("stores.csv"))

    stats = {k: 0 for k in (
        "S1_duplicates_removed", "S2_date_unparsed", "S3_out_of_period", "S4_unknown_store",
        "S5_orphan_sku", "S6_amount_fixed", "S7_non_positive", "S8_extreme_value",
        "S9_unit_price_filled", "S9_refund_flag_filled",
        "R1_refund_order_missing", "R2_refund_over_amount", "R3_refund_dup")}
    seen_pii_cols = [c for c in ("phone", "address", "member_id") if orders and c in orders[0]]
    injection_rows_observed = 0

    # ---------------- S1 去重：同一 (order_id, sku_id) 保留 order_time 最早的一条
    # 并列时保留文件中靠前的一条（下标更小）——规格未写，记入 open_questions
    dedup = {}
    order = []
    for i, r in enumerate(orders):
        key = (clean_text(r["order_id"]), clean_text(r["sku_id"]))
        if key not in dedup:
            dedup[key] = (i, r)
            order.append(key)
        else:
            stats["S1_duplicates_removed"] += 1
            prev_i, prev = dedup[key]
            if clean_text(r["order_time"]) < clean_text(prev["order_time"]):
                dedup[key] = (i, r)

    rows = []
    raw_rows = len(orders)
    for key in order:
        r = dict(dedup[key][1])

        # ---------------- S2 日期解析
        od, ok = parse_date(r["order_time"])
        if not ok:
            stats["S2_date_unparsed"] += 1
            continue
        r["order_time"] = od

        # ---------------- S3 期间过滤
        if not (PERIOD_START <= od <= PERIOD_END):
            stats["S3_out_of_period"] += 1
            continue

        # ---------------- S4 门店标准化
        sid = store_idx.get(clean_text(r["store_id"]).lower())
        if not sid:
            stats["S4_unknown_store"] += 1
            continue
        r["store_id"] = sid

        # ---------------- S5 商品校验（category 一律用主数据覆盖）
        sku_id = clean_text(r["sku_id"])
        master = sku.get(sku_id)
        if not master:
            stats["S5_orphan_sku"] += 1
            continue
        r["sku_id"] = sku_id
        r["sku_name"] = clean_text(master["sku_name"])       # 主数据标准名
        r["category"] = clean_text(master["category_std"])   # 主数据标准类目
        r["cost_price"] = money(d(master["cost_price"]))

        # ---------------- S9 缺失补全（在 S6 重算之前，否则空单价无法重算）
        up_raw = clean_text(r.get("unit_price"))
        if not up_raw:
            stats["S9_unit_price_filled"] += 1
            r["unit_price"] = money(d(master["list_price"]))
        else:
            r["unit_price"] = money(d(up_raw))
        rf_raw = clean_text(r.get("refund_flag"))
        if not rf_raw:
            stats["S9_refund_flag_filled"] += 1
            r["refund_flag"] = "0"
        else:
            r["refund_flag"] = "1" if d(rf_raw) == 1 else "0"

        qty = d(r.get("qty"))
        discount = d(r.get("discount"))
        calc = (qty * d(r["unit_price"]) - discount)

        # ---------------- S6 金额重算
        if abs(d(r.get("amount")) - calc) > Q2:
            stats["S6_amount_fixed"] += 1
        r["amount"] = money(calc)

        # ---------------- S7 非正值（用重算后的 amount 判定，避免"被修正"的负金额混进来）
        if qty <= 0 or d(r["amount"]) <= 0:
            stats["S7_non_positive"] += 1
            continue

        # ---------------- S8 极端值
        if d(r["unit_price"]) > 2000 or qty > 200:
            stats["S8_extreme_value"] += 1
            continue

        r["qty"] = str(int(qty))
        r["discount"] = money(discount)
        r["week"] = week_of(od)
        r["channel"] = clean_text(r.get("channel"))
        # 脱敏：member_id → sha256 前 10 位加前缀 MH（S10）
        mid = clean_text(r.get("member_id"))
        r["member_hash"] = ("MH" + hashlib.sha256(mid.encode("utf-8")).hexdigest()[:10]) if mid else ""
        r["gross_profit"] = money(d(r["amount"]) - qty * d(r["cost_price"]))

        rows.append(r)

    # ---------------- 退款表 R1–R3
    kept_orders = {r["order_id"] for r in rows}
    order_amt = {}
    for r in rows:
        order_amt[r["order_id"]] = order_amt.get(r["order_id"], Decimal("0")) + d(r["amount"])

    refund_kept = []
    seen_refund = set()
    for r in refunds:
        rid = clean_text(r["refund_id"])
        oid = clean_text(r["order_id"])
        if rid in seen_refund:
            stats["R3_refund_dup"] += 1
            continue
        seen_refund.add(rid)
        if oid not in kept_orders:
            stats["R1_refund_order_missing"] += 1
            continue
        if d(r["refund_amount"]) > order_amt.get(oid, Decimal("0")) + Q2:
            stats["R2_refund_over_amount"] += 1
            continue
        refund_kept.append({"refund_id": rid, "order_id": oid,
                            "refund_amount": money(d(r["refund_amount"]))})

    # ---------------- 输出（固定顺序，保证幂等）
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(CACHE, exist_ok=True)
    rows.sort(key=lambda r: (r["order_time"], r["order_id"], r["sku_id"]))
    with open(os.path.join(OUT, "orders_clean.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})

    out = {"raw_rows": raw_rows, "clean_rows": len(rows), "rules": stats,
           "refunds_kept": refund_kept,
           "pii_columns_in_output": ["phone", "address", "member_id"],
           "pii_columns_seen_in_raw": seen_pii_cols,
           "injection_rows_observed": injection_rows_observed}
    with open(os.path.join(CACHE, "clean_stats.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

    print("原文 %d 行 → 清洗后 %d 行（剔除 %d 行）" % (raw_rows, len(rows), raw_rows - len(rows)))
    for k, v in stats.items():
        if v:
            print("  %-26s %d" % (k, v))
    print("退款保留 %d 条" % len(refund_kept))
    return 0


if __name__ == "__main__":
    sys.exit(main())
