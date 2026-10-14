---
name: pdf-structured-extract
description: PDF 结构化提取：文字层正文、表格抽取为结构化行列、稀疏文字页渲染为图片交视觉识别、关键数字（身份证/手机号/银行卡/邮箱）两轮交叉校验（正则形状 + 校验位/Luhn 算法）。当用户上传 PDF 要求"提取内容成结构化数据/把表格转成台账/核对 PDF 里的证件号手机号"时使用。本地路径不依赖任何第三方 API Key；扫描页无 OCR 引擎时显式标注 needs_vision 而不是伪造识别结果。
---

# PDF 及扫描表单结构化提取

## 何时使用

- PDF（含文字层）→ 结构化正文 + 表格（JSON/Markdown）
- 需要核对 PDF 中的关键数字（身份证/手机/银行卡/邮箱）
- 扫描或图片型页面 → 渲染成 PNG 交视觉识别（不伪造 OCR）

## 用法

```
python scripts/extract_pdf.py --input file.pdf --outdir out/
```

## 输出（outdir/）

1. `extract.json`：
   - `pages`: 每页文字层全文
   - `tables`: 检测到的表格（页码 + bbox + 结构化行列数据）
   - `sparse_pages`: 文字 < 20 字符的页（渲染为 `pages/page-N.png`，标注 `needs_vision: true`）
2. `extract.md`：人读版（正文 + Markdown 表格）
3. `verify-report.json`：关键数字两轮校验
   - 第一轮：正则形状提取（候选）
   - 第二轮：独立算法校验（身份证校验位 / 手机号段 / 银行卡 Luhn / 邮箱语法）
   - `pass2: valid|invalid|needs_review`；身份证形状对但校验位错 → `invalid`（显式标注，不静默丢弃）；15 位数字 → `needs_review`
   - 校验不一致的条目单独列出 `mismatches`

## 诚实边界（脚本保证）

- 无文字层且未渲染成功的页 → 标 `needs_vision`，不编造内容
- 不调用外部 OCR/云服务；没有 API Key 也能完整走文字层路径
- 加密 PDF：显式报错退出（退出码 2），不暴力破解

## 依赖

Python 3.9+，PyMuPDF（`pip install pymupdf`；AGPL/商业双许可——本包仅通过其公开 API 调用，不复制其源码，不分发其本体）。
