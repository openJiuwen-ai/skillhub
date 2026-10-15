---
name: online-office-delivery
description: 在线办公文档交付与回读：探测宿主环境已授权的官方 CLI（默认飞书 lark-cli，可选钉钉官方 dws），先用 CLI 自身 --help 核对命令语法再执行导入/回读/可编辑校验；交付成功必须同时满足真实导入完成、CLI 返回的真实 URL、内容回读一致、金丝雀写入回读通过，缺任何一环即失败；无授权 CLI、语法无法核实或未授权时显式 blocked 并以非零退出，绝不伪造链接或以本地文件/截图冒充在线交付。当用户要求"把表格发布到线上/创建在线文档并修改"时使用。
display_name: 在线表格交付与回读
tags: [办公与生产力, 文档处理]
license: Apache-2.0
---

# 在线办公文档交付与回读

## 何时使用

- 本地 XLSX/CSV → 在线电子表格（真实可访问可编辑链接）
- 用户要求"发布到飞书/建在线表格给大家填/在线文档定点修改"

## 前提（诚实边界）

本技能**依赖宿主环境提供已授权的官方 CLI**。默认后端为飞书 `lark-cli sheets`
（已真实链路验证）；另提供可选钉钉后端 `dingtalk_sheet`（官方 DingTalk
Workspace CLI `dws`），仅在计划显式选择时使用，不是其它办公技能的必需依赖。
技能不内置任何凭据，不假设环境自带专属工具：

- PATH 中无 CLI → `blocked`（退出码 3），报告列出缺失项
- CLI 存在但必需命令未出现在其 `--help` 输出中 → `blocked`：**不猜测子命令**
- CLI 调用被拒绝（401/403/未授权/缺 scope 类错误）→ `blocked`，缺失项为有效
  授权，缺 scope 时报告只回传 `missing_scopes` 与官方授权页 `console_url`，
  不申请与本任务无关的权限
- 任何环节（导入/URL/回读/可编辑）失败 → `failed`（退出码 4），绝不判成功

## 用法

```
python scripts/online_delivery.py --plan plan.json --outdir out/ [--cli lark-cli]
                              [--as user|bot]
```

plan.json：

```json
{
  "target": {"type": "lark_sheet", "folder_token": "可选，飞书云空间文件夹"},
  "files": [{"local": "merged.xlsx", "name": "Q3对账表"}],
  "identity": "user",
  "keep_check_sheet": false
}
```

- `identity`/`--as` 显式指定交付身份（CLI 参数优先），全程不被 CLI 隐式切换；
  请求身份与 CLI 信封 `identity` 不一致即 failed
- `local` 支持相对计划文件的路径；`files` 不允许为空（空清单是输入错误）
- 仅支持 `.xlsx`/`.csv`；`.xls` 无可信本地值读取器，请先转为 `.xlsx`
- `readback` 恒定执行（计划里写 false 会被忽略并记 warning）
- docx/pptx/bitable 目标未获命令语法核实，计划即拒绝（输入错误），不猜测执行

## 交付闭环（缺一不可）

1. **语法核对**：运行 `<cli> --help` 与 `<cli> sheets --help`，确认
   `+workbook-import/+workbook-info/+csv-get/+csv-put/+sheet-create/+sheet-delete`
   全部存在才继续（依据官方文档核对过的 shortcut 集）
2. **导入**：`sheets +workbook-import --file <相对路径> [--name] [--folder-token]`
   ——`--file` 只接受 cwd 相对路径，脚本以文件所在目录为 cwd；
   信封 `ready=true` 且 `job_status=0` 才算完成；`timed_out=true` 时按返回的
   `next_command` 续查（上限 3 次）
3. **真实 URL**：从 CLI 返回信封/输出提取，只提取不构造；无 URL 即失败
4. **内容回读**：`+workbook-info` 核对子表集合（`title`，缺省回退
   `sheet_name`）；`+csv-get`（主入口 `data.annotated_csv`，先去掉每逻辑行
   `[row=N] ` 前缀）按 `sheet_id` 优先定位、抽样比对本地基准
   （xlsx 用 openpyxl 缓存值、csv 用标准库；数值按容差、文本按字面，
   "001"≠"1"），任何不一致即失败
5. **可编辑校验**：新建 `__delivery_check__` 子表 → `+csv-put` 写入金丝雀
   → `+csv-get` 回读比对 → `+sheet-delete --yes` 清理（失败仅记 warning，
   不影响已通过的校验）；`keep_check_sheet: true` 可保留审计痕迹

输出 `delivery-report.json`：探测/语法核对、逐文件导入信封、URL、回读明细、
权限校验、全量 CLI 调用审计日志（argv/返回码/输出摘录）。

退出码：0=delivered，2=输入错误，3=blocked，4=failed。

## 钉钉后端（可选 dingtalk_sheet）

保留上表 `lark_sheet` 契约不变；当计划 `target.type` 为 `dingtalk_sheet` 时，
`online_delivery.py` 分派到独立小模块 `scripts/dingtalk_delivery.py`（也可直接
运行该模块）。命令事实仅来自官方 **DingTalk Workspace CLI (dws) v1.0.64** 的
`--help`/`schema`/包内 reference，运行时仍以 CLI 自身 `--help` 复核。

plan.json（钉钉）：

```json
{
  "target": {"type": "dingtalk_sheet", "folder_token": "云盘文件夹ID",
             "workspace": "可选，知识库ID（folder_token 与 workspace 至少一个）"},
  "files": [{"local": "merged.xlsx", "name": "Q3对账表"}],
  "keep_check_sheet": false
}
```

用法：

```
python scripts/dingtalk_delivery.py --plan plan.json --outdir out/ \
        [--cli <dws 可执行文件>] [--profile <corpId:userId>]
```

核对过的命令集（依据 dws v1.0.64 `--help`，运行时复核；不猜测子命令）：
`sheet import create --file (--folder-token|--workspace) [--name]`、
`sheet +list-sheets --node`、`sheet +read --node --sheet-id --range`
（严格，截断失败关闭）、`sheet csv-get/csv-put`、`sheet new`、
`sheet delete-sheet --yes`（user_required）。命令失败即 blocked/failed。

钉钉与飞书的契约差异（不照搬）：
- 官方 `sheet import` **要求** `--folder-token` 或 `--workspace` 至少一个，
  缺失按输入错误拒绝（不猜测默认位置）；只支持 xlsx/xls，**不支持 csv**；
  本后端本地回读基准用 openpyxl，故只接受 `.xlsx`（`.xls` 请先转存）。
- 无 `ready/job_status`：成功信封为
  `{success:true, taskId, documentUrl, documentName, documentType, nodeId}`；
  轮询超时 `{success:false, timed_out:true, taskId, status, next_command}`
  （退出码 0），按 `next_command` 续查（≤3 次）。
- 身份不是 `--as user/bot`，而是组织/账号 `--profile`；授权只读
  `dws auth status --readonly --format json`，不含任何密文。
- `+read` 严格读取要求 `complete/hasMore/truncationReasons` 无截断才判回读通过；
  `+list-sheets` 缺集合即失败，绝不把未知结构当空结果。
- 未登录或**组织未开启 CLI 访问**（需组织主管理员在
  https://open-dev.dingtalk.com/fe/old#/developerSettings 开启后重新登录）
  记 `blocked`（非能力缺失），报告含 `admin_url`。

**验证范围**：飞书表格导入、回读和当前账号写权限已做真实环境验证。钉钉适配已通过脚本回归；真实交付要求组织开通 CLI 访问并完成账号授权，尚未完成同等线上验收。不得将脚本测试结果表述为钉钉线上成功。

## 依赖

Python 3.9+；xlsx 回读基准需 openpyxl（MIT）。在线交付本身依赖宿主已授权的
lark-cli 或等价官方 CLI/API——无授权环境在线用例记 `blocked`/`not_run`，
不以 mock 冒充在线验收。

## 限制

- 公式单元格按 openpyxl 缓存值比对；无缓存值的公式（纯 openpyxl 生成、未经
  Excel/WPS 保存）会判不一致，交付前请让源表带缓存值
- 检查子表清理失败会保留 `__delivery_check__` 并记 warning
- 链接可编辑性以"当前授权账号可写"为准；协作者权限管理不在本技能范围
