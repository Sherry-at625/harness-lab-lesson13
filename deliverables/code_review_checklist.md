# 代码审查与测试清单（已填，20/20 项带证据）

> 审查对象：`project/`（青禾生鲜 Q3 订单数据治理）
> 填写人：Sherry ｜ 日期：2026-11-17 ｜ 工具：WorkBuddy Agent 模式
> 原则：**证据必须是别人能复现的，不是你相信的。**"是"但没有证据，视为未检查。

## A｜范围与边界（5 项）

| 编号 | 检查项 | 是/否 | 证据（文件:行 / 命令输出） |
|---|---|---|---|
| A1 | 只修改了允许的文件，没碰 `data/`、`acceptance/`、期望值 | **是** | `data/raw/` 4 个 CSV 的 md5 与解压时一致（`python -c "import hashlib..."` 逐个比对）；`git status` 显示新增仅 `scripts/{clean_orders,compute_metrics,make_report,check_idempotent,drill_perturb,drill_break_it}.py`、`deliverables/*`、`.harness/hooks/{pre,post}_tool_use.py`、`AGENTS.md` |
| A2 | 没有新增第三方依赖，没有联网调用 | **是** | 全部脚本 `import csv,json,os,re,sys,hashlib,unicodedata,decimal,subprocess` —— 纯标准库；`grep -n "requests\|urllib.request\|http" scripts/*.py` 无命中；`.harness/config.json` 的 `permissions.network = "deny"` |
| A3 | 没有删除或跳过任何验收项 | **是** | `eval_deliverables.py` 未修改（12 个 `@check` 装饰器数量不变）；`test_hooks.py` 的 `CASES` 仍为 21 条；`acceptance/expected_metrics.json` 未修改 |
| A4 | 没有把期望值写死在代码里 | **是** | `grep -n "29534\|583\|350\|0.0617" scripts/*.py` → 0 命中。`clean_orders.py` 中不含任何指标数字；`compute_metrics.py` 全部从 CSV 现算 |
| A5 | 改动量与任务规模匹配，没有顺手重构无关模块 | **是** | `harness.py`、`_common.py`、`config.json`、`profile_data.py`、`eval_deliverables.py` 全部 0 改动；新增脚本 6 个，每个单一职责 |

## B｜口径正确性（6 项）

| 编号 | 检查项 | 是/否 | 证据 |
|---|---|---|---|
| B1 | 状态归一化：去空白、只保留有效状态 | **是** | `clean_orders.py:82-84` `clean_text()` 去 `\u3000`（全角空格）与 `\u200b`（零宽）；`clean_orders.py:167-170` `refund_flag` 空→0、非 1→0 |
| B2 | 清洗顺序严格按规格 | **是** | `clean_orders.py` 的 `for key in order:` 主循环内，S2(行119)→S3(126)→S4(132)→S5(137)→S9(147/155)→S6(164)→S7(168)→S8(173)。**注意**：S9 必须在 S6 之前，否则空单价无法重算——这是本实现的显式决策，已记入 `quality_report.json.notes[0]` |
| B3 | 去重规则：保留 `order_time` **最早** | **是** | `clean_orders.py:104-111`：`if clean_text(r["order_time"]) < clean_text(prev["order_time"]): dedup[key] = (i, r)`。**并列时取文件中靠前的一条**（规格未写，已记入 `open_questions[0]`） |
| B4 | 剔除计数器齐全，且 `有效 = 原始 − 剔除之和` | **是** | `make_report.py:96-100` 的 `assert raw - S1 - removals == clean`（624−18−23=583）；运行输出即证明断言未触发 |
| B5 | 空品类按主数据 `category_std` 覆盖，不剔除 | **是** | `clean_orders.py:141` `r["category"] = clean_text(master["category_std"])`；`eval` c5「非法类目」PASS，c9「8 类目齐全」PASS |
| B6 | 排序规则一致：GMV 降序、同值按键升序 | **是** | `compute_metrics.py:88` `sorted(store.items(), key=lambda kv: (-kv[1]["gmv"], kv[0]))`；类目同法；`clean_orders.py:255` 交付表排序键为 `(order_time, order_id, sku_id)` |

## C｜数值与格式（4 项）

| 编号 | 检查项 | 是/否 | 证据 |
|---|---|---|---|
| C1 | 金额用 `Decimal` 解析与求和，输出 2 位小数 | **是** | `clean_orders.py:32,58-70` `d()` / `money()`（`quantize(Q2, ROUND_HALF_UP)`）；`compute_metrics.py:63-66` 全部 `Decimal` 累加；`grep -c "float(" scripts/*.py` 仅出现在输出转换处 |
| C2 | 比率 4 位小数，报告展示 2 位百分比 | **是** | `compute_metrics.py:42-43` `r4()` 用 `Decimal("0.0001")`；`metrics.json` 中 `refund_rate: 0.0617`、`gross_margin_rate: 0.3054`；`report.md` 显示 `6.17%` / `30.54%` |
| C3 | 金额展示带千分位/两位小数，占比带百分号 | **是** | `report.md`「**Q3 净销售 GMV 为 29,534.98 元**」；`make_report.py` 表格内 `%.2f`、占比 `%.1f%%` |
| C4 | JSON 无多余键、无时间戳；报告标题齐全且顺序正确 | **是** | `metrics.json` 的键：period/gmv/valid_orders/all_checked_orders/aov/refund_orders/refund_rate/refund_amount/gross_profit/gross_margin_rate/row_count/by_store/by_category/weekly_gmv —— **无 generated_at**；`report.md` 五个二级标题：结论先行 / 数据质量 / 经营结构与建议 / 这份结论的边界 / 复现方式 |

## D｜测试有效性（3 项）

| 编号 | 检查项 | 是/否 | 证据 |
|---|---|---|---|
| D1 | 覆盖边界值：金额符号/千分位/非法值、多种日期、去重并列 | **是**（部分） | 日期 5 种写法全测（`date_formats` 分布 ISO 582 / 美式 8 / 中文 7 / 斜杠 18 / 不补零 9）；金额 `d()` 处理 `¥` `,` 与非法值；**去重并列未构造专门用例**（真实数据里 `order_time` 并列 0 例），已在 `open_questions` 声明 |
| D2 | 验收测试会红，不是永远绿 | **是** | `scripts/drill_break_it.py` D3 组：把 `valid_orders` 改成行数 → `总分 88/113`，退出码 1，两项 FAIL |
| D3 | 确定性 + 扰动测试有效 | **是** | `check_idempotent.py`：两次 md5 均 `b1e6c75c…` / `a939fc36…` → PASS；`drill_perturb.py`：追加 100 元订单 → gmv **恰好 +100.0000**、gross_profit +60、valid_orders +1；再追 200 元退款单 → gmv **+0.0000**、refund_orders +1 → 8 项全 PASS |

## E｜可解释性（2 项）

| 编号 | 检查项 | 是/否 | 证据 |
|---|---|---|---|
| E1 | 能指出状态归一化/去重/金额解析/聚合口径的代码位置 | **是** | 状态归一化 `clean_orders.py:167-170`；去重 `clean_orders.py:104-111`；金额解析 `clean_orders.py:58-70`；聚合口径 `compute_metrics.py:61-75`（O/R/E 三集合定义在 57-60） |
| E2 | 能解释金额为何不能用 float，并指出最不可信的一段 | **是** | 见下方「三个最危险的假设」第 2 条 + `harness_evidence.json.limits[1]`。最不可信的一段是 `clean_orders.py:141` 的 `category` 覆盖——若主数据本身错，交付表 8 个类目会整体错位，而所有验收仍然全绿 |

---

## 审查报告

### 1. 结论

**有条件通过。** 12 项机器验收 113/113、21 项 Hook 用例全绿、计数守恒与幂等均已断言通过。
"条件"指的是：下文 3 个假设未经业务方书面确认，其中第 1 条（退款口径）一旦翻转，GMV 会变动约 2,289 元（7.75%）。

### 2. 缺陷清单（按严重程度排序）

| 严重度 | 文件:行号 | 问题 | 可能后果 | 最小修复 |
|---|---|---|---|---|
| **严重** | `compute_metrics.py:70` | GMV 只在 `refund_flag == 0` 的订单上求和；若某单**部分行**退款而 `refund_flag` 只标在其中一行，该单会被整体判为退款单，GMV 少算 | GMV 偏低，且 `by_store` 之和仍等于总数（自洽但错）——验收抓不到 | 在 `quality_report.json` 里统计"同一 order_id 内 refund_flag 不一致"的订单数；本次为 0，但需持续监控 |
| **一般** | `clean_orders.py:164-166` | S6 无条件用重算值覆盖 `amount`，包括"原始值为 0 而重算值正常"的情况（可能代表未成交单） | 把"未成交"误判成"成交"，GMV 虚高 | 若 `原 amount == 0 且 qty > 0`，先计入 `open_questions` 而不是直接覆盖；本次该类 0 行 |
| **一般** | `.harness/hooks/pre_tool_use.py::g6_obfuscation` | 只看字符串里的 `..`/`%2e`/`~`，不展开符号链接与 Windows 8.3 短路径 | `deliverables/link.csv` 若是指向 `data/raw` 的软链，写入合法路径即可穿透 | 用 `os.path.realpath()` 再归一化一次；已知未修复，已列入 `harness_evidence.json.limits[4]` |
| **建议** | `check_idempotent.py:20` | 幂等只比 md5，不比"内容语义"，若两次都错得一模一样也会 PASS | 幂等 PASS 被误读为"结果正确" | 在注释与 SKILL.md 里都已写明"幂等验稳不稳，不验对不对"；扰动测试补上"真算"的验证 |
| **建议** | `compute_metrics.py:96` | `weekly_gmv` 缺失周补 0，与"该周无订单"不可区分 | 读者无法判断是"零销售"还是"数据缺失" | 在 `quality_report.json` 增加 `weeks_without_orders` 列表 |

### 3. 口径核对

| 项目 | 符合/不符合/无法判断 | 证据 |
|---|---|---|
| 剔除计数器 | **符合** | 10 类规则命中（S1=18、S3=6、S4=3、S5=3、S6=37、S7=6、S8=5、S9=10/7、R2=4），`quality_report.json.rules` 逐项可查 |
| 清洗顺序 | **符合**（含一处显式偏离） | 按 S1–S10；S9 提前到 S6 之前（否则空单价无法重算），已在 `notes` 声明 |
| 金额精度（Decimal） | **符合** | 全链路 `Decimal`，仅在输出时 `float()`；GMV 与参考值 29,534.98 精确一致（非容差内接近） |
| 排序规则 | **符合** | 门店/类目 GMV 降序 + 键升序；交付表按 `(order_time, order_id, sku_id)` 稳定排序 |
| 报告五节结构 | **符合** | 结论先行 / 数据质量 / 经营结构与建议 / 边界 / 复现方式 |

### 4. 测试有效性：现有测试能否发现这三类问题？

| 问题类型 | 能否发现 | 理由 |
|---|---|---|
| **写死 golden** | **能** | `drill_perturb.py`：写死 GMV 的实现在"追加 100 元订单后 gmv 必须 +100"这一步必然失败。这是唯一能区分"真算"与"假算"的检查 |
| **修改测试变绿** | **不能（自动）** | `eval_deliverables.py` 与 `test_hooks.py` 没有自校验哈希。若有人把 `CASES` 里的期望值改掉，测试照样全绿。**只能靠 `check_integrity` 类的哈希清单或教师复跑**——本次未提供该机制，是一个真实缺口 |
| **金额用 float** | **部分能** | 29,534.98 这种量级上 float 与 Decimal 差异约 1e-12，远小于 0.5% 容差，**验收抓不到**。只有"D1 改 0 位小数"这类大改动才可能被抓（实测偏差 0.48 元 < 147 元容差，仍抓不到，见下） |

**关键发现（这一条比 113/113 更有价值）**：`drill_break_it.py` 实测三组破坏，
只有 D3 被抓，D1/D2 仍能拿满分：

| 破坏 | 实际偏差 | 验收结果 | 为什么没红 |
|---|---|---|---|
| D1 GMV 保留 0 位小数 | 29,535 vs 29,534.98 = **0.48 元** | **113/113 通过** | 容差是 0.5%（≈147 元），0.48 元远在容差内 |
| D2 退款率分母用 E 而不是 O | 0.0617 → 0.0657 = **0.0040** | **113/113 通过** | 比率容差 `rate_abs = 0.005`，0.004 < 0.005 |
| D3 有效订单数用行数 | 350 → 583 | 88/113 失败 | 计数类无容差 |

**结论**：这台"验收机器"的灵敏度恰好卡在本案例两个最典型口径错误的**临界值之内**——
退款率口径错（偏差 0.0040）和金额精度错（偏差 0.48 元）都能通过。
**验收全绿 ≠ 口径正确，这是本课最该带走的一条。** 唯一可靠的防线是"用另一条独立路径重算"（本次是 `p2_metric_consistency` 用 CSV 重算 + `drill_perturb` 扰动响应）。

### 5. 三个最危险的假设

| # | 假设 | 口径里写了吗 | 如何验证 |
|---|---|---|---|
| 1 | **退款以 `orders.refund_flag` 为准，而不是以 `refunds.csv` 为准** | `metrics_spec.md` 写了"以 orders 表为准"，但没说"若两表不一致信谁" | 本次实测：`refunds.csv` 有效 19 条、`orders` 中 `refund_flag=1` 的订单 23 单，**两者差 4**（正好是 R2 超额退款被剔除的 4 条）。若某天退款表的丢失不是"超额"而是"漏导出"，退款率会偏低。验证方式：每次交付时打印两者差额，差额 ≠ R2 计数就报警 |
| 2 | **金额差异 S6 一律以 `qty*unit_price-discount` 为真值** | 写了"用重算值覆盖"，但没写"谁是真相来源" | 若上游的错误在 `unit_price` 而非 `amount`，重算会把正确的总额改成错误的。验证方式：统计 S6 命中行的 `|原值-重算值|` 分布，若出现成簇的大偏差（如整列 ×10），说明是单价字段错位而不是金额笔误 |
| 3 | **`week` 的 W13 吸收第 92 天** | 写了（"第 92 天并入 W13"），但没写"若期间起始日不是周一如何" | 本次 2026-07-01 是周三，W01 实际跨 07-01（周三）~07-07（周二）。若业务按"自然周"（周一~周日）解读，"周报"会对不上。验证方式：找财务要一张已发布的周报，用同一份数据复现其中一周的 GMV |

---

**签字**：实现者 Sherry ｜ 审查者 本清单 + `drill_break_it.py` 实测输出 ｜ 日期 2026-11-17
**最终结论**：有条件通过（条件 = 上文 3 个假设需业务方确认，其中第 1 条影响 GMV 7.75%）
