# online-office-delivery（在线办公文档交付与回读）

## 定位

本地 XLSX/CSV → 在线电子表格的交付与回读：依赖宿主已授权的官方 CLI
（`lark-cli`），先从 CLI 自身 `--help` 核对命令语法，再执行导入、提取真实
URL、内容回读与可编辑校验。无授权 CLI 或语法无法核实时显式 blocked，
绝不伪造链接、不以本地文件或截图冒充在线交付。

## 用法

```
python scripts/online_delivery.py --plan plan.json --outdir out/ [--cli lark-cli]
                              [--as user|bot]
```

plan.json：

```json
{
  "target": {"type": "lark_sheet", "folder_token": "可选"},
  "files": [{"local": "merged.xlsx", "name": "Q3对账表"}],
  "identity": "user",
  "keep_check_sheet": false
}
```

- `identity` / `--as`：显式指定交付身份，透传 CLI `--as`（CLI 命令行参数
  优先于计划）。全程显式选择身份，避免 CLI 在 bot/user 间隐式切换。
- 退出码：0=delivered，2=输入错误（计划非法/空清单/本地文件缺失/不支持目标
  或扩展名），3=blocked（无授权 CLI/语法无法核实/CLI 未授权/缺 scope），
  4=failed（导入/回读/可编辑校验失败/身份不符）。

## 交付闭环（缺一不可，修复旧空壳缺陷）

旧实现的三条虚假成功路径已全部封堵：

| 旧缺陷 | 现行为 |
| --- | --- |
| CLI 返回 0 即 delivered，未提取 URL、回读只标 pending | URL 从 CLI 输出提取，`ready=true`+`job_status=0` 闭环，回读/可编辑未完成即 failed |
| 空 files 也可 delivered | 空清单为输入错误（退出 2） |
| 猜测 lark-cli 子命令（如不存在的 `sheets create`） | 只使用官方文档核对过的 shortcut，且执行前必须出现在 CLI 自身 `--help` 中，否则 blocked |

核对过的命令集（按真实 lark-cli 1.0.98 `--help` 与官方 reference 二次核对，
运行时仍以 CLI 自身 `--help` 复核）：
`sheets +workbook-import --file <cwd相对路径> [--folder-token] [--name]`、
`+workbook-info --url`、`+csv-get --url --sheet-id/--sheet-name --range`、
`+csv-put --start-cell --csv`、`+sheet-create --title`、
`+sheet-delete --sheet-id --yes`（high-risk 门禁）；各命令可加 `--as user|bot`。
`timed_out=true` 时按 CLI 返回的 `next_command` 续查（≤3 次）。

关键输出契约（真实 CLI）：成功信封 `{ok, identity, data, ...}` 走 stdout；
错误信封 `{ok, identity, error:{type,subtype,missing_scopes,console_url,...}}`
走 stderr。`+csv-get` 主入口是 `data.annotated_csv`，每逻辑行带 `[row=N] `
前缀，脚本去前缀后再逐格比对。`+workbook-import` 成功返回
`token/url/ticket/ready/job_status`；`+workbook-info` 的 `sheets[]` 用
`title`（缺省回退 `sheet_name`）与 `sheet_id`。

## 交付成功的完整条件

1. 导入信封 `ok=true` 且 `ready=true` 且 `job_status=0`（`timed_out` 已续查收敛）
2. 从 CLI 返回中提取到真实 https URL（只提取，不构造）
3. 内容回读一致：`+workbook-info` 子表集合与本地相符；`+csv-get` 抽样
   （每表前 5×5，按 sheet_id 优先定位）与本地基准一致（数值容差、文本字面，
   "001"≠"1"）
4. 可编辑校验通过：`__delivery_check__` 子表金丝雀写入→回读命中→
   `--yes` 删除清理（清理失败仅 warning）
5. 身份一致：请求身份（`--as`/plan `identity`）与 CLI 信封 `identity` 一致，
   否则 failed（不在错误账号下判交付）

## 输出

`delivery-report.json`：`probe`（CLI 解析/版本/环境变量名存在性/语法核对与
帮助摘录）、`identity_requested`、`missing`（blocked 时缺失项，含
`missing_scopes`/`console_url`）、`results`（逐文件导入信封摘要、URL、
token、回读明细与不一致单元格、权限校验、状态与原因）、`cli_invocation_log`
（全量 argv/cwd/返回码/输出摘录审计）、`warnings`、`notes`。

## 测试

`tests/office_skills/test_online_office_delivery.py`（黑盒，不 import 被测
模块，17 项）：正常（xlsx/CSV 全管线交付）、复杂（timed_out 按 next_command
续查、保留检查子表、多文件部分失败、readback=false 被忽略仍回读、
annotated_csv `[row=N]` 前缀与文本前导零回读、身份透传记录、身份不符失败、
缺 scope 返回 missing_scopes/console_url）、异常（无 CLI blocked、帮助缺命令
blocked、空清单/缺文件/坏计划/不支持目标与 .xls、回读不一致、无 URL 不伪造、
写权限拒绝、未授权 blocked）。正常/复杂路径使用严格按真实 lark-cli 1.0.98
输出语义实现的测试替身（annotated_csv 前缀、{ok,identity,data} 信封、--as
身份、结构化错误信封、相对路径约束、--yes 门禁均有断言）。

**真实在线验收（2026-10-15，DeepSeek/opencode 独立进程）**：本机已装官方
lark-cli 1.0.98，用户 `auth status --json --verify` 为 ready/verified，以
`--as user` 对纯合成 XLSX（常规单表 + 三 sheet 中文复杂表）跑通脚本端到端：
真实导入 `ready=true/job_status=0`、取得官方返回 URL、`+csv-get` 全量抽样
0 不一致（含文本前导零 "001"/"A001"、中文、金额、含空格文本）、金丝雀可编辑
校验通过（`keep_check_sheet=true` 保留 `__delivery_check__` 作为证据）。
真实 URL 与独立回读记录见 `work/skills-comparison-20261013/office-delivery-glm53/
online-live-review-20261015/`。竞品同题对齐未验证（parity_status=not_verified）。

## 来源与许可

原创实现（decision-register：independent_implement_env_blocked；未复制
豆包包体代码，仅引用其文档中公开的 lark-cli 命令语法事实）。代码与文档随
仓库根 LICENSE（Apache-2.0）分发；运行时依赖 openpyxl 3.1.5（MIT），已登记
license-register.json runtime_dependencies。lark-cli/飞书为外部宿主工具，
其账号授权与许可由宿主环境承担。
