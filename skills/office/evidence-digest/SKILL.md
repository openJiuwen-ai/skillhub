---
name: evidence-digest
description: 多源证据归集与结构化台账：读取材料目录（docx/xlsx/md/txt/图片），产出逐条证据台账（每条含来源文件与页码/段落/单元格定位）、数字口径冲突清单、问题覆盖情况与无证据待核问题列表。当用户给出评价报告、访谈记录、指标表格等多份材料要求"整理证据/支撑结论/来源可追溯"时使用。图片登记为 needs_vision 来源不编造内容；无来源推断标待核不入结论。
---

# 多源证据归集与结构化报告

## 何时使用

- 多份材料（docx/xlsx/md/txt/png 等）+ 待回答问题 → 证据台账 + 冲突清单 + 待核问题
- 用户要求"把这些材料的证据整理出来/每个结论要有出处"

## 用法

```
python scripts/evidence_ledger.py --materials 材料目录 --questions q.md --outdir out/
```

questions.md 每行一个问题。

## 输出（outdir/）

1. `evidence-ledger.json`：逐条证据 {id, text, source_file, location(段落N/Sheet!A1/行N), has_numbers}
2. `evidence-ledger.md`：人读版台账（每条含来源定位）
3. `conflicts.json`：同一指标（标签+单位）在不同材料出现不同数值 → 双记并列，不静默合并
4. `open-questions.json`：无任何证据支撑的问题（标 `no_evidence`，不做推断）

## 规则（脚本保证）

- docx 逐段落登记；xlsx 逐非空单元格登记（Sheet!坐标）；md/txt 逐行登记；图片只登记为 `needs_vision` 来源项，不产生内容
- 证据条目含数字的标记 `has_numbers`；条款级"标签+数值+单位"抽取用于冲突检测
- 覆盖匹配：问题与证据按关键词命中（确定性）；命中清单写入报告供人工复核
- 无来源的推断不进入台账；台账每条都可回溯到文件与定位

## 依赖

Python 3.9+，python-docx、openpyxl。
