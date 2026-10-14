---
name: docx-review-redline
description: 对 .docx 应用结构化审校计划，产出含原生批注（comments.xml + commentReference）与修订（w:ins/w:del）的审校稿，供接收方在 Word 中逐条接受/拒绝。当用户要求"审校这份文档并给批注/修订/红线稿/track changes"时使用。输入是文档+编辑计划（查找文本、替换文本、审校意见），输出 _REVIEWED.docx 与意见定位对照表。不用于模板占位填充（用 word-template-editor），不做两版对比（用 docx-version-diff）。
---

# Word 审校批注与修订交付

## 何时使用

- 审校 .docx：按编辑计划把修改写成**修订**（接收方可接受/拒绝），并挂**批注**说明理由
- 用户明确要 track changes、红线稿、带意见的审校版

## 工作流程

1. 与用户确认审校点，整理成编辑计划 plan.json：

```json
{"operations": [
  {"op": "replace", "find": "旧文本", "replace": "新文本", "comment": "修改理由"},
  {"op": "comment", "find": "目标文本", "comment": "意见"}
]}
```

2. 执行：

```
python scripts/apply_review.py --input draft.docx --plan plan.json --output draft_REVIEWED.docx --report review-report.json
```

3. 交付 `_REVIEWED.docx` + review-report.json（每个操作：定位段落序号/状态 applied|skipped/意见）。skipped 项明确告知用户"未找到目标文本"，不要谎称全部处理。
4. 提醒接收方在 Word 中开启"修订"显示，逐条接受/拒绝。

## 实现要点（脚本保证）

- `find` 支持跨多个 run 的文本（Word 常把文本拆 run），按**文档顺序**（含表格内段落）定位，不受表格混排影响
- 替换 = 原文本移入 `w:del`（保留原格式）+ 新文本放入 `w:ins`（继承原 run 字符格式）
- 批注 = `word/comments.xml` + `commentRangeStart/End` + `commentReference` + `commentsExtended`，在 Word 中可见可回复
- 相同 `find` 出现多次时按 `occurrence`（默认第 1 处，1 基）选择
- 给定结构化编辑计划即可运行，不调用外部付费模型

## 失败与降级

- 找不到目标文本：该操作记 `skipped` 继续其余，退出码仍 0
- 输入损坏/不是 docx：退出码 2，不产出文件
- 空计划：原样复制输出并报告 0 操作

## 依赖

Python 3.9+，python-docx（`pip install python-docx`）。
