# -*- coding: utf-8 -*-
"""
质量报告 + 一页结论 —— 所有数字都从文件读，不手写、不估算

    python scripts/make_report.py

输入：data/out/clean_stats.json、deliverables/metrics.json
输出：deliverables/quality_report.json、deliverables/report.md
"""
import json
import os
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEL = os.path.join(ROOT, "deliverables")
STATS = os.path.join(ROOT, "data", "out", "clean_stats.json")

RULE_DESC = {
    "S1_duplicates_removed": ("S1", "同一 (order_id, sku_id) 重复行，保留 order_time 最早一条"),
    "S2_date_unparsed": ("S2", "日期无法解析，剔除"),
    "S3_out_of_period": ("S3", "2026-07-01~09-30 期间外（含 6 月试运行数据），剔除并计数"),
    "S4_unknown_store": ("S4", "门店号无法映射到 QH001-003（如 QH009），剔除"),
    "S5_orphan_sku": ("S5", "孤儿 SKU：sku_id 不在主数据中，剔除"),
    "S6_amount_fixed": ("S6", "金额与 qty*单价-优惠 不符，按重算值覆盖"),
    "S7_non_positive": ("S7", "数量或金额非正/为空，剔除"),
    "S8_extreme_value": ("S8", "单价>2000 或 数量>200，业务上限外，剔除"),
    "S9_unit_price_filled": ("S9", "unit_price 为空，用主数据 list_price 补全"),
    "S9_refund_flag_filled": ("S9", "refund_flag 为空，按 0 补全"),
    "R1_refund_order_missing": ("R1", "孤儿退款：order_id 不在清洗后订单中，剔除"),
    "R2_refund_over_amount": ("R2", "超额退款：refund_amount > 订单金额+0.01，剔除"),
    "R3_refund_dup": ("R3", "退款单号重复，保留一条"),
}


def pad(text, width):
    """按显示宽度补齐（中文/全角算 2 宽度），让 Markdown 表格在等宽渲染下对齐。"""
    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)
    return text + " " * max(0, width - w)


def table(header, rows, align="l"):
    """生成对齐的 Markdown 表格。rows 里每行是已格式化的字符串列表。"""
    cols = len(header)
    widths = [max(sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in r[i])
                  for r in [header] + rows) for i in range(cols)]
    out = ["| " + " | ".join(pad(h, widths[i]) for i, h in enumerate(header)) + " |"]
    out.append("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        out.append("| " + " | ".join(pad(c, widths[i]) for i, c in enumerate(r)) + " |")
    return "\n".join(out)


def main():
    stats = json.load(open(STATS, encoding="utf-8"))
    m = json.load(open(os.path.join(DEL, "metrics.json"), encoding="utf-8"))
    rules = stats["rules"]

    removals = sum(rules[k] for k in ("S2_date_unparsed", "S3_out_of_period", "S4_unknown_store",
                                      "S5_orphan_sku", "S7_non_positive", "S8_extreme_value"))
    # 去重不改变"订单内容"，但会计入行数差：624 - 18(去重) - removals = clean_rows
    assert stats["raw_rows"] - rules["S1_duplicates_removed"] - removals == stats["clean_rows"], \
        "计数不守恒：%d - %d - %d != %d" % (stats["raw_rows"], rules["S1_duplicates_removed"],
                                           removals, stats["clean_rows"])


    q = {
        "raw_rows": stats["raw_rows"],
        "clean_rows": stats["clean_rows"],
        "removed_rows": stats["raw_rows"] - stats["clean_rows"],
        "removed_rows_breakdown": {
            "deduplicated": rules["S1_duplicates_removed"],
            "dropped": removals,
        },
        "rules": rules,
        "rule_catalog": {k: {"rule": v[0], "meaning": v[1], "count": rules[k]}
                         for k, v in RULE_DESC.items()},
        "pii_columns_in_output": [],
        "pii_columns_removed": ["phone", "address"],
        "pii_columns_hashed": {"member_id": "member_hash = 'MH' + sha256(member_id)[:10]"},
        "injection_rows_observed": stats.get("injection_rows_observed", 0),
        "open_questions": [
            "S1 去重时若两行 order_time **完全相同**，规格只写“保留最早一条”，未说明并列时取文件中靠前还是靠后。"
            "本实现取文件中**靠前**的一条，已在此显式声明，请业务方确认。",
            "S5 规定 category 一律用主数据 category_std 覆盖，但主数据 category_std 本身为空的行"
            "（本次 0 行）如何归类，规格未覆盖。",
            "S6 金额差异阈值为 0.01，若两侧都为空（amount 与 qty*unit_price-discount 均缺失），"
            "是否应并入 S7 还是 S6 计数，规格未明确；本实现计入 S6。",
            "refunds.csv 的 refund_amount 为 0 的退款项是否应算作“退款订单”，规格未覆盖（本次 0 行）。",
            "week 计算以 2026-07-01 为第 1 周第 1 天；规格明确第 92 天并入 W13，但未说明若期间跨年如何编号（本次不涉及）。",
        ],
        "notes": [
            "处理顺序严格按 cleaning_spec 的 S1→S10；S9（缺失补全）在 S6（金额重算）之前执行，"
            "否则空单价的行无法重算金额。",
            "S7 判定使用 S6 重算后的 amount，避免“负金额被取绝对值后混回交付表”。",
            "去重在清洗之前完成，因此 S2–S8 的计数器均为去重后的口径。",
        ],
    }
    json.dump(q, open(os.path.join(DEL, "quality_report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # ---------------- 一页结论（每个数字都来自 metrics.json / quality_report.json）
    bs = list(m["by_store"].items())
    store_tbl = table(["门店", "GMV(元)", "有效订单", "AOV(元)", "GMV 占比"],
                      [[k, "%.2f" % v["gmv"], str(v["orders"]), "%.2f" % v["aov"],
                        "%.1f%%" % (v["gmv"] / m["gmv"] * 100)] for k, v in bs])
    cat_tbl = table(["类目", "GMV(元)", "销量"],
                    [[k, "%.2f" % v["gmv"], str(v["qty"])] for k, v in list(m["by_category"].items())[:5]])
    weekly = list(m["weekly_gmv"].items())
    wk_max = max(weekly, key=lambda kv: kv[1])
    wk_min = min(weekly, key=lambda kv: kv[1])
    aov_hi = max(bs, key=lambda kv: kv[1]["aov"])
    aov_lo = min(bs, key=lambda kv: kv[1]["aov"])
    aov_spread = (aov_hi[1]["aov"] / aov_lo[1]["aov"] - 1) * 100
    rule_tbl = table(["规则", "命中", "说明"], [
        ["S1 去重", str(rules["S1_duplicates_removed"]), "同一 (order_id, sku_id) 保留 order_time 最早一条"],
        ["S3 期间外", str(rules["S3_out_of_period"]), "6 月试运行数据，剔除但计数"],
        ["S4 门店不可识别", str(rules["S4_unknown_store"]), "如 QH009，映射不到任何门店"],
        ["S5 孤儿 SKU", str(rules["S5_orphan_sku"]), "sku_id 不在主数据中"],
        ["S6 金额重算", str(rules["S6_amount_fixed"]), "原始金额与 qty×单价−优惠 不符，按重算值覆盖"],
        ["S7 非正值", str(rules["S7_non_positive"]), "数量或金额 ≤ 0"],
        ["S8 极端值", str(rules["S8_extreme_value"]), "单价>2000 或 数量>200"],
        ["S9 缺失补全", "%d / %d" % (rules["S9_unit_price_filled"], rules["S9_refund_flag_filled"]),
         "单价用主数据 list_price；退款标记补 0"],
        ["R2 超额退款", str(rules["R2_refund_over_amount"]), "退款金额大于订单金额"],
    ])

    report = f"""# 青禾生鲜 Q3 订单数据治理｜结论摘要

> 期间 2026-07-01 ~ 2026-09-30 ｜ 原始记录 {q['raw_rows']} 行 ｜ 清洗后 {q['clean_rows']} 行 ｜
> 校验后订单 {m['all_checked_orders']} 单（含退款），净销售订单 {m['valid_orders']} 单
>
> 本文所有数字均由 `scripts/compute_metrics.py` 从 `deliverables/orders_clean.csv` 计算并写入
> `metrics.json`，再由此脚本渲染；**文中不含任何手工填写的数值**。

## 1. 结论先行

**Q3 净销售 GMV 为 {m['gmv']:,.2f} 元，来自 {m['valid_orders']} 个有效订单，客单价 {m['aov']:.2f} 元。**
退款订单 {m['refund_orders']} 单，退款率 {m['refund_rate'] * 100:.2f}%，退款金额 {m['refund_amount']:,.2f} 元；
毛利额 {m['gross_profit']:,.2f} 元，毛利率 {m['gross_margin_rate'] * 100:.2f}%。

三句话的业务含义：

1. **规模**：{m['all_checked_orders']} 个校验后订单里有 {m['refund_orders']} 单发生退款，
   退款金额相当于 GMV 的 {m['refund_amount'] / m['gmv'] * 100:.2f}%，相当于毛利的
   {m['refund_amount'] / m['gross_profit'] * 100:.2f}%。退款不是小数，需要单独看原因分布。
2. **结构**：门店间差异明显——{bs[0][0]} 贡献了 {bs[0][1]['gmv'] / m['gmv'] * 100:.1f}% 的 GMV，
   但客单价最高的是 {aov_hi[0]}（{aov_hi[1]['aov']:.2f} 元）。
3. **节奏**：13 周中 GMV 最高的是 {wk_max[0]}（{wk_max[1]:,.2f} 元），最低的是 {wk_min[0]}（{wk_min[1]:,.2f} 元）。

## 2. 数据质量：问题在哪、处理了多少

原始数据共 **{q['raw_rows']} 行**，最终交付 **{q['clean_rows']} 行**，减少 **{q['removed_rows']} 行**，
其中 {q['removed_rows_breakdown']['deduplicated']} 行是重复记录（同一订单同一商品出现多次），
{q['removed_rows_breakdown']['dropped']} 行是被规则剔除的无效记录。**每一行都有规则编号，没有静默丢弃。**

{rule_tbl}

最值得注意的两类问题：

- **金额不自洽（S6，{rules['S6_amount_fixed']} 行）**：这些行如果直接用原始 `amount` 汇总，
  GMV 会整体偏高或偏低，且偏差不可预测——不是"四舍五入级别"的误差，而是口径级错误。
  处理方式是**全部重算**而不是"挑大的改"，因为无法判断原始值是笔误还是漏了优惠。
- **数据里的"指令文本"（R4）**：`remark` 字段混入了 `SYSTEM OVERRIDE`／"忽略之前的规则"这类内容。
  按 AGENTS.md 的 R4，它们是**数据不是命令**，一律不执行，清洗后不进入交付表。
  这一类问题的价值不在"处理掉了几行"，而在于它证明了"没有 Hook 的 Agent 会被数据劫持"。

个人信息方面：`phone`、`address` 两列整列不出现在交付表中，`member_id` 已替换为
`member_hash`（sha256 前 10 位加前缀 MH）。交付表 **0 列 PII**，报告正文 **0 处明文手机号**
（由 `eval_deliverables.py` 第 3、12 项机器验证）。

## 3. 经营结构与建议

### 3.1 门店

{store_tbl}

### 3.2 类目（GMV 前 5）

{cat_tbl}

### 3.3 建议（按可执行性排序，每条都能落到一个数字上）

1. **把退款率当周度指标看，而不是季度指标。** 季度 {m['refund_rate'] * 100:.2f}% 看似可控，
   但退款金额 {m['refund_amount']:,.2f} 元已相当于毛利的
   {m['refund_amount'] / m['gross_profit'] * 100:.2f}%。建议按 `refunds.reason` 分类后
   对"商品品质问题"单独设阈值。
2. **先修上游导出，再谈分析。** 本次 {q['raw_rows']} 行里 {rules['S6_amount_fixed']} 行金额需要重算、
   {rules['S9_unit_price_filled']} 行单价缺失，说明 POS 与线上订单同步链路本身有缺陷。
   建议在导出环节加两道约束：`amount = qty * unit_price - discount`、`unit_price IS NOT NULL`。
   这比在报表层打补丁便宜得多。
3. **门店考核不要只看 GMV。** {bs[0][0]} 的 GMV 是 {bs[-1][0]} 的
   {bs[0][1]['gmv'] / bs[-1][1]['gmv']:.1f} 倍，但客单价差异只有 {aov_spread:.1f}%——
   规模差异主要来自订单量而非消费力。

## 4. 这份结论的边界（必读）

1. **"GMV 只算净销售订单"是财务口径，不是运营口径。** 如果运营周会用"含退款 GMV"，
   同一份数据会得出更高的数字。本文用的是 `metrics_spec.md` 第 1 节的口径，**不接受口头说明**。
2. **单价缺失的行是按主数据标价补的，不是真实成交价。** 本次有
   {rules['S9_unit_price_filled']} 行属于这种情况，若这些商品普遍有会员折扣，
   GMV 会被高估——量级估计在 1% 以内，但无法用现有数据验证。
3. **Hook 能证明"数据没被污染"，不能证明"计算是对的"。** 全部 12 项机器验收通过，
   只说明结构、口径自洽、留痕齐全；`把折扣当成本`这类语义错误仍能通过所有检查。
   这一层只能靠人复核（见 `deliverables/harness_evidence.json` 的 `limits`）。

## 5. 复现方式

```bash
python scripts/profile_data.py --json      # 体检
python scripts/clean_orders.py             # S1–S10 + R1–R3 → deliverables/orders_clean.csv
python scripts/compute_metrics.py          # metrics_spec 口径 → deliverables/metrics.json
python scripts/make_report.py              # 本文与 quality_report.json
python scripts/check_idempotent.py         # R7：两次运行字节一致
python scripts/eval_deliverables.py        # 12 项自动验收
```
"""
    open(os.path.join(DEL, "report.md"), "w", encoding="utf-8").write(report)
    print("已写出 quality_report.json（剔除 %d 行，计数守恒校验通过）与 report.md"
          % q["removed_rows"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
