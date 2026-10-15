---
name: pdf-structured-extract
description: PDF 结构化提取：文字层正文、表格抽取为结构化行列、稀疏文字页渲染为图片交视觉识别、关键数字（身份证/手机号/银行卡/邮箱）两轮交叉校验（正则形状 + 校验位/Luhn 算法）。当用户上传 PDF 要求"提取内容成结构化数据/把表格转成台账/核对 PDF 里的证件号手机号"时使用。本地路径不依赖任何第三方 API Key；扫描页无 OCR 引擎时显式标注 needs_vision 而不是伪造识别结果。
display_name: PDF 正文与表格提取
tags: [办公与生产力, PDF, 文档处理]
license: Apache-2.0
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
   - `pages`: 每页文字层全文（每页含 `sparse`/`needs_vision`/`rendered_image`/`tables` 行列）
   - `tables`: 检测到的表格，每条含 `page`（页码）+ `bbox`（页面坐标 [x0,y0,x1,y1]）+ `rows`（结构化行列）+ `row_count`/`col_count`/`source`
   - `uncertain_items` / `key_numbers.needs_review`: 关键数字中长度或形态有歧义的待核条目（如 15 位纯数字）
   - `sparse_pages`: 文字 < 20 字符的页（渲染为 `pages/page-N.png`，标注 `needs_vision: true`）
2. `extract.md`：人读版（正文 + Markdown 表格）
3. `verify-report.json`：关键数字两轮校验
   - 第一轮：正则形状提取（候选）
   - 第二轮：独立算法校验（身份证校验位 / 手机号段 / 银行卡 Luhn / 邮箱语法）——与正则不共享判定
   - `pass2: valid|invalid|needs_review`；身份证形状对但校验位错 → `invalid`（显式标注，不静默丢弃）；15 位纯数字长度有歧义 → `needs_review`
   - `mismatches` 汇总 `invalid`，`needs_review` 单列，`summary` 给出 total/valid/invalid/needs_review 计数；所有值以 `masked` 脱敏输出

## 诚实边界（脚本保证）

- 无文字层且未渲染成功的页 → 标 `needs_vision`，不编造内容
- 不调用外部 OCR/云服务；没有 API Key 也能完整走文字层路径
- 加密 PDF：显式报错退出（退出码 2），不暴力破解
- 15 位等有歧义长度的数字不臆断为卡号或旧身份证，统一进待核清单

## 依赖

Python 3.10+，`pip install -r requirements.txt`（版本已锁定）：

- `pdfplumber`（MIT，含 `pdfminer.six` MIT）——正文与线框表格提取
- `pypdfium2` / PDFium（BSD-3-Clause OR Apache-2.0）——稀疏页真实 PNG 渲染
- `pypdf`（BSD-3-Clause）——结构与加密检查

### 许可（区分原创与运行时依赖）

- 本技能代码/SKILL.md/文档为原创，随本仓库按 `LICENSE`（Apache-2.0）分发。
- 当前验证的 Windows x64 / Python 3.12 运行环境使用宽松许可依赖，不使用 PyMuPDF/fitz。具体版本及官方来源见 `requirements.txt` 与 `DEPENDENCIES.md`，依赖各自的版权、许可与通知要求仍适用；其他平台的安装组合需另行核查。
- 本地运行：不调用联网 OCR、不调用外部进程借用其他 PDF 引擎、不重命名受限代码。第三方版权声明按其原始许可保留（MIT/BSD 的 LICENSE 文本随各依赖包分发）。
