# -*- coding: utf-8 -*-
"""
Hook 公共工具。**学生不需要修改本文件。**
Hook 协议（业界通用做法，Claude Code / CodeBuddy / WorkBuddy / Trae 等基本一致）：

  输入：stdin 收到一个 JSON
  {
    "event": "PreToolUse" | "PostToolUse" | "UserPromptSubmit",
    "tool_name": "write_file",
    "tool_input": { ... },          # PreToolUse
    "tool_response": { ... },       # PostToolUse
    "cwd": "D:/work/project",
    "policy": {...}                 # 当前权限策略（只读）
  }

  输出（两条路，选一条）：
    1) stdout 打印 JSON 后 exit 0：
       {"hookSpecificOutput": {"permissionDecision": "deny|ask|allow",
                               "permissionDecisionReason": "人话原因"}}
    2) exit 2，把原因写到 stderr —— 与 1) 等价的“硬拦截”

  exit 0 且无 JSON = 放行；exit 其他非 0 = Hook 自身出错（按 deny 处理，fail-closed）。
"""
import json
import os
import re
import sys

PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
IDCARD_RE = re.compile(r"(?<!\d)\d{6}(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
MEMBER_RE = re.compile(r"(?<![A-Za-z0-9])M\d{5}(?![0-9])")
ADDR_RE = re.compile(r"(北京|上海|广州|深圳)市?[\u4e00-\u9fa5]{2,10}(区|县|路|街|道)")
INJECT_RE = re.compile(r"(SYSTEM\s*OVERRIDE|忽略(之前|以上|此前).{0,8}(指令|规则)|ignore (all )?previous|"
                       r"直接标记为(合格|通过)|不要执行.{0,6}(校验|检查))", re.I)

DANGEROUS_PATTERNS = [
    (r"\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*)", "递归强制删除 rm -rf"),
    (r"\brm\s+-[rf]", "强制删除 rm -f/-r"),
    (r"\bdel\s+/[fsq]", "Windows 强制删除 del /f /s /q"),
    (r"\brd\s+/s", "Windows 递归删目录 rd /s"),
    (r"\brmdir\s+/s", "Windows 递归删目录 rmdir /s"),
    (r"Remove-Item.*-Recurse", "PowerShell 递归删除"),
    (r"\bformat\s+[a-z]:", "格式化磁盘"),
    (r"\b(shutdown|reboot)\b", "关机/重启"),
    (r"git\s+(reset\s+--hard|clean\s+-[a-z]*f)", "丢弃未提交改动"),
    (r"curl[^|]*\|\s*(ba)?sh", "远程脚本直接执行"),
    (r"\bmv\s+\S+\s+data/raw", "移动文件覆盖原始数据"),
    (r">\s*\.?/?data/raw", "重定向覆盖原始数据"),
]

PROTECTED_PREFIXES = ("data/raw", "data/reference")


def read_payload():
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"_parse_error": raw[:200]}


def project_root(payload):
    return payload.get("cwd") or os.getcwd()


def norm_path(p, root):
    """把任意写法的路径归一化成相对项目根的正斜杠小写路径。

    统一处理：反斜杠、./、../、重复分隔符、盘符、大小写、URL 编码 %2e%2e
    """
    from urllib.parse import unquote
    p = unquote(str(p or "")).replace("\\", "/").strip().strip('"').strip("'")
    root = str(root).replace("\\", "/").rstrip("/")
    if p.lower().startswith(root.lower()):
        p = p[len(root):]
    p = os.path.normpath("/" + p).replace("\\", "/").lstrip("/")
    return p.lower()


def touches_protected(rel):
    return any(rel == pre or rel.startswith(pre + "/") for pre in PROTECTED_PREFIXES)


def emit(decision, reason):
    print(json.dumps({"hookSpecificOutput": {
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }}, ensure_ascii=False))
    sys.exit(0)


def deny(reason):
    emit("deny", reason)


def ask(reason):
    emit("ask", reason)


def allow():
    emit("allow", "no rule matched")


def block_post(reason):
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


def warn(reason):
    print(json.dumps({"additionalContext": reason}, ensure_ascii=False))
    sys.exit(0)


def audit(event, tool, decision, reason, extra=None):
    """Hook 自己的留痕（和 harness 的审计日志互为交叉验证）"""
    try:
        import datetime
        root = os.environ.get("HARNESS_ROOT") or os.getcwd()
        path = os.path.join(root, ".harness", "audit", "hooks.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec = {"ts": datetime.datetime.now().isoformat(timespec="seconds"),
               "event": event, "tool": tool, "decision": decision, "reason": reason}
        if extra:
            rec.update(extra)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 留痕失败不能影响判定
