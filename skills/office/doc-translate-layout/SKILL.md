---
name: doc-translate-layout
description: 文档翻译并保持版式：从 docx/pptx 抽取段落级待译工作表（含样式与位置），回填译文时保留段落样式/表格结构/页眉页脚，术语表校验命中译法，超长译文自动降字号回退并记录清单，未翻译项显式列出。当用户要求"翻译这份 Word/PPT 并保持格式/版式不变"时使用。译文由调用方（宿主模型或人工）提供，本技能保证结构保持的确定性与可验证；不冒充机器翻译质量（无 MT 引擎时翻译质量标 not_verified）。
---

# 文档翻译并保持版式

## 何时使用

- docx/pptx + 目标语言 → 版式不变的译文文档
- 用户要求"翻译并保持格式/别动排版"

## 用法（两步工作流）

```
# 第一步: 抽取待译工作表
python scripts/translate_layout.py extract --input source.docx --worksheet ws.json

# (调用方完成翻译: ws.json 中每项 {"id": "...", "translation": "译文"})

# 第二步: 回填译文（保持版式）
python scripts/translate_layout.py apply --input source.docx --worksheet ws.json \
    --output translated.docx --glossary terms.json --report report.json
```

- pptx 同理（按扩展名自动识别）
- 术语表 terms.json：`{"智慧园区": "Smart Park"}` —— 源文含术语时校验译文是否使用指定译法，未命中记入 `glossary_misses`

## 保证与输出

- **结构保持**：替换仅发生在文本 run 层——段落样式、表格结构、分节、页眉页脚框架不动；第一 run 的字符格式继承到译文
- **超长回退**：译文长度 > 2× 原文（默认阈值，可调）时，run 有显式字号的降 1pt（最低 9pt）并记入 `fallback` 清单；继承字号的记录为 needs_attention
- **未翻译清单**：工作表缺某段译文 → 该段保留原文并列出，不静默
- **诚实边界**：`mt_engine: "external"`——译文质量不在本技能验证范围内（无 MT 引擎时翻译质量为 not_verified），报告只保证版式与流程确定性

## 失败与降级

- 输入损坏：退出码 2
- 工作表 id 对不上：skipped 列出
- pptx 图形/图片内文字不可抽取：报告 `not_extractable` 清单

## 依赖

Python 3.9+，python-docx / python-pptx（按格式安装）。
