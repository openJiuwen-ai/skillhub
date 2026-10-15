---
name: docx-version-diff
description: 对同一 Word 文档的两个版本 .docx 做结构级对比，输出段落/表格单元格/页眉页脚/分节的变更清单（新增/删除/修改+定位）与变更报告 docx。当用户给出 v1/v2 两版文档问"改了什么/两版差异/对比这两份合同"时使用。不做批注修订（用 docx-review-redline），不读 PDF。
display_name: Word 版本差异对比
tags: [办公与生产力, 文档处理]
license: Apache-2.0
---

# Word 文档版本结构级对比

## 何时使用

- 同一文档两个版本 .docx → 变更清单（JSON）+ 变更报告（docx）
- 用户需要知道"哪些段落改了、表格哪个单元格变了、页眉页脚/分节是否变化"

## 用法

```
python scripts/docx_diff.py --old v1.docx --new v2.docx --report diff.json [--changes-doc changes.docx]
```

## 输出 diff.json

```json
{
  "summary": {"paragraph_modified": 1, "paragraph_added": 0, "paragraph_removed": 0,
              "table_cell_modified": 0, "header_footer_changed": 0, "section_changed": 0},
  "changes": [
    {"type": "paragraph", "change": "modified", "location": "body paragraph 3",
     "before": "旧文本", "after": "新文本"}
  ]
}
```

- 段落按文本序列对齐（difflib），modified/added/removed 带原始定位
- 表格按索引配对，输出单元格级差异（表N 行R 列C，旧值→新值）；行列数变化记 structural
- 页眉页脚按节比较文本（计入 header_footer_changed）；分节变化（节数、页面尺寸、四边页边距，计入 section_changed）单独列出
- 相同文件 → 空变更集，退出码 0

## 失败与降级

- 任一文件损坏/非 docx：退出码 2
- 表格数量不一致：多出的表记 added/removed，可配对的继续单元格比对

## 依赖

Python 3.9+，python-docx（`pip install python-docx`）。
