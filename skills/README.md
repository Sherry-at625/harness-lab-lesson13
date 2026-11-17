# skills/ —— 技能放在这里只是"草稿"，装到对的目录才生效

## 两个安装位置，作用域不同

| 位置 | 作用域 | 适用场景 |
|---|---|---|
| `~/.workbuddy/skills/<skill-name>/SKILL.md` | 当前用户的所有项目 | 个人方法沉淀，跨项目复用 |
| `<项目>/.workbuddy/skills/<skill-name>/SKILL.md` | 仅当前项目 | 团队共享，跟着仓库走 |

（Trae 侧的等价物是 `.trae/rules/*.md` 项目规则 + 全局 `~/.trae/user_rules`；
技能这类"带流程的方法包"，优先用 WorkBuddy 的 skills 目录。）

## 本课要求

1. 在 `skills/fresh-order-audit/SKILL.md` 写好你的版本（可以先抄参考实现的结构，内容要换成你自己的）
2. 复制到用户级目录：
   ```bash
   # macOS / Linux
   mkdir -p ~/.workbuddy/skills/fresh-order-audit
   cp skills/fresh-order-audit/SKILL.md ~/.workbuddy/skills/fresh-order-audit/
   ```
   ```powershell
   # Windows PowerShell
   New-Item -ItemType Directory -Force "$env:USERPROFILE\.workbuddy\skills\fresh-order-audit"
   Copy-Item "skills\fresh-order-audit\SKILL.md" "$env:USERPROFILE\.workbuddy\skills\fresh-order-audit\"
   ```
3. **新开一个会话**，用一句自然语言触发它，验证真的生效（旧会话有上下文缓存，不算验证通过）

## 判断"装对了"的三个证据

- 目录结构是 `.../skills/fresh-order-audit/SKILL.md`（不是 `SKILL.md.txt`）
- frontmatter 的 `name` 与目录名一致，`description` 里含触发词
- 新会话里问"你有哪些技能"，或直接描述任务，能看到它被调用
