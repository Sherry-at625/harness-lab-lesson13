# -*- coding: utf-8 -*-
"""
扰动实验（对应《学生实践手册》6.4「故意改坏」与附录 C「扰动测试」）

目的不是"证明测试会红"，而是**量出测试的灵敏度**：改动多大才会红，改动多小不会红。
测不出来就写"测不出来"——那才是这条验收的真实边界。

    python scripts/drill_break_it.py

实验分三组，每组：备份 → 改坏一处 → 跑验收 → 记录得分与红项 → 还原。
"""
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CM = os.path.join(ROOT, "scripts", "compute_metrics.py")
PY = sys.executable

CASES = [
    ("D1", "GMV 汇总保留 0 位小数（round(gmv, 0)）",
     'metrics["gmv"] = r2(gmv)', 'metrics["gmv"] = round(float(gmv), 0)'),
    ("D2", "退款率分母用 E（净销售订单）而不是 O（全量订单）—— 口径错误第 2 种",
     'metrics["refund_rate"] = r4(Decimal(len(R)) / Decimal(len(O))) if O else 0.0',
     'metrics["refund_rate"] = r4(Decimal(len(R)) / Decimal(len(E))) if E else 0.0'),
    ("D3", "有效订单数改用行数（把订单数当行数）—— 口径错误第 3 种",
     'metrics["valid_orders"] = len(E)',
     'metrics["valid_orders"] = len(rows)'),
]


def run(script, tail=3):
    r = subprocess.run([PY, os.path.join(ROOT, script)], capture_output=True, text=True,
                       encoding="utf-8", cwd=ROOT)
    lines = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
    return r.returncode, lines


def eval_once():
    code, lines = run("scripts/eval_deliverables.py", 20)
    red = [l for l in lines if l.startswith("[FAIL]")]
    score = [l for l in lines if l.startswith("总分")]
    return code, (score[0] if score else "?"), red


def main():
    results = []
    for cid, desc, old, new in CASES:
        src = open(CM, encoding="utf-8").read()
        if old not in src:
            print("[SKIP] %s 未找到目标行（脚本已变更）" % cid)
            continue
        shutil.copy2(CM, CM + ".bak")
        try:
            open(CM, "w", encoding="utf-8").write(src.replace(old, new))
            run("scripts/compute_metrics.py")
            code, score, red = eval_once()
            results.append((cid, desc, code, score, red))
            print("\n【%s】%s" % (cid, desc))
            print("  验收退出码 = %d   %s   (%s)" % (code, score, "变红" if code else "**没变红**"))
            for l in red:
                print("   ", l.strip())
        finally:
            shutil.move(CM + ".bak", CM)
    # 还原后重跑，确认基线依旧全绿
    run("scripts/compute_metrics.py")
    code, score, _ = eval_once()
    print("\n【还原】验收退出码 = %d   %s" % (code, score))

    print("\n==== 扰动灵敏度结论 ====")
    for cid, desc, code, score, red in results:
        print("  %s  %s  → %s" % (cid, "会红" if code else "不会红（落在容差/检查口径内）", score))
    return 0


if __name__ == "__main__":
    sys.exit(main())
