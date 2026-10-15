# input-preflight-check 交付说明

材料齐全性与分析前数据范围校验：`checklist` 模式做清单三态核对（ready/missing/needs_confirm，patterns 需在可读文件的文件名或内容全部命中，损坏文件不得因文件名命中而判已齐）；`data` 模式做分析前数据范围校验（必需列/重复表头/单位口径/数值列/真实日历日期范围/最少行数/公式缓存），输出放行（pass）或阻断（block）结论。校验不通过即阻断，不继续产出分析结论。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 清单三态核对：patterns 全命中（文件名或**可读**内容）→ ready；仅 fuzzy 文件名近似 → needs_confirm；无命中 → missing（必需则阻断） | 是 |
| 文件命中模式但损坏（docx/xlsx 解析失败）→ 条目列 `unreadable_files`（含错误原因），必需项 block，非必需 confirm_needed | 是 |
| 名称相近陷阱：内容不含所需模式 → 不判命中 | 是 |
| 日期列按**真实日历日期**解析（datetime/date 对象与 `2026-01-05`/`2026/1/5`/`2026.1.5` 字符串） | 是 |
| 不可能的日历日期（如 `2026-02-30`）→ 计入 `unparseable`，**阻断** | 是 |
| 无法解析的日期值（如 `NOT_A_DATE`）→ 计入 `unparseable`，**阻断** | 是 |
| 日期缺失（空单元格/空串）→ 计入 `missing_rows`，**阻断** | 是 |
| 日期越界 → 默认阻断（`date_range_block: false` 可改为仅记录） | 是 |
| 数值列（`numeric_columns`）：不可解析（含非有限数/布尔）、缺失 → **阻断** | 是 |
| 公式无缓存值 → `formula_cache` 检查项列出坐标，**阻断**（未读取到的范围不静默通过） | 是 |
| 重复表头 / 空清单 / 清单项缺 id 或 id 重复 / 条目无 patterns / 空 requirements → **阻断** | 是 |
| 材料目录为空 → 至少 confirm_needed（必需项随之 missing→block） | 是 |
| 日期列要求了但表头没有 / date_range 配置无效 → 明确失败检查项 + 阻断（不静默跳过） | 是 |
| 单位口径：表头未注明要求单位且列有数值 → confirm（needs_confirm） | 是 |
| 不存在的材料目录 / 清单或要求文件不是 JSON 对象 / 数据表无法读取 | 退出 2 |
| 语义级"材料真实性"判定（内容真伪、签章效力等） | 否（只做结构与范围校验） |

## 安装与依赖

- Python 3.9+
- openpyxl（MIT）、python-docx（MIT，内容嗅探用）
- 标准库：argparse / datetime / json / math / os / re / sys

技能包结构：

```text
input-preflight-check/
├── SKILL.md
└── scripts/preflight.py
```

## 用法

```bash
# 清单核对模式
python scripts/preflight.py checklist --checklist c.json --dir materials/ --report r.json

# 数据范围模式
python scripts/preflight.py data --data d.xlsx --requirements req.json --report r.json
```

checklist.json：

```json
{"items": [
  {"id": "M1", "name": "营业执照", "required": true,
   "patterns": ["营业执照", "统一社会信用代码"], "fuzzy": ["执照"]}
]}
```

requirements.json：

```json
{"columns": ["日期", "销售额", "地区"], "units": {"销售额": "元"},
 "numeric_columns": ["销售额"], "date_column": "日期",
 "date_range": ["2026-01-01", "2026-12-31"], "min_rows": 1}
```

输出 preflight-report.json：`verdict: pass|confirm_needed|block`；checklist 模式含
`items[].state/matched_files/unreadable_files/fuzzy_files`、`files_scanned`、`reasons`；
data 模式含 `duplicate_headers/required_columns/unit/numeric/date_range/formula_cache/min_rows`
各检查项（日期项含 `parsed_rows/out_of_range/unparseable/missing_rows` 计数与行号）、
`sheet`、`rows`、`reasons`。**verdict=block 时不产出任何分析结论**。

## 退出码

- 0：报告已生成（含 verdict=block / confirm_needed）
- 2：材料目录不存在、清单/要求文件不是合法 JSON 对象、数据表无法读取

## 验收依据与测试

对应 `specs.json` 的 input-preflight-check（清单三态核对+数据范围校验；异常含无命中
missing、近似名不判命中、字段缺失阻断）与 `decision-register.json`: independent_implement。
基线条目 `baseline-matrix.json` rows[1]（豆包"将现有材料与清单、表单、岗位要求或政策
标准逐项核对，找出已齐、缺失和需要确认的内容"目录快照，未读正文，仅功能对标）；
M02 材料齐全性检查 / M17 分析前数据范围校验 / M18 Excel 工作前检查（rely_existing
部分为 xlsx READ，本技能提供显式范围校验交付）。

黑盒测试位于 `tests/office_skills/test_input_preflight_check.py`（14 项，独立推导预期
值，不 mock 实现，不 import 被测模块）：

1. 正常（2）：清单全齐 pass（md 文件名+内容双模式命中、files_scanned、两次运行确定
   性一致）；干净数据表 pass（字符串/真实日期单元格/点分日期混排 parsed_rows=3、单位
   头注明、数值列全可解析）。
2. 复杂（4）：冻结缺陷回归——损坏合同.docx 仅文件名命中 → needs_confirm+unreadable_files，
   必需 block、非必需 confirm_needed，可读"协议.docx"内容模式仍 ready；2026-02-30 /
   NOT_A_DATE / 空日期 / 越界同表 → unparseable_count=2（行2,3）、missing=1（行4）、
   out_of_range=1（行5）、parsed_rows=2、verdict block；重复表头+数值 NOT_A_NUMBER/
   缺失 → duplicate_headers=["金额"] 与数值计数行号阻断；公式无缓存（C2）→ formula_cache
   阻断且其余检查全通过（阻断仅因缺缓存）。
3. 异常（8）：不存在目录退出 2；空清单 block；空目录不静默 pass（可选条目
   confirm_needed、必需条目 block）；重复 id+缺 id block（命中文件本身 ready）；无
   patterns 条目 block；损坏数据文件退出 2；空 requirements block；date_column 不在
   表头 → date_column_not_in_header 阻断。

在仓库根目录运行（任务 venv 解释器，任务目录下唯一临时目录）：

```bash
C:\code\jiuwen\work\skills-comparison-20261013\office-delivery-glm53\venv\Scripts\python.exe -m pytest tests/office_skills/test_input_preflight_check.py --basetemp <任务目录下唯一测试临时目录>
```

家族回归（同技能）：`tests/office_skills/test_business_family.py::test_preflight` 通过
（三态核对/近似名陷阱/缺列阻断/日期越界原有契约保持）。

包校验：仓库原 CLI `jiuwen-teamskills validate skills/office/input-preflight-check`
→ "Skill validation passed."；`pack` 成功产出 zip+sha256。

## 修复记录（2026-10-15，相对初版）

主控冻结探针 `benchmarks/preflight-complex/before-review/controller-results.json`
证实三项真实误判，均已修复并复测：

1. **2026-02-30 通过日期范围检查**：原实现用正则+字符串比较，不校验日历。修复为
   `datetime.date` 真实日历解析，不存在的日期计入 `unparseable` 并阻断。
2. **NOT_A_DATE（parsed_rows=0）仍 pass**：原实现只查越界、不统计不可解析/缺失。
   修复为 `unparseable`/`missing_rows` 计数（含行号与值），任一存在即 ok=false 并
   阻断；越界默认也阻断。
3. **损坏合同.docx 仅文件名匹配就 ready/pass**：原 `sniff_content` 吞异常返回空串，
   文件名含"合同"即命中。修复为逐文件可读性跟踪：命中模式但不可读 → 不判 ready，
   `unreadable_files`（含错误原因），必需项 block。

规则要求的静默通过缺口一并封堵：空清单/空目录/重复 id/缺 id/无 patterns 条目/重复
表头/公式无缓存/空 requirements/日期列缺失或配置无效。新增 `numeric_columns` 数值
范围校验（不可解析/缺失/非有限数阻断）。冻结复测在
`benchmarks/preflight-complex/after-fix-20261015/`（新目录，未覆盖 before-review 历史
证据），`passed=true`（脚本 probe_rerun.py 可独立复跑）。

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；未复制外部代码；
基线条目仅目录快照对标，未搬运任何竞品代码）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE`（Apache License 2.0）分发。
- 运行时依赖 python-docx 1.2.0（MIT）、openpyxl 3.1.5（MIT），已登记于
  `license-register.json` runtime_dependencies。任务中安装于任务 venv，未全局安装。

## 未测项（不宣称通过）

- 豆包端同题竞品验收：`parity_status=not_verified`，未伪造竞品结果（基线仅为目录
  快照，未读竞品正文）。
- 语义级材料真实性/内容真伪判定：本技能只做文件可读性、模式与数据范围的结构化
  校验，不判定材料内容真实有效。
- xlsx 多工作表：data 模式校验 active 工作表（报告标 `sheet` 名），非 active 表不
  在校验范围。
- 未知扩展名（pdf/doc/图片等）文件：不做内容提取（不判损坏），仅可按文件名命中
  patterns；内容模式要求对这类文件无法验证时会如实 missing/needs_confirm。
