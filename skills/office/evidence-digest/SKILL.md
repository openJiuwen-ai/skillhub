---
name: evidence-digest
description: 多源证据归集与结构化台账：读取材料目录（docx/xlsx/csv/md/txt/图片），产出逐条证据台账（每条含来源文件与页码/段落/单元格定位）、同口径数字冲突清单（保留期间/主体/单位）、跨口径待核清单与问题覆盖候选列表。当用户给出评价报告、访谈记录、指标表格等多份材料要求"整理证据/支撑结论/来源可追溯"时使用。读取失败/未知类型/公式缺缓存单列，绝不计入台账与统计；图片登记为 needs_vision 来源不编造内容；关键词命中仅为检索候选而非已证实答案；无来源推断不入结论。
---

# 多源证据归集与结构化报告

## 何时使用

- 多份材料（docx/xlsx/csv/md/txt/png 等）+ 待回答问题 → 证据台账 + 冲突清单 + 待核问题
- 用户要求"把这些材料的证据整理出来/每个结论要有出处"

## 用法

```
python scripts/evidence_ledger.py --materials 材料目录 --questions q.md --outdir out/
```

questions.md/txt 每行一个问题。

## 输出（outdir/）

1. `evidence-ledger.json`：逐条证据 {id, text, source_file, location(段落N/Sheet!A1/行N), type, has_numbers, confidence:"sourced"}
2. `evidence-ledger.md`：人读版台账（含冲突/跨口径/读取失败/未知类型/公式缺缓存/图片/问题覆盖各节）
3. `conflicts.json`：同口径（期间+主体+单位一致或未声明分量兼容）不同数值 → 双记并列，不静默合并；期间/主体口径不同的并列值单列 `cross_caliber_review`，不判为同口径冲突
4. `open-questions.json`：无任何关键词命中的问题（`no_evidence`，不做推断）

## 规则（脚本保证）

- docx 逐段落/表格单元格登记；xlsx 逐非空单元格登记（Sheet!坐标，行首文本列作为标签前缀）；csv 逐行登记；md/txt 逐行登记；图片只登记为 `needs_vision` 来源项，不产生内容
- 证据条目含数字的标记 `has_numbers`；条款级"标签+数值+单位"抽取用于冲突检测
- **读取失败单列**：损坏文件记入 `unreadable_files`（含错误原因），绝不进入台账、覆盖匹配或数字统计，也不能借错误信息中出现的文件名让相关问题"有证据"
- **公式缺缓存单列**：xlsx 公式单元格无缓存值记入 `formula_cells_without_cache`，不编造数值
- **未知类型单列**：不支持的扩展名记入 `unsupported_files`，不静默跳过；空材料目录记入 warnings
- **覆盖匹配**：问题与证据按关键词命中，命中仅是检索候选（`candidates_need_review`），需人工核对原文，不构成已证实答案；读取失败/未知类型/公式缺缓存/图片不参与匹配
- 无来源的推断不进入台账；台账每条都可回溯到文件与定位

## 依赖

Python 3.9+，python-docx、openpyxl。
