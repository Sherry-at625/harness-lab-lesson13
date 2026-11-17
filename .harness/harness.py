# -*- coding: utf-8 -*-
"""
mini-harness —— 一个 200 行、可以逐行读完的 Agent 运行时（教学版）
=================================================================
它把课程里讲的四层防线，做成一条真实可跑的链路：

    用户指令 → [规则注入] → 模型决定调工具 → [权限闸] → [PreToolUse Hook] → 执行
                                              → [PostToolUse Hook] → 结果回给模型

它不是玩具：判定顺序、fail-closed、路径归一化、留痕，都是生产环境里的做法。
它也不是产品：没有 UI、没有重试、没有上下文压缩。看懂它，你再看任何 IDE 的
规则/钩子/权限面板，就知道每一栏在管什么。

子命令：
  python .harness/harness.py guard --tool write_file --args "{\"path\":\"data/raw/orders.csv\"}"
      只做判定，不执行。用来看某一次工具调用会被怎么处理。
  python .harness/harness.py demo [--policy off|rules|hooks|full]
      用 redteam/cases.json 的 6 个攻击用例，在**一次性沙箱副本**里跑一遍，
      看四层防线分别拦住了什么。（原始数据不会被碰）
  python .harness/harness.py audit
      汇总审计日志：谁在什么时候被谁拦了。
"""
import argparse
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
CFG = json.load(open(os.path.join(HERE, "config.json"), encoding="utf-8"))


# ---------------------------------------------------------------- 基础工具
def norm(p, root):
    """路径归一化：反斜杠、./ ../、大小写、URL 编码，一律压平后再比较。"""
    from urllib.parse import unquote
    p = unquote(str(p or "")).replace("\\", "/").strip()
    root = root.replace("\\", "/").rstrip("/")
    if p.lower().startswith(root.lower()):
        p = p[len(root):]
    return os.path.normpath("/" + p).replace("\\", "/").lstrip("/").lower()


def match_any(rel, patterns):
    for pat in patterns:
        pat = pat.lower().replace("\\", "/")
        if pat.startswith("**") and rel.endswith(pat[2:]):
            return True
        if fnmatch.fnmatch(rel, pat):
            return True
    return False


def audit(rec):
    if not CFG.get("audit", {}).get("enabled"):
        return
    path = os.path.join(ROOT, CFG["audit"]["path"])
    os.makedirs(os.path.dirname(path), exist_ok=True)
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- 权限闸（第 4 层）
def permission_check(tool, args, root):
    """返回 (decision, reason)。allow / ask / deny。
    注意：它是**白名单**，不是黑名单。没写在允许里的，默认不放。"""
    perm = CFG["permissions"]
    if tool == "http_get":
        return ("deny", "策略：禁止出网（network=deny）") if perm.get("network") == "deny" \
            else ("allow", "")
    if tool == "run_shell":
        cmd = str(args.get("command", "")).strip()
        for pre in perm.get("shell_allow_prefix", []):
            if cmd.lower().startswith(pre.lower()):
                return ("allow", "命令前缀命中白名单")
        return ("deny", "命令不在白名单内：%s" % cmd[:60])
    rel = norm(args.get("path", ""), root)
    if tool == "read_file":
        if match_any(rel, perm.get("read_allow", [])):
            return ("allow", "")
        return ("deny", "读取路径不在白名单：%s" % rel)
    if tool in ("write_file", "delete_file"):
        if match_any(rel, perm.get("write_deny", [])):
            return ("deny", "命中 write_deny：%s" % rel)
        if match_any(rel, perm.get("write_allow", [])):
            return ("allow", "")
        return ("deny", "写入路径不在白名单：%s" % rel)
    return ("ask", "未知工具，需人工确认")


# ---------------------------------------------------------------- Hook 执行（第 2 层）
def run_hook(event, tool, args, root, extra=None):
    hooks = CFG["hooks"].get(event, [])
    for h in hooks:
        if not fnmatch.fnmatch(tool, h.get("matcher", "*")):
            continue
        payload = {"event": "PreToolUse" if event == "pre_tool_use" else "PostToolUse",
                   "tool_name": tool, "tool_input": args, "cwd": root}
        if extra:
            payload.update(extra)
        try:
            r = subprocess.run(h["command"].split(), input=json.dumps(payload, ensure_ascii=False),
                               capture_output=True, text=True, encoding="utf-8", cwd=ROOT,
                               timeout=h.get("timeout_ms", 5000) / 1000)
        except subprocess.TimeoutExpired:
            return ("deny", "Hook 超时，按 fail-closed 拦截")
        out = (r.stdout or "").strip()
        if r.returncode == 2:
            return ("deny", (r.stderr or "Hook exit 2").strip())
        if r.returncode != 0 and not out:
            return ("deny", "Hook 异常退出（fail-closed）")
        if not out:
            continue
        try:
            data = json.loads(out.splitlines()[-1])
        except json.JSONDecodeError:
            return ("deny", "Hook 输出无法解析（fail-closed）")
        if "hookSpecificOutput" in data:
            d = data["hookSpecificOutput"].get("permissionDecision", "allow")
            return (d, data["hookSpecificOutput"].get("permissionDecisionReason", ""))
        if data.get("decision") == "block":
            return ("deny", data.get("reason", "post hook blocked"))
    return ("allow", "")


# ---------------------------------------------------------------- 组合成 gate
def gate(tool, args, root, policy):
    """四层防线的执行顺序：规则(不拦) → 权限 → pre hook → 执行 → post hook"""
    if policy == "off":
        return ("allow", "无任何约束", "—")
    if policy == "rules":                    # 规则层：只能靠模型自觉，这里不拦
        return ("allow", "规则已注入上下文，但模型可以选择不遵守", "AGENTS.md")
    if policy in ("hooks", "full"):
        if policy == "full":
            d, r = permission_check(tool, args, root)
            if d != "allow":
                return (d, r, "permissions")
        d, r = run_hook("pre_tool_use", tool, args, root)
        if d != "allow":
            return (d, r, "PreToolUse")
        return ("allow", "pre 通过", "PreToolUse")
    return ("allow", "未知策略", "—")


def post_gate(tool, args, root, policy):
    if policy not in ("hooks", "full"):
        return ("pass", "无 post 校验", "—")
    d, r = run_hook("post_tool_use", tool, args, root, {"tool_response": {"ok": True}})
    return ("block" if d == "deny" else "pass", r, "PostToolUse")


# ---------------------------------------------------------------- 沙箱副本 + 模拟执行
DEMO_FILES = {
    "data/raw/orders.csv": "order_id,order_time,store_id,amount\nSO1,2026-07-01,QH001,13.80\n",
    "data/raw/customers.csv": "member_id,phone\nM00123,13800001234\n",
    "data/reference/cleaning_spec.md": "# 清洗规格（只读）\n",
    "data/reference/metrics_spec.md": "# 指标口径（只读）\n",
    "AGENTS.md": "# 规则\n数据只读。\n",
    "deliverables/.keep": "",
}


def build_ws(ws):
    """重置沙箱副本：只重写/清理必要文件，不做整目录删除（避免触发批量删除保护）。"""
    for rel, content in DEMO_FILES.items():
        p = os.path.join(ws, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(content)
    for sub in ("data/raw", "deliverables"):
        d = os.path.join(ws, sub)
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            rel = os.path.relpath(fp, ws).replace("\\", "/")
            if os.path.isfile(fp) and rel not in DEMO_FILES:
                os.remove(fp)
    dst_hooks = os.path.join(ws, ".harness", "hooks")
    os.makedirs(dst_hooks, exist_ok=True)
    for f in os.listdir(os.path.join(HERE, "hooks")):
        if f.endswith(".py"):
            shutil.copy2(os.path.join(HERE, "hooks", f), os.path.join(dst_hooks, f))
    for f in ("config.json", "harness.py"):
        shutil.copy2(os.path.join(HERE, f), os.path.join(ws, ".harness", f))


def snapshot(ws):
    snap = {}
    for base, _, files in os.walk(ws):
        for f in files:
            p = os.path.join(base, f)
            snap[os.path.relpath(p, ws).replace("\\", "/")] = os.path.getsize(p)
    return snap


def simulate(tool, args, ws):
    """演示用的“执行器”：只动沙箱副本，绝不动真实项目。"""
    if tool == "write_file":
        p = os.path.join(ws, norm(args["path"], ws))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "w", encoding="utf-8").write(args.get("content", ""))
        return "文件已写入"
    if tool == "delete_file":
        p = os.path.join(ws, norm(args["path"], ws))
        if os.path.exists(p):
            os.remove(p)
        return "文件已删除"
    if tool == "run_shell":
        cmd = str(args.get("command", ""))
        low = cmd.lower()
        if "data/raw" in low or "data\\raw" in low:
            d = os.path.join(ws, "data", "raw")
            if any(k in low for k in ("rm", "del", "rd ", "rmdir", "remove-item")):
                shutil.rmtree(d, ignore_errors=True)
                os.makedirs(d, exist_ok=True)
                return "已执行删除（原始数据被清空，目录重建）"
            return "已执行（未命中删除逻辑）"
        return "已执行"
    if tool == "http_get":
        return "已下载外部数据（策略本应禁止）"
    return "已执行"


# ---------------------------------------------------------------- 子命令
def cmd_guard(a):
    args = json.loads(a.args)
    root = os.path.abspath(a.root or ROOT)
    d, r, who = gate(a.tool, args, root, a.policy)
    print(json.dumps({"tool": a.tool, "args": args, "decision": d, "reason": r,
                      "blocked_by": who}, ensure_ascii=False, indent=2))
    audit({"kind": "guard", "tool": a.tool, "decision": d, "reason": r, "by": who})
    sys.exit(0 if d == "allow" else 3)


def cmd_demo(a):
    cases = json.load(open(os.path.join(ROOT, "redteam", "cases.json"), encoding="utf-8"))
    policies = [a.policy] if a.policy else ["off", "rules", "hooks", "full"]
    ws = os.path.join(HERE, "audit", "demo_ws")
    results = {}
    for pol in policies:
        for c in cases:
            build_ws(ws)
            before = snapshot(ws)
            d, r, who = gate(c["tool"], c["args"], ws, pol)
            executed = d == "allow"
            msg = simulate(c["tool"], c["args"], ws) if executed else "未执行"
            after = snapshot(ws)
            broken = [k for k in before if k not in after]
            changed = [k for k in before if k in after and before[k] != after[k]]
            post, preason, _ = post_gate(c["tool"], c["args"], ws, pol) if executed else ("—", "", "")
            results.setdefault(c["id"], {})[pol] = {
                "decision": d, "reason": r, "by": who, "exec": msg,
                "damaged": bool(broken or changed), "broken": broken, "changed": changed,
                "post": post, "post_reason": preason,
            }
            audit({"kind": "demo", "policy": pol, "case": c["id"], "tool": c["tool"],
                   "decision": d, "by": who, "damaged": bool(broken or changed)})

    head = "| 用例 | 试图做什么 | " + " | ".join(policies) + " |"
    print(head)
    print("|" + "---|" * (len(policies) + 2))
    for c in cases:
        cells = []
        for pol in policies:
            x = results[c["id"]][pol]
            mark = "拦截" if x["decision"] != "allow" else ("破坏成功" if x["damaged"] else "放行")
            if x["post"] == "block":
                mark = "事后判无效"
            cells.append(mark)
        print("| %s | %s | %s |" % (c["id"], c["title"], " | ".join(cells)))
    print("\n明细：")
    for c in cases:
        for pol in policies:
            x = results[c["id"]][pol]
            print("  %-4s %-5s → %-5s %-9s %s" % (c["id"], pol, x["decision"], x["by"],
                                                  (x["reason"] or "")[:56]))
    # 保留沙箱副本供课后检查，不做整目录删除


def cmd_audit(a):
    path = os.path.join(ROOT, CFG["audit"]["path"])
    if not os.path.exists(path):
        print("暂无审计日志。先跑一次 guard 或 demo。")
        return
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    deny = [r for r in rows if r.get("decision") == "deny"]
    print("总计 %d 条，其中被拦 %d 条。" % (len(rows), len(deny)))
    for r in deny[-10:]:
        print("  %s  %-11s %-10s %s" % (r.get("ts"), r.get("tool", r.get("case", "")),
                                        r.get("by", ""), (r.get("reason") or "")[:60]))


def main():
    ap = argparse.ArgumentParser(description="mini-harness 教学运行时")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("guard", help="只判定一次工具调用会被怎么处理")
    g.add_argument("--tool", required=True)
    g.add_argument("--args", default="{}", help="JSON 字符串")
    g.add_argument("--root", default=None)
    g.add_argument("--policy", default="full", choices=["off", "rules", "hooks", "full"])
    d = sub.add_parser("demo", help="在一次性沙箱里跑红队用例")
    d.add_argument("--policy", default=None, choices=["off", "rules", "hooks", "full"])
    sub.add_parser("audit", help="汇总审计日志")
    a = ap.parse_args()
    {"guard": cmd_guard, "demo": cmd_demo, "audit": cmd_audit}[a.cmd](a)


if __name__ == "__main__":
    main()
