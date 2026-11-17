# -*- coding: utf-8 -*-
"""
扰动测试（《学生实践手册》附录 C / 审查清单 D3）

回答一个问题：**我的指标脚本是"算出来的"，还是"碰巧对上的"？**

做法（不碰任何只读目录）：
  1. 备份 deliverables/orders_clean.csv
  2. 追加 1 行**新订单**：金额 100.00，refund_flag=0，数量 1，单价 100.00，成本 40.00
     → 期望 GMV +100.00、有效订单数 +1、毛利额 +60.00、总行数 +1
  3. 再追加 1 行**退款订单**：金额 200.00，refund_flag=1
     → 期望 GMV **不变**、有效订单数不变、退款订单数 +1、退款率分子 +1
  4. 每次追加后重算，与期望值逐字段比对；最后还原

为什么必须做这一步：
  113/113 全绿只说明"结果没被人为写死到某个常量"，不能说明计算逻辑对。
  扰动测试是唯一能区分"真算"和"假算"的检查——写死答案的实现，扰动后必然不变。

    python scripts/drill_perturb.py
"""
import json
import os
import shutil
import subprocess
import sys
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV = os.path.join(ROOT, "deliverables", "orders_clean.csv")
MJ = os.path.join(ROOT, "deliverables", "metrics.json")
PY = sys.executable

EXTRA_NORMAL = ("ZZ900000001,2026-09-15,W12,QH001,APP,SK1001,有机小油菜 300g,蔬菜,"
                "1,100.00,0.00,100.00,40.00,60.00,MHperturb001,0")
EXTRA_REFUND = ("ZZ900000002,2026-09-16,W12,QH001,APP,SK1001,有机小油菜 300g,蔬菜,"
                "2,100.00,0.00,200.00,40.00,120.00,MHperturb002,1")


def recompute():
    r = subprocess.run([PY, os.path.join(ROOT, "scripts", "compute_metrics.py")],
                       capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    if r.returncode != 0:
        raise SystemExit("compute_metrics.py 失败：\n" + r.stderr[-800:])
    return json.load(open(MJ, encoding="utf-8"), parse_float=Decimal)


def append_line(line):
    with open(CSV, "a", encoding="utf-8", newline="") as f:
        f.write(line + "\n")


def compare(tag, base, now, expected):
    ok = True
    print("\n【%s】" % tag)
    for field, delta in expected.items():
        got = Decimal(str(now[field])) - Decimal(str(base[field]))
        same = abs(got - Decimal(str(delta))) <= Decimal("0.01")
        ok = ok and same
        print("  %s %-18s 变化 %+.4f  期望 %+.4f" % ("[PASS]" if same else "[FAIL]",
                                                     field, got, delta))
    return ok


def main():
    backup = CSV + ".bak"
    shutil.copy2(CSV, backup)
    all_ok = True
    try:
        base = recompute()
        print("基线：gmv=%s valid_orders=%s refund_orders=%s gross_profit=%s row_count=%s"
              % (base["gmv"], base["valid_orders"], base["refund_orders"],
                 base["gross_profit"], base["row_count"]))

        append_line(EXTRA_NORMAL)
        now = recompute()
        all_ok &= compare("追加 1 笔净销售订单（金额 100、成本 40）", base, now,
                          {"gmv": 100, "valid_orders": 1, "gross_profit": 60, "row_count": 1})

        append_line(EXTRA_REFUND)
        now2 = recompute()
        all_ok &= compare("再追加 1 笔退款订单（金额 200）—— GMV 必须**不变**", now, now2,
                          {"gmv": 0, "valid_orders": 0, "refund_orders": 1, "row_count": 1})
    finally:
        shutil.move(backup, CSV)
        recompute()
        print("\n【还原】订单表与指标已恢复基线")

    print("\n=== %s ===" % ("扰动测试通过：指标是算出来的，退款订单没有被计入 GMV"
                            if all_ok else "扰动测试失败：指标响应与口径不符"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
