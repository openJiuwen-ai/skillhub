---
name: doc-sensitive-redact
description: 文档敏感信息识别与打码交付（身份证/手机号/银行卡/邮箱），支持 DOCX/XLSX/PPTX/PDF 单文件或目录批量。双轨识别（正则形状 + 校验位/Luhn 算法复核）避免误杀；PDF 采用 redaction 底层删除并回提验证不可还原；输出打码清单（类型/位置/计数）。当用户要求"脱敏/打码/去敏感信息后再发出去"时使用。15 位数字与校验位不符的 18 位数字标 needs_review 不误打。
---

# 文档敏感信息识别与打码交付

## 何时使用

- DOCX/XLSX/PPTX/PDF（单文件或整个目录）→ 打码后文件 + 打码清单
- 用户说"把这批文件里的身份证/手机号/银行卡/邮箱打码后交付"

## 用法

```
python scripts/redact.py --input 材料目录_or_单文件 --outdir out/ [--types id_card,phone,bank_card,email] [--report manifest.json]
```

## 识别规则（双轨，脚本保证）

| 类型 | 第一轨（形状） | 第二轨（算法复核） | 处理 |
|---|---|---|---|
| 身份证 | 18 位含出生日期模式 | GB11643 校验位 | 两轨都过 → 打码；形状对校验位错 → `needs_review` 不打 |
| 手机号 | 1[3-9] 开头 11 位 | 号段复核 | 两轨都过 → 打码 |
| 银行卡 | 13-19 位数字 | Luhn | Luhn 过 → 打码；不过 → 视为普通数字不打 |
| 邮箱 | 邮箱正则 | 语法 | 打码 |
| 15 位数字 | 形状接近证件 | 无校验位 | `needs_review` 不打，避免误杀 |

## 处理方式（按格式）

- **DOCX**：段落/表格/页眉页脚内 run 文本替换为 `■`（跨 run 命中完整替换），底层 XML 不再含原文
- **XLSX**：单元格字符串替换为 `■`
- **PPTX**：文本框 run 替换为 `■`
- **PDF**：`search_for` 定位 → `add_redact_annot` → `apply_redactions`（内容流底层删除，非画黑框）→ 保存后**回提验证**：重新提取全文确认原文不再出现，验证结果写入清单

## 输出（outdir/）

- `<原名>_REDACTED.<ext>` 文件
- `redact-manifest.json`：逐文件 {redactions: [{type, count, locations}], needs_review: [{type, count}], pdf_removed_and_verified}
  - 清单**不含**原始敏感值，只有类型/位置/计数

## 失败与降级

- 损坏文件 → 该文件记 failed，批量继续
- PDF 中文字被拆分到多个 span 导致 search 未命中 → 文本仍会进入 needs_review 扫描报告提示人工复核

## 依赖

Python 3.9+，python-docx / openpyxl / python-pptx / PyMuPDF（按输入格式按需安装；PyMuPDF 为 AGPL/商业双许可，作为独立安装依赖使用）。
