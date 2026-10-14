---
name: hr-material-analysis
description: 人力材料分析撰写：薪资表/考勤表/花名册跨表比对（缺勤差异可复算、姓名口径不一致与缺考勤显式标注、重复工号列明细）、按部门汇总名单，并生成 HR 分析简报 docx。输出中身份证/银行卡等敏感字段默认脱敏并记录脱敏清单。当用户要求"比对薪资考勤/汇总员工名单/生成人力分析简报"时使用。不修改源表；汇总剔除合计行。
---

# 人力材料分析撰写

## 何时使用

- payroll.xlsx（工号/姓名/应发/实发…）+ attendance.xlsx（工号/姓名/应出勤/实出勤）+ roster.xlsx（工号/姓名/部门）
- → 人员比对表 + 部门汇总 + HR 简报草稿

## 用法

```
python scripts/hr_compare.py --payroll p.xlsx --attendance a.xlsx --roster r.xlsx --outdir out/ [--no-mask]
```

三表均可选（至少一张）；列名按关键词识别（工号/姓名/部门/应发/实发/应出勤/实出勤/身份证/银行卡）。

## 规则（脚本保证）

- 按**工号**关联；姓名跨表不一致 → 显式标注；考勤缺失 → `缺考勤记录`（不默认满勤）
- 缺勤天数 = 应出勤 − 实出勤（手工可复算）
- 重复工号 → 明细列出，不静默合并
- 汇总（部门人数/应发合计）**剔除合计行**（合计/总计开头的行）
- 输出文件中身份证/银行卡列默认脱敏为 `■`，脱敏清单写入 `mask-manifest.json`（--no-mask 关闭）

## 输出（outdir/）

1. `compare-report.xlsx`：sheet「人员比对」（工号/各表姓名/部门/应出勤/实出勤/缺勤天数/应发/实发/异常标记）+ sheet「重复工号」
2. `roster-summary.xlsx`：部门/人数/应发合计
3. `draft.docx`：HR 分析简报草稿（总人数/总缺勤/异常人员数等由计算值填充）
4. `mask-manifest.json`：脱敏列清单

## 依赖

Python 3.9+，openpyxl、python-docx。
