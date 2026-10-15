---
name: input-preflight-check
description: 材料齐全性与分析前数据范围校验：清单三态核对（已齐/缺失/需确认，按名称与内容模式匹配，不因名称相近误判命中，损坏文件不得因文件名命中而判已齐）与数据表范围校验（必需列/重复表头/单位口径/数值列/真实日历日期范围/最少行数/公式缓存），输出放行或阻断结论。对有跨年度/近N年要求的条目另做业务完整性判定（business_verdict/coverage_state/coverage_warnings，可用 required_years 核对覆盖），关键词层 pass 不得当作完整材料放行。当用户给出材料清单问"材料齐了吗"或要求"分析前先检查数据范围"时使用。校验不通过即阻断，不继续产出分析结论。
---

# 材料齐全性与分析前数据范围校验

## 何时使用

- 清单 + 材料目录 → 三态核对表（已齐/缺失/需确认）
- 数据表 + 分析要求 → 口径/单位/字段/时间范围校验 + 放行或阻断

## 用法

**清单核对模式**：

```
python scripts/preflight.py checklist --checklist c.json --dir materials/ --report r.json
```

checklist.json：

```json
{"items": [
  {"id": "M1", "name": "营业执照", "required": true,
   "patterns": ["营业执照", "统一社会信用代码"], "fuzzy": ["执照"]},
  {"id": "M2", "name": "近三年审计报告", "required": true,
   "patterns": ["审计报告"], "required_years": [2021, 2022, 2023]}
]}
```

- `patterns`：同一文件需**全部命中**（文件名或**可读文件的内容**）才算"已齐"
- `fuzzy`：仅文件名近似命中而内容模式未验证 → `needs_confirm`（防误判）
- 名称相近但内容不符 → 不判命中
- **文件命中模式但无法读取（损坏 docx/xlsx 等）→ 不判 ready**：条目列
  `unreadable_files`（含错误原因），必需项直接 `block`，非必需项 `confirm_needed`
- `required_years`（可选）：跨年度材料的**显式**目标年份清单（4 位年份）。
  逐年在"该项已命中的可读证据文件"中核对：证据文件名须含该年份（独立数字标记）
  才算覆盖，缺年记 `missing_years` 并附来源证据 `evidence`。
- `allow_merged_years`（可选，默认 false）：是否允许**单文件名并列多年份**的合并
  报告作为多年份证据。默认 **false** → 单文件名称并列多年份只算一份，判
  `needs_confirmation`（`merged_years_pending` 列出），需拆分或人工核对。
- **两层判定**：`verdict`/`state` 是关键词匹配层结论（向后兼容保留）；
  `business_verdict`/`coverage_state`/`coverage_warnings` 是业务完整性层结论。
  - 名称含"近N年/最近N年"但**未声明 `required_years`** → 不猜基准年份，
    `coverage_state=needs_confirmation`，必需项 `business_verdict=block`。
  - 声明 `required_years` 但**缺年** → `coverage_state=incomplete`，
    必需项 `business_verdict=block`；三年齐备 → `coverage_state=complete`、`pass`。
  - **配置不自洽不得放行**：`required_years` 含非法年份、重复年份，或与名称
    "近N年"的 N 数量不一致 → `needs_confirmation`（即便存在有效命中年份）。
  - **单文件名称并列多年份 ≠ 多份证据**：默认待确认；仅显式 `allow_merged_years: true`
    才按可核对证据接受。
  - **不得把关键词层 `pass` 当作完整材料放行**：`business_verdict` 才是业务放行依据。
  - 核对的是**按配置的材料覆盖**，**不是**审计内容真实性验证。
  - 不涉及年度的普通清单：`coverage_state=null`，业务层与旧行为一致。
  - 不以"任意年份出现在任意文件"覆盖；不确定时提示人工核验。

**数据范围模式**：

```
python scripts/preflight.py data --data d.xlsx --requirements r.json --report r.json
```

requirements.json：

```json
{"columns": ["日期", "销售额", "地区"], "units": {"销售额": "元"},
 "numeric_columns": ["销售额"], "date_column": "日期",
 "date_range": ["2026-01-01", "2026-12-31"], "min_rows": 1}
```

## 判定（脚本保证）

- 必需项 missing / 必需项仅命中损坏文件 → `verdict: block`（并明确列出缺什么/坏什么）
- **日期列按真实日历日期解析**：不存在的日期（如 `2026-02-30`）与无法解析的值
  （如 `NOT_A_DATE`）计入 `unparseable`，空值计入 `missing`，**均阻断**；
  越界日期默认阻断（`date_range_block: false` 可改为仅记录）
- 数值列（`numeric_columns`）：不可解析（含非有限数）、缺失 → 阻断
- 公式无缓存值（未读取到的范围）→ `formula_cache` 检查项列出坐标并阻断，不静默跳过
- 以下情况**不得静默通过**：
  - 空清单（无条目）/ 清单项缺 id / id 重复 / 条目无 patterns→ `block`
  - 材料目录为空 → 至少 `confirm_needed`（必需项随之 `block`）
  - 数据表头重复（列引用歧义）→ `block`
  - requirements 未配置任何校验项 → `block`
- 单位列与要求不一致 → 记入检查项（`confirm_needed`）
- **业务完整性层**（清单模式）：
  - 必需项含"近N年"但未声明 `required_years` → `business_verdict: block`，
    条目 `coverage_state: needs_confirmation`，`coverage_warnings` 说明无法核对
    （不猜基准年份）
  - 必需项声明 `required_years` 但缺年 → `business_verdict: block`，
    `coverage_state: incomplete`，`missing_years` 列出缺年并保留 `evidence`
  - 配置不自洽（非法/重复年份、与"近N年"数量不一致）→ `business_verdict: block`，
    `coverage_state: needs_confirmation`（即使有有效年份命中）
  - 单文件名并列多年份（未配 `allow_merged_years`）→ `needs_confirmation`，
    `merged_years_pending` 列出；显式允许且证据可核对时才可 `complete`
  - 三年齐备（各自独立证据文件）→ `coverage_state: complete`，业务层可 `pass`
  - `business_verdict` 绝不比关键词层 `verdict` 更宽松；关键词 `pass` 不得当完整材料放行
  - 核对的是按配置的材料覆盖，不是审计内容真实性验证
  - 无年度要求的条目 `coverage_state: null`，业务层与旧行为一致
- 同一输入两次运行结果一致

## 输出（preflight-report.json）

```json
{"mode": "checklist", "verdict": "block|confirm_needed|pass",
 "business_verdict": "block|needs_confirmation|pass",
 "items": [{"id": "M1", "state": "ready|missing|needs_confirm",
            "matched_files": [...], "unreadable_files": [{"file": "...", "error": "..."}],
            "coverage_state": "complete|incomplete|needs_confirmation|null",
            "coverage": {"required_years": ["2021", "2022"],
                         "covered_years": ["2022"], "missing_years": ["2021"],
                         "evidence": {"2022": ["审计报告_2022.md"]}}}],
 "files_scanned": 3, "coverage_warnings": ["跨年度材料不齐：..."],
 "reasons": ["缺失: 近三年审计报告（无文件命中全部模式）"]}
```

`verdict`/`state` 为关键词匹配层（向后兼容）；`business_verdict`/`coverage_state`/
`coverage_warnings` 为业务完整性层。数据模式附 `duplicate_headers`、
`required_columns`、`unit`、`numeric`、`date_range`（`parsed_rows` / `out_of_range` /
`unparseable` / `missing_rows` 计数与行号）、`formula_cache`、`min_rows`、`sheet`、
`rows` 明细。
**verdict=block / business_verdict=block 时不产出任何分析结论**。

## 退出码

- 0：报告已生成（含 verdict=block / confirm_needed）
- 2：材料目录不存在、清单/要求文件不是合法 JSON 对象、数据表无法读取

## 依赖

Python 3.9+，openpyxl、python-docx（内容嗅探用；均为 MIT）。
