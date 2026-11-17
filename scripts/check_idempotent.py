# -*- coding: utf-8 -*-
"""
R7 校验：清洗与指标脚本必须幂等、确定性。

    python scripts/check_idempotent.py

做法：跑一遍流水线 → 记录产物 md5 → 再跑一遍 → 比对 md5。
两次 md5 完全相同才算通过（AGENTS.md R7 的判定条件）。

为什么这条值得单独写一个脚本：
  "两次运行结果一致"是最容易被忽略、又最容易破的一条约束。它会坏在
  随机序（set 迭代）、时间戳字段、dict 顺序、float 累加顺序这四个地方，
  而且**平时跑不出来**——只有认真比对字节才发现。
"""
import hashlib
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TARGETS = ["deliverables/orders_clean.csv", "deliverables/metrics.json"]
PIPELINE = [["scripts/clean_orders.py"], ["scripts/compute_metrics.py"]]


def md5(p):
    if not os.path.exists(p):
        return "(缺失)"
    h = hashlib.md5()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def run_pipeline():
    for step in PIPELINE:
        r = subprocess.run([sys.executable] + [os.path.join(ROOT, s) for s in step],
                           capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        if r.returncode != 0:
            print("[FAIL] %s 退出码 %d\n%s" % (step[0], r.returncode, r.stderr[-500:]))
            sys.exit(1)


def main():
    print("第 1 次运行流水线…")
    run_pipeline()
    first = {t: md5(os.path.join(ROOT, t)) for t in TARGETS}

    print("第 2 次运行流水线…")
    run_pipeline()
    second = {t: md5(os.path.join(ROOT, t)) for t in TARGETS}

    ok = True
    for t in TARGETS:
        same = first[t] == second[t]
        ok = ok and same
        print("%s %-38s 第1次 %s  第2次 %s" % ("[PASS]" if same else "[FAIL]", t, first[t], second[t]))

    print("\n=== %s ===" % ("IDEMPOTENT：两次运行字节完全一致" if ok else "不一致：存在非确定性来源"))
    if not ok:
        print("排查顺序：① 产物里是否写了时间戳 ② 是否遍历了 set ③ 是否依赖 dict 插入序 ④ float 累加顺序")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
