---
name: input-preflight-check
description: 材料齐全性与分析前数据范围校验：清单三态核对（已齐/缺失/需确认，按名称与内容模式匹配，不因名称相近误判命中，损坏文件不得因文件名命中而判已齐）与数据表范围校验（必需列/重复表头/单位口径/数值列/真实日历日期范围/最少行数/公式缓存），输出放行或阻断结论。当用户给出材料清单问"材料齐了吗"或要求"分析前先检查数据范围"时使用。校验不通过即阻断，不继续产出分析结论。
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
  {"id": "M2", "name": "近三年审计报告", "required": true, "patterns": ["审计报告"]}
]}
```

- `patterns`：同一文件需**全部命中**（文件名或**可读文件的内容**）才算"已齐"
- `fuzzy`：仅文件名近似命中而内容模式未验证 → `needs_confirm`（防误判）
- 名称相近但内容不符 → 不判命中
- **文件命中模式但无法读取（损坏 docx/xlsx 等）→ 不判 ready**：条目列
  `unreadable_files`（含错误原因），必需项直接 `block`，非必需项 `confirm_needed`

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
- 同一输入两次运行结果一致

## 输出（preflight-report.json）

```json
{"mode": "checklist", "verdict": "block|confirm_needed|pass",
 "items": [{"id": "M1", "state": "ready|missing|needs_confirm",
            "matched_files": [...], "unreadable_files": [{"file": "...", "error": "..."}]}],
 "files_scanned": 3, "reasons": ["缺失: 近三年审计报告（无文件命中全部模式）"]}
```

数据模式附 `duplicate_headers`、`required_columns`、`unit`、`numeric`、`date_range`
（`parsed_rows` / `out_of_range` / `unparseable` / `missing_rows` 计数与行号）、
`formula_cache`、`min_rows`、`sheet`、`rows` 明细。
**verdict=block 时不产出任何分析结论**。

## 退出码

- 0：报告已生成（含 verdict=block / confirm_needed）
- 2：材料目录不存在、清单/要求文件不是合法 JSON 对象、数据表无法读取

## 依赖

Python 3.9+，openpyxl、python-docx（内容嗅探用；均为 MIT）。
