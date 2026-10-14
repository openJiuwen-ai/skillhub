# pdf-structured-extract 交付说明

把 PDF（文字层或扫描页）转成可录入的结构化台账：正文、结构化表格（带页码与 bbox）、稀疏/扫描页渲染交视觉识别，并对关键数字（身份证/手机号/银行卡/邮箱）做两轮交叉校验。全程本地、无第三方 API Key；无 OCR 引擎时显式标注 `needs_vision` 而不伪造识别结果。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 文字层正文提取（逐页全文） | 是 |
| 表格抽取为结构化行列 | 是：`find_tables` 线框检测，输出 `page` + `bbox` + `rows` + `row_count`/`col_count` |
| 稀疏文字页（< 20 字符）渲染 | 是：渲染 `pages/page-N.png`，标 `needs_vision: true`，交调用方视觉识别 |
| 关键数字两轮交叉校验 | 是：round1 正则形状候选 + round2 独立算法（身份证 GB11643 校验位 / 手机号段 / 银行卡 Luhn / 邮箱语法） |
| 校验不一致显式标注 | 是：`pass2=invalid` 进 `mismatches`，不静默丢弃 |
| 15 位纯数字等长度歧义 | 是：`pass2=needs_review`，单列待核，不臆断为卡号或旧证件 |
| 关键数字输出脱敏 | 是：verify-report 与 key_numbers 仅保留 `masked`，不含原始值 |
| 无文字层且未渲染成功的页 | 标 `needs_vision`，不编造内容 |
| 外部 OCR / 云服务 | 否：不调用；没有 API Key 也能走完文字层路径 |
| 加密 PDF | 否：显式报错退出（退出码 2），不暴力破解 |
| 无边框表格 | 可能漏检：`find_tables` 依赖线框，需结合正文核对（脚本 notes 已声明） |

## 安装与依赖

- Python 3.9+
- PyMuPDF（`pip install pymupdf`）
- 标准库：argparse / json / os / re / sys

技能包结构：

```text
pdf-structured-extract/
├── SKILL.md
└── scripts/extract_pdf.py
```

## 用法

```bash
python scripts/extract_pdf.py --input file.pdf --outdir out/
```

## 输出（outdir/）

- `extract.json`
  - `pages[]`：每页 `text` / `sparse` / `needs_vision` / `rendered_image` / `tables`（行列兼容视图）
  - `tables[]`：每张表 `page` + `bbox`（[x0,y0,x1,y1]）+ `rows` + `row_count` / `col_count` / `source`
  - `key_numbers`：`candidates_and_verification` / `valid` / `mismatches` / `needs_review`
  - `uncertain_items`：待核条目（长度/形态有歧义）
  - `sparse_pages`：稀疏页页码列表
- `extract.md`：人读版（正文 + Markdown 表格）
- `verify-report.json`：两轮校验报告——`round1.candidates`、`entries[]`（含 `pass2: valid|invalid|needs_review`）、`valid` / `mismatches` / `needs_review` / `summary`
- `pages/page-N.png`：稀疏页渲染图（仅当存在稀疏页）

## 退出码

- 0：完成
- 2：输入错误（损坏 / 加密 PDF）

## 验收依据与测试

对应基线 M21（图片 OCR 验证：不确定字符/数字标待核、关键数字两轮交叉校验、校验不一致显式标注）与 M26（PDF 提取验证：表格结构化非纯文本流、稀疏文字页自动渲染并标注交视觉识别、无第三方 Key 仍可完成文字层路径并显式说明）。黑盒测试位于 `tests/office_skills/test_pdf_structured_extract.py`（3 项，独立推导预期值，不 mock 实现）：

1. 正常：文字层 PDF + 线框表格 → 三件产物齐全、`tables` 带 page+bbox+结构化 rows、`extract.md` 含管道表格、verify-report 空计数
2. 复杂：3 页 PDF（表格 + 正确/错误校验位证件 + 电话 + 有效卡号 + 邮箱 + 15 位歧义数 + 稀疏页）→ 稀疏页渲染、`pass2` 分别 valid/invalid/needs_review、坏校验位进 `mismatches`、15 位进待核、verify-report/key_numbers 脱敏含 `***`、两次运行逐字节一致
3. 异常：损坏 PDF → 非零退出且无成功产物；加密 PDF → 退出 2 且不产出提取结果

在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_pdf_structured_extract.py --basetemp <任务目录下唯一临时目录>
```

家族回归（同技能，兼容旧契约）：`tests/office_skills/test_pdf_ppt_family.py::test_extract` 通过。

修复记录（2026-10-14，相对初版）：主控缺陷探针 `benchmarks/pdf-extract-complex/before-review` 冻结三例（table/scan/corrupt），指出初版 (a) SKILL.md 承诺的 `verify-report.json` 未生成；(b) 表格只输出行列、缺 promised 的页码与 bbox；(c) `pass2: valid|invalid|needs_review` 与"15 位数字 → needs_review"未实现。修复：`extract_table_blocks()` 输出含 `page`+`bbox` 的顶层 `tables`（`pages[].tables` 保留行列视图以向后兼容）；新增 `build_verify_report()` 生成 `verify-report.json`，统一 `pass2` 语义，新增 15 位纯数字 `ambiguous_15digit` 待核；不再靠删承诺缩减能力。对保留的原始 `table.pdf`/`scan.pdf` 用修复后代码复跑（写入新目录 `per-skill/pdf-structured-extract/after-fix-run-20261014`，未覆盖 `before-review`）：table 检出 1 张表带 bbox、scan 稀疏页渲染 + verify-report 生成、corrupt 退出 2 无产物，与 `expected.json` 一致。

未测项（不宣称通过）：无边框/复杂合并单元格表格的检出率（未系统取样）；扫描页 OCR 内容识别（本技能不调用 OCR，只渲染并标注，识别由调用方视觉模型完成）；豆包端同题竞品验收（`parity_status=not_verified`，未伪造）。

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；无外部代码复制；竞品 doubao/word、workbuddy wps-office-suite、nano-pdf、pdf-image-text-extractor 均 `no_license_found`，仅功能对标未搬运）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE`（Apache-2.0）分发。
- 运行时依赖 **PyMuPDF 1.28.2 为 AGPL-3.0 或商业许可双许可**（`license-register.json` 已登记）。本包仅通过其公开 API 调用、不复制源码、不打包其本体，但这**不免除宿主遵守 AGPL 或购买商业授权**的义务——不是"全链路宽松许可"。若需宽松许可可替换为其他 PDF 库。任务中 PyMuPDF 安装于任务 venv，未全局安装。
