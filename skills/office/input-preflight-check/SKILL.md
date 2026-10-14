---
name: input-preflight-check
description: 材料齐全性与分析前数据范围校验：清单三态核对（已齐/缺失/需确认，按名称与内容模式匹配，不因名称相近误判命中）与数据表范围校验（必需列/单位口径/时间范围/最少行数），输出放行或阻断结论。当用户给出材料清单问"材料齐了吗"或要求"分析前先检查数据范围"时使用。校验不通过即阻断，不继续产出分析结论。
---

# 材料齐全性与分析前数据范围校验

## 何时使用

- 清单 + 材料目录 → 三态核对表（已齐/缺失/需确认）
- 数据表 + 分析要求 → 口径/单位/字段/时间范围校验 + 放行或阻断

## 用法

**清单核对模式**：

```
python scripts/preflight.py checklist --checklist c.json --dir materials/
```

checklist.json：

```json
{"items": [
  {"id": "M1", "name": "营业执照", "required": true,
   "patterns": ["营业执照", "统一社会信用代码"], "fuzzy": ["执照"]},
  {"id": "M2", "name": "近三年审计报告", "required": true, "patterns": ["审计报告"]}
]}
```

- `patterns`：同一文件需**全部命中**（文件名或内容）才算"已齐"
- `fuzzy`：仅文件名近似命中而内容模式未验证 → `needs_confirm`（防误判）
- 名称相近但内容不符 → 不判命中

**数据范围模式**：

```
python scripts/preflight.py data --data d.xlsx --requirements r.json
```

requirements.json：

```json
{"columns": ["日期", "销售额", "地区"], "units": {"销售额": "元"},
 "date_column": "日期", "date_range": ["2026-01-01", "2026-12-31"], "min_rows": 1}
```

## 判定（脚本保证）

- 必需项 missing / 必需列缺失 / 有效行数 < min_rows → `verdict: block`（并明确列出缺什么）
- 单位列与要求不一致、日期超出范围 → 记入检查项（`needs_confirm` 或 block，按配置）
- 同一输入两次运行结果一致

## 输出（preflight-report.json）

```json
{"mode": "checklist", "verdict": "block|confirm_needed|pass",
 "items": [{"id": "M1", "state": "ready|missing|needs_confirm", "matched_files": [...]}],
 "reasons": ["缺失: 近三年审计报告（无文件命中全部模式）"]}
```

数据模式附 `columns_check`、`units_check`、`date_range_check`、`rows` 明细。**verdict=block 时不产出任何分析结论**。

## 依赖

Python 3.9+，openpyxl、python-docx（内容嗅探用）。
