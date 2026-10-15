# online-office-delivery（在线办公文档交付与回读）

## 定位

本地 XLSX/CSV → 在线电子表格的交付与回读：依赖宿主已授权的官方 CLI
（`lark-cli`），先从 CLI 自身 `--help` 核对命令语法，再执行导入、提取真实
URL、内容回读与可编辑校验。无授权 CLI 或语法无法核实时显式 blocked，
绝不伪造链接、不以本地文件或截图冒充在线交付。

## 用法

```
python scripts/online_delivery.py --plan plan.json --outdir out/ [--cli lark-cli]
```

plan.json：

```json
{
  "target": {"type": "lark_sheet", "folder_token": "可选"},
  "files": [{"local": "merged.xlsx", "name": "Q3对账表"}],
  "keep_check_sheet": false
}
```

退出码：0=delivered，2=输入错误（计划非法/空清单/本地文件缺失/不支持目标
或扩展名），3=blocked（无授权 CLI/语法无法核实/CLI 未授权），4=failed
（导入/回读/可编辑校验失败）。

## 交付闭环（缺一不可，修复旧空壳缺陷）

旧实现的三条虚假成功路径已全部封堵：

| 旧缺陷 | 现行为 |
| --- | --- |
| CLI 返回 0 即 delivered，未提取 URL、回读只标 pending | URL 从 CLI 输出提取，`ready=true`+`job_status=0` 闭环，回读/可编辑未完成即 failed |
| 空 files 也可 delivered | 空清单为输入错误（退出 2） |
| 猜测 lark-cli 子命令（如不存在的 `sheets create`） | 只使用官方文档核对过的 shortcut，且执行前必须出现在 CLI 自身 `--help` 中，否则 blocked |

核对过的命令集（来自授权审计材料中 lark-cli 官方语义）：
`sheets +workbook-import --file <cwd相对路径> [--folder-token] [--name]`、
`+workbook-info --url`、`+csv-get --url --sheet-name/--sheet-id --range`、
`+csv-put --start-cell --csv`、`+sheet-create --title`、
`+sheet-delete --sheet-id --yes`（high-risk 门禁）。`timed_out=true` 时按
CLI 返回的 `next_command` 续查（≤3 次）。

## 交付成功的完整条件

1. 导入信封 `ok=true` 且 `ready=true` 且 `job_status=0`（`timed_out` 已续查收敛）
2. 从 CLI 返回中提取到真实 https URL（只提取，不构造）
3. 内容回读一致：`+workbook-info` 子表集合与本地相符；`+csv-get` 抽样
   （每表前 5×5）与本地基准一致（数值容差、文本字面，"001"≠"1"）
4. 可编辑校验通过：`__delivery_check__` 子表金丝雀写入→回读命中→
   `--yes` 删除清理（清理失败仅 warning）

## 输出

`delivery-report.json`：`probe`（CLI 解析/环境变量名存在性/语法核对与帮助
摘录）、`missing`（blocked 时缺失项）、`results`（逐文件导入信封摘要、URL、
token、回读明细与不一致单元格、权限校验、状态与原因）、`cli_invocation_log`
（全量 argv/cwd/返回码/输出摘录审计）、`warnings`、`notes`。

## 测试

`tests/office_skills/test_online_office_delivery.py`（黑盒，不 import 被测
模块）：正常（xlsx/CSV 全管线交付）、复杂（timed_out 按 next_command 续查、
保留检查子表、多文件部分失败、readback=false 被忽略仍回读）、异常（无 CLI
blocked、帮助缺命令 blocked、空清单/缺文件/坏计划/不支持目标与 .xls、回读
不一致、无 URL 不伪造、写权限拒绝、未授权 blocked）。正常/复杂路径使用
严格按官方文档语义实现的 lark-cli 测试替身验证管线真实行为（相对路径
约束、--yes 门禁、信封解析均有断言）；**替身仅验证管线，不冒充真实在线
交付**——本环境无授权 lark-cli，真实在线验收 not_run（见 per-skill
handoff）。

## 来源与许可

原创实现（decision-register：independent_implement_env_blocked；未复制
豆包包体代码，仅引用其文档中公开的 lark-cli 命令语法事实）。代码与文档随
仓库根 LICENSE（Apache-2.0）分发；运行时依赖 openpyxl 3.1.5（MIT），已登记
license-register.json runtime_dependencies。lark-cli/飞书为外部宿主工具，
其账号授权与许可由宿主环境承担。
