# -*- coding: utf-8 -*-
"""
PreToolUse Hook —— 工具执行之前介入，能做“拦截”。
这就是 harness 里唯一真正“确定性”的位置：它是程序，不猜、不商量、不看模型心情。

判定契约：调用 deny(原因) / ask(原因) 后函数立即结束进程；返回 None = 本条守则不拦。

守卫清单（与本项目 AGENTS.md 的规则编号对应）：
  G1  原始数据/口径文档只读      ← R1
  G2  高危 shell 命令            ← R1 / R6 的兜底（规则层拦不住"借脚本删数据"）
  G3  交付物个人信息外泄          ← R2（含地址判定口径正则）
  G4  写入边界（最小权限白名单）  ← R1 的结构化兜底：只许写三个目录
  G5  断网                      ← R5
  G6  路径混淆（.. / %2e / ~）   ← R1 的绕过检测，针对"归一化已生效但来源可疑"
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _common import (DANGEROUS_PATTERNS, IDCARD_RE, MEMBER_RE, PHONE_RE, ADDR_RE, ask,
                     audit, deny, norm_path, project_root, read_payload, touches_protected)

WRITE_TOOLS = ("write_file", "delete_file", "move_file")

# G4：最小权限白名单——只写这三处。没列进来的一律不放（而不是"列出来的才禁"）
WRITE_ALLOW_PREFIXES = ("deliverables/", "data/out/", ".harness/audit/")


def g1_protect_raw(tool, tin, rel):
    """【范例，已实现】原始数据与口径文档只读。

    为什么要归一化路径：攻击者（或模型）会写 ./data/raw、data//raw、
    D:\\...\\data\\raw、DATA/RAW、deliverables/../data/raw —— 字符串比较全部漏过。
    """
    if tool in WRITE_TOOLS and touches_protected(rel):
        deny("原始数据/口径文档为只读区，禁止写入或删除：%s" % rel)
    return None


def g2_shell_danger(tool, tin, rel):
    """G2 危险命令拦截（对应 AGENTS.md R1 的执行面兜底）。

    为什么要拦 shell：G1 只能看见"工具参数里的路径"。一旦模型改为
    `python scripts/xxx.py`（脚本内部去删 data/raw），G1 就完全瞎了——
    所以命令文本必须先过一遍高危模式表。这是"第二道物理门"。
    """
    if tool != "run_shell":
        return None
    cmd = str(tin.get("command", ""))
    for pat, desc in DANGEROUS_PATTERNS:
        if re.search(pat, cmd, re.I):
            deny("命中高危命令规则（%s）：%s" % (desc, cmd[:80]))
    return None


def g3_pii_leak(tool, tin, rel):
    """G3 个人信息外泄拦截（对应 AGENTS.md R2）。

    只拦"写出去"的内容，不管读。读原始数据必须放行，否则做不了活——
    把安全做成"全禁"等于把防线拆了。
    地址口径（R2 的可判定补充）见 AGENTS.md 第 6 节：
      命中 (北京|上海|广州|深圳)市? + 2~10 个汉字 + (区|县|路|街|道)，或字段名含 address。
    """
    if tool not in WRITE_TOOLS:
        return None
    content = str(tin.get("content", "")) + " " + str(tin.get("content_append", ""))
    for rx, name in ((PHONE_RE, "手机号"), (IDCARD_RE, "身份证号"),
                     (MEMBER_RE, "明文会员号"), (ADDR_RE, "收货地址")):
        m = rx.search(content)
        if m:
            deny("交付内容含%s（命中片段：%s），违反 R2/S10 脱敏要求"
                 % (name, m.group(0)[:12]))
    return None


def g4_write_boundary(tool, tin, rel):
    """G4 写入边界（最小权限白名单）。

    为什么用白名单：黑名单永远漏。模型的下一步动作是无限的（改 hook、写
    config.json、改 AGENTS.md、删 acceptance/…），白名单只需要列清楚
    "允许的三件事"。这也是 .harness/config.json 里 permissions 的设计意图。
    G4 与 G1 的区别：G1 是"保护特定目录"，G4 是"只信三个出口"。
    """
    if tool not in WRITE_TOOLS:
        return None
    if rel.endswith("/") or rel == "":
        return None  # 目录级操作交给 G1 与权限层
    if not any(rel.startswith(p) for p in WRITE_ALLOW_PREFIXES):
        deny("写入越界：%s（只允许写 deliverables/、data/out/、.harness/audit/）" % rel)
    return None


def g5_network(tool, tin, rel):
    """G5 断网（对应 AGENTS.md R5）。

    与"在提示词里写请不要上网"的差别：这条是程序判定，模型无法"忘记"。
    """
    if tool in ("http_get", "http_post", "fetch", "web_fetch", "web_search"):
        deny("策略禁止出网（R5：不新增外部数据源）：%s" % str(tin.get("url", ""))[:60])
    return None


def g6_obfuscation(tool, tin, rel):
    """G6 路径混淆（进阶）。

    为什么还要这一层：rel 已经归一化了，`deliverables/../data/raw/x.csv`
    会变成 `data/raw/x.csv` 被 G1 抓住；但 `%2e%2e` 这类写法在**不同**的
    归一化实现下可能漏掉（大小写、反斜杠、短路径 8.3、符号链接）。
    这里看**原始**路径，宁可误报也不要漏放。
    """
    if tool not in WRITE_TOOLS:
        return None
    raw = str(tin.get("path") or tin.get("target") or "")
    suspicious = re.search(r"(\.\.|%2e|%2E|~|\$home|\$\{)", raw, re.I)
    if suspicious and touches_protected(rel):
        deny("检测到路径混淆：%s → %s（命中 %s）" % (raw, rel, suspicious.group(0)))
    return None


def main():
    p = read_payload()
    if not p or p.get("_parse_error"):
        # 输入坏了就 fail-closed：宁可误拦，不可漏放
        deny("Hook 未能解析输入，按 fail-closed 处理")
    tool = p.get("tool_name", "")
    tin = p.get("tool_input", {}) or {}
    root = project_root(p)
    rel = norm_path(tin.get("path") or tin.get("target") or "", root)

    for guard in (g1_protect_raw, g2_shell_danger, g3_pii_leak,
                  g4_write_boundary, g5_network, g6_obfuscation):
        guard(tool, tin, rel)

    audit("PreToolUse", tool, "allow", "no rule matched", {"path": rel})
    print('{"hookSpecificOutput":{"permissionDecision":"allow","permissionDecisionReason":"no rule matched"}}')


if __name__ == "__main__":
    main()
