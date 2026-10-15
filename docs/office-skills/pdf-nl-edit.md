# pdf-nl-edit 交付说明

按"第 N 页把 X 改成 Y"的自然语言指令，在 PDF 指定页做局部文字替换：底层删除原文 + 同位插入新文本，默认替换该页**全部**命中，输出编辑报告与其余页不变性验证。全程本地、无第三方 API Key。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 指定页（1 基页码）连续文本定位与替换 | 是 |
| 同页多处命中全部替换 | 是：每处先删后插，`occurrences_replaced` 与实际一致（绝不"删 2 插 1 报 2"） |
| 其余页内容不变验证 | 是：逐页提取文本与原文件比对（`other_pages_unchanged`） |
| 保留源文件 | 是：复制到临时文件编辑，成功后原子替换输出；源文件字节不变 |
| 失败不破坏既有输出 | 是：失败时不改动既有输出文件，不留临时文件 |
| 替换文本越页 / 覆盖邻近内容 | 否：明确失败（退出 2）且不产出文件，不静默破坏版面 |
| 输入输出同一路径 | 否：拒绝（退出 2），保护源文件 |
| 不可恢复脱敏 | 否：普通编辑增量保存会保留旧对象版本；`--purge` 可整文件重写但仍非取证级擦除，敏感脱敏请用 `doc-sensitive-redact` |
| 扫描页 / 曲线文字 / 艺术字 | 否：无法定位时不猜不改 |
| 加密 PDF | 否：显式报错退出（退出 2），不暴力破解 |

## 安装与依赖

- Python 3.9+
- 运行时依赖全部宽松许可（**无 PyMuPDF/fitz**）：`pdfplumber`(MIT) + `pdfminer.six`(MIT) 定位文本；`pypdf`(BSD-3-Clause) 结构读写与落盘（`pip install -r requirements.txt`）
- 标准库：argparse / io / json / os / re / sys / tempfile

技能包结构：

```text
pdf-nl-edit/
├── SKILL.md
├── requirements.txt
└── scripts/pdf_edit.py
```

### 实现方式（相对原 PyMuPDF 版）

- 定位：pdfplumber `page.search(find)`（等价 `search_for` 连续文本语义）给出命中矩形与字符框
- 改写：直接对目标页内容流做**算子级手术**——把命中字形码从 `Tj/TJ` 操作数中切除；命中与其它文本共用算子时按字距补偿精确切除（不整段误删）；再在命中原点追加新文本算子
- 字体：复用文档已有字体资源（`helv`/`china-s` 等 CID）；页面缺少合适字体时按需新增（西文 Helvetica / CJK Heiti `UniGB-UTF16-H`，与 PyMuPDF 内建 CJK 同一 Adobe 标准字体机制）
- 落盘：默认 `pypdf` **append-only 会话来写**（真正增量：输出以原文件字节为前缀、仅追加修改对象）；`--purge` 用 `pypdf` 整文件重建，未引用对象（旧内容流）被清除
- 不做整页栅格化、不用白框遮盖；不支持的结构（跨算子命中、无法安全往返重编码的字体）**显式失败退出 2**

## 用法

```bash
python scripts/pdf_edit.py --input in.pdf --page 2 --find 旧标题 --replace 新标题 \
    --output out.pdf --report r.json [--purge]
```

## 输出报告字段（r.json）

- `edits[]`：每处 `{page, before, after, rect:[x0,y0,x1,y1], fontsize}`
- `occurrences_found` / `occurrences_replaced`：命中数与真实替换处数（多命中时两者一致）
- `other_pages_unchanged` / `target_page_contains_new_text` / `target_page_contains_old_text`
- `font_note`：插入字体（helv / china-s）说明
- `mode`：`incremental`（默认，真正 append-only 增量）/ `purge`（`--purge` 整文件重写）/ `full_fallback`（增量未成立时整文件回退）
- `irreversible`：`purge`/`full_fallback` 为 true；`security_note` 说明增量保存不构成敏感原文删除

未命中时报告 `status: not_found` 并退出 1；越页/覆盖保护等参数错误时报告 `status: error` 与 `reason` 并退出 2。

## 退出码

- 0：成功
- 1：目标文本未找到 / 落盘后校验未通过（不产出输出文件）
- 2：输入或参数错误（损坏 / 加密 / 页码越界 / 同输入输出路径 / 替换文本越页 / 覆盖邻近内容 / 写入失败）

## 验收依据与测试

对应基线中"PDF 指定页局部编辑"方向（`decision-register.json`: independent_implement；`specs.json` 的 `pdf-nl-edit`：输入 PDF + 编辑指令，输出 edited.pdf + edit-report.json，异常含页码越界/目标不存在/该编辑 skipped 不产出损坏文件；金标准为对 3 页 PDF 改第 2 页、仅第 2 页变化、其余页文本不变）。

黑盒测试位于 `tests/office_skills/test_pdf_nl_edit.py`（5 项，独立推导预期值，不 mock 实现，不 import 被测模块）：

1. 正常：3 页 PDF 改第 1 页标题 → 退出 0、产物与报告字段正确、新文在旧文不在、未编辑页文本不变、源文件 hash 不变、无残留临时文件
2. 复杂：同页两处 `OLD_LABEL` + `KEEP_THIS`（第 2 页哨兵文本）→ 两处都替换（NEW==2、OLD==0、KEEP 保留、第 2 页不变），报告的 `occurrences_replaced==2`；CJK 同页两处替换；`--purge` 模式结果正确且标记 `irreversible`
3. 异常：目标不存在 → 退出 1、既有输出保留、报告 `not_found`；页码越界 → 退出 2；同输入输出路径 → 退出 2 且源文件不变；损坏/加密 → 退出 2；超长替换越页 / 覆盖同行邻近内容 → 退出 2、既有输出不变、无临时残留

在仓库根目录运行（使用任务 venv 解释器，临时目录为任务目录下唯一目录）：

```bash
<python> -m pytest tests/office_skills/test_pdf_nl_edit.py --basetemp <任务目录下唯一临时目录>
```

家族回归（同技能，兼容旧契约）：`tests/office_skills/test_pdf_ppt_family.py::test_pdf_edit` 通过。

## 修复记录（2026-10-14，相对初版）

主控缺陷探针 `benchmarks/pdf-edit-complex/before-review/controller-results.json` 冻结真实数据丢失：同页两处 `OLD_LABEL` 被全删、只插入一处 `NEW_LABEL`，却退出 0 且报告 `occurrences_replaced=2`。初版 `pdf_edit.py` 对每个命中加 redaction 全部删除，却只在 `rects[0]` 插入一次。修复：对**每个命中**分别规划插入框并逐一插入（`plan_insert` 使用 `get_text_length` 精确量宽），`occurrences_replaced` 反映真实处数；失败改用临时文件 + 原子替换，避免覆盖既有输出；新增同输入输出路径拒绝、越页/覆盖邻近内容保护、`mode`/`irreversible`/`security_note` 字段区分普通编辑与不可恢复脱敏。对保留的原始 `two-occurrences.pdf` 用修复后代码在新目录 `per-skill/pdf-nl-edit/after-fix-run-20261014` 复跑（未覆盖 `before-review`）：NEW==2、OLD==0、KEEP 保留、第 2 页不变、退出 0，与 `expected.json` 一致。

## 迁移记录（2026-10-15，运行时去 PyMuPDF）

发布前将运行时 `pymupdf/fitz`（AGPL/商业双许可）替换为宽松许可栈（`pdfplumber`+`pymupdf`→pypdf 等），**保持既有功能与冻结验收**：

- 定位改用 pdfplumber `search`；改写改为直接对内容流做算子级删除 + 同位插入（新实现，非薄封装）
- 默认落盘改为 pypdf append-only 会话来写（**真正增量**：输出以原文件字节为前缀）；`--purge` 用 pypdf 整文件重建（旧未引用内容流被清除）
- 旧测试 `test_pdf_nl_edit.py`（5 项）与家族 `test_pdf_ppt_family.py::test_pdf_edit` 原样通过，未改任何旧 expected/pass 条件
- 新增独立迁移回归 `tests/office_skills/test_pdf_nl_edit_migration.py`（覆盖真正增量前缀、purge 清除旧对象、字体缺失时新增资源、部分命中字距补偿、跨算子显式失败、静态无 pymupdf/fitz import、用 PyMuPDF oracle 交叉核对）
- 迁移暴露的新隐患（原测试未覆盖）：命中与其它文本共用同一算子时的精确切除与字距补偿；页面缺少目标字体时的资源新增——均已补独立测试

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；未复制外部代码；竞品 doubao/word 的 PDF 方向、workbuddy nano-pdf 均 `no_license_found`，仅功能对标未搬运）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE` 分发。
- 运行时依赖全部宽松许可，**无 PyMuPDF/fitz**（许可证据为安装 wheel 的 METADATA，读取于 2026-10-15）：

| 依赖 | 版本 | 许可 | 角色 |
|---|---|---|---|
| pdfplumber | 0.11.10 | MIT | 直接：文本定位/字符框 |
| pypdf | 6.19.0 | BSD-3-Clause | 直接：结构读写/增量与整文件落盘 |
| pdfminer.six | 20260107 | MIT | 传递（pdfplumber）：PDF 解析 |
| pillow | 12.3.0 | MIT-CMU | 传递（pdfplumber） |
| pypdfium2 | 5.14.0 | BSD-3-Clause OR Apache-2.0（随附 PDFium 及其第三方均宽松：freetype/icu/libjpeg-turbo/libpng/lcms/openjpeg/zlib/abseil/agg/simdutf/fast_float/llvm-libc） | 传递（pdfplumber） |
| charset-normalizer | 3.5.2 | MIT | 传递（pdfminer.six） |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | 传递（pdfminer.six） |
| cffi | 2.1.1 | MIT-0 | 传递（cryptography） |
| pycparser | 3.11 | BSD-3-Clause | 传递（cffi） |

- 未引入 GPL/AGPL/LGPL；无外部进程调用旧 PyMuPDF；未复制/改名受限代码或竞品实现。第三方版权与许可文本按各依赖随附保留。

## 未测项（不宣称通过）

- 豆包端同题竞品验收：`parity_status=not_verified`，未伪造竞品结果。
- `--purge` 的取证级安全擦除：只做整文件重建（清除未引用对象），未做文件系统级擦除验证，故不宣称"安全删除敏感原文"。
