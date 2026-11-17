# -*- coding: utf-8 -*-
"""一键跑通全部验收，输出可粘贴进报告的汇总。

    python scripts/verify_all.py

5 步各有自己的期望退出码；全部命中才算"可以进入人工复核"。
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable

# (标题, [参数...], 期望退出码)。第一个参数若是脚本路径则转绝对；其余原样传给脚本
STEPS = [
    ("1. Hook 单元测试（期望 21/21）", [".harness/hooks/test_hooks.py"], 0),
    ("2. 四层防线对比 demo（期望 full 列 6/6 拦截）", [".harness/harness.py", "demo"], 0),
    ("3. 幂等校验 R7（期望两次 md5 一致）", ["scripts/check_idempotent.py"], 0),
    ("4. 扰动测试（期望 GMV +100 / 退款单 GMV +0）", ["scripts/drill_perturb.py"], 0),
    ("5. 交付物自动验收（期望 113/113）", ["scripts/eval_deliverables.py"], 0),
]


def main():
    summary = []
    for title, args, want in STEPS:
        print("\n" + "=" * 72)
        print(title)
        print("=" * 72)
        argv = [PY, os.path.join(ROOT, args[0])] + list(args[1:])
        r = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
        out = (r.stdout or "").rstrip()
        print(out)
        if r.returncode not in (0, 1):
            print("STDERR:", (r.stderr or "")[-400:])
        ok = r.returncode == want
        summary.append((title, r.returncode, ok))
        if title.startswith("2."):
            full_col = [l for l in out.splitlines() if l.startswith("| A")]
            blocked = sum(1 for l in full_col if l.rstrip().endswith("| 拦截 |"))
            print("\n>>> full 策略拦截：%d / %d" % (blocked, len(full_col)))
            summary[-1] = (title, r.returncode, ok and blocked >= 6)

    print("\n" + "=" * 72)
    print("汇总")
    print("=" * 72)
    for title, code, ok in summary:
        print("%s %-52s 退出码 %d" % ("[PASS]" if ok else "[FAIL]", title, code))
    all_ok = all(ok for _, _, ok in summary)
    print("\n%s" % ("全部验收通过，可以进入人工复核。" if all_ok else "有失败项，见上。"))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
