# doc-sensitive-redact 交付说明

对 DOCX/XLSX/PPTX/PDF 中的身份证/手机号/银行卡/邮箱做识别与打码交付：双轨识别避免误杀，处理范围覆盖**实际可访问的隐藏部件**（删除修订、批注、数值单元格、PPT 备注、docProps、PDF 元数据/XMP/书签/批注/链接目标/表单域值/附件），每个输出做底层回读验证，无法清洗的残留被显式阻断而不是笼统成功。全程本地、无第三方 API Key。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 正文/表格/页眉页脚打码（跨 run 完整替换） | 是 |
| Word 删除修订（`w:delText`）、批注（含作者属性）、脚注尾注 | 是：包内 XML 部件级清洗 |
| Excel 数值型手机号/证件号、单元格批注（文本+作者） | 是：数值单元格转为打码文本；批注在 `xl/comments` 层清洗 |
| Excel 线程批注（openpyxl 会丢弃） | 是：清洗后回填原包（`threaded_comments_preserved` 计数） |
| PPT 备注、幻灯片/版式/母版/表格/组合形状文本、域文本 | 是：`a:p` 段落级清洗 |
| docProps（创建者/最后修改者等）与 rels | 是：文本节点与属性级清洗 |
| PDF 页面文本底层删除（非画黑框）+ 回提验证 | 是：`apply_redactions` + 保存后回读 |
| PDF 元数据 / XMP / 书签标题 / 批注信息 / 链接目标（mailto/URI 等字符串字段）/ 表单域值 | 是：逐一清洗并纳入回读验证；无关正常链接保留可用 |
| PDF 文件附件批注（含压缩 Office 附件） | 是：zip/文本视图逐条目重写并保留无关内容；无法可靠重写（二进制明文/加密/超深嵌套）→ 整文件标记 failed、删除输出、退出 2（显式阻断） |
| PDF 文档级附件含敏感值 | 阻断式处理：移除该附件并在清单计数（`removed_embedded_files`） |
| 二进制部件（图片/嵌入对象）内明文敏感值 | 否：无法安全清洗 → 整文件标记 failed、删除输出、退出 2（显式阻断，不笼统成功） |
| 目录批量、嵌套子目录同名文件 | 是：按相对路径展平命名（`a__report_REDACTED.docx`），冲突显式失败不覆盖 |
| 混合批处理部分失败 | 退出 2 + 清单逐文件记录；不再以 0 掩盖 |
| 15 位数字 / 校验位不符 18 位号 | 不误打：保留原文，`needs_review` 提示 |
| 图片/扫描件像素文字、嵌入对象内部 | 否：不做底层识别，清单标记 needs_review；PDF 含表单/图像页时 `pdf_removed_and_verified=false` 并附 `unverified_surfaces`，需人工复核 |
| 与豆包竞品同题验收 | 未执行（`parity_status=not_verified`），不伪造竞品结果 |

## 安装与依赖

- 实测栈：Python 3.12.9（2026-10-15 任务 venv 实测，依据 `inputs/runtime-dependency-metadata.json`）
- 依赖最低版本：PyMuPDF 1.28.2 要求 Python >=3.10；lxml >=3.8、openpyxl >=3.8。更旧的依赖版本组合未验证、不作承诺
- lxml（DOCX/PPTX 底层 XML 处理，BSD-3-Clause）
- openpyxl（XLSX，MIT）
- PyMuPDF（PDF，AGPL-3.0/商业双许可）

技能包结构：

```text
doc-sensitive-redact/
├── SKILL.md
└── scripts/redact.py
```

## 用法

```bash
python scripts/redact.py --input 材料目录_or_单文件 --outdir out/ \
    [--types id_card,phone,bank_card,email] [--report manifest.json]
```

退出码：0=全部完成且通过回读验证（可含 needs_review）；2=输入错误或任一文件失败/被阻断。

## 输出（outdir/）

- `<原名>_REDACTED.<ext>`；目录批量按相对路径展平命名，`子目录\名.docx` → `子目录__名_REDACTED.docx`
- `redact-manifest.json`：逐文件 `{redactions:[{type,count,locations}], needs_review:[{type,count}], pdf_removed_and_verified, unverified_surfaces, removed_embedded_files, dropped_unmodeled_parts, threaded_comments_preserved, failed, error}`；位置精确到部件+段落/单元格/页；清单不含任何原始敏感值

## 修复记录（2026-10-15，相对初版）

主控冻结探针 `benchmarks/redaction-hidden/before-review/controller-results.json` 证实初版真实脱敏失败：退出 0 但 Word 删除修订与批注仍留邮箱、Excel 数值型手机号与批注仍留原值、PPT 备注仍留邮箱。初版只扫可见正文（python-docx 段落直接 run、openpyxl 字符串单元格、python-pptx 文本框），且部分失败退出 0、目录重名输出互相覆盖。重写为包级处理：

- DOCX/PPTX 改为直接在包内全部 XML 部件上做三级清洗（段落 run 级含 `w:delText`/`a:fld`、文本节点级、属性级），未改动部件按原始字节复制；
- XLSX 增加数值单元格、批注、文档属性、线程批注保留与 openpyxl 丢弃部件的如实清单（`dropped_unmodeled_parts`）；
- PDF 增加元数据/XMP/书签/批注信息清洗与含敏感值附件移除；
- 新增保存后**底层回读验证**（逐部件搜索已识别值，二进制按原始字节，PDF 含全部隐藏面），残留即阻断（failed+删输出+退出 2）；
- 输出命名按相对路径展平、冲突显式失败；任一文件失败即退出 2；实现 SKILL.md 此前承诺但未实现的 `--types`。

对冻结输入复制件复跑（`per-skill/doc-sensitive-redact/benchmark-rerun-20261015/`，未覆盖 `before-review`）：exit 0、3/3 文件处理、6 个 needle 逐部件扫描全部为零残留，与 `expected.json` 规则一致。

## 修复记录（2026-10-15 第二轮：PDF 侧通道）

主控冻结探针 `benchmarks/redaction-pdf-sidechannels/initial/controller-results.json` 证实上一轮存在漏检：PDF 中 mailto 链接目标仍含 `link@example.test`、文件附件批注中压缩 DOCX 仍含 `attachment@example.test`，但退出 0、needs_review 为空且 `pdf_removed_and_verified=true`。根因：`process_pdf`/`_pdf_scopes` 均不覆盖链接目标与附件批注内容（压缩条目对 latin-1 视图不可见），回读只搜索此前已识别的值集合。修复（仅本技能）：

- 链接目标（URI/启动文件/name/nameddest 字符串字段）纳入清洗（`update_link` 重写，打码后无关正常链接原样保留）与回读；link-only 独立冻结样本（仅邮箱链接+正常 https 链接）验证通过；
- 文件附件批注内容按 zip（含嵌套 zip，深度受限）/utf-8/带 BOM utf-16 文本视图逐条目重写并保留无关内容（`update_file`）；无法可靠重写的二进制明文、加密或超深嵌套附件抛 `UncleanableAttachment` 显式阻断整份交付（failed+删输出+退出 2），不笼统成功；
- 表单域值纳入清洗与回读；文档级附件的敏感检测升级为深层视图（压缩 Office 附件不再漏检）；
- 回读验证改为对全部启用类型在新表面上重新识别（链接目标/表单域值/附件 zip 递归展开内容），不再仅搜索此前已识别值集合；
- `pdf_removed_and_verified` 不再无条件为 true：存在表单或含图像页等未核验表面时为 false，并附 `unverified_surfaces` 与 needs_review 标记（`form_fields_present_needs_manual_review` / `images_present_not_ocr_scanned`），与承诺不冲突；
- 标准黑盒测试从 12 项增至 16 项，新增：链接目标清洗且无关链接保留、压缩 DOCX 附件批注清洗且无关内容保留、不可重写二进制附件显式阻断、表单/图像页验证限定。

冻结 `verify_fixed.py` 复测（新目录留证，未覆盖 initial）：复合样本（mailto 链接+压缩 DOCX 附件）7 项检查全部通过（sanitized、无泄漏、正文保留）；redaction-hidden 冻结回归 7 项检查全部通过。

## 验收依据与测试

对应 `decision-register.json`: independent_implement；`specs.json` 的 `doc-sensitive-redact`（输入文件或目录，输出 `_REDACTED` 文件 + 清单；异常含 15 位/校验位不符不误杀、批量部分失败记录；金标准为四格式敏感值打码后不可从底层回提、清单计数=实际修改）。

黑盒测试位于 `tests/office_skills/test_doc_sensitive_redact.py`（16 项，独立推导预期值，不 mock 实现，不 import 被测模块；识别期望值用测试内独立实现的 GB11643 校验位与 Luhn 算法构造）：

1. 正常（2 项）：DOCX 正文/表格/页眉 + XLSX 字符串 + PPTX 文本框打码、清单计数与读回验证
2. 复杂（10 项）：冻结缺陷场景复现（删除修订/批注/数值单元格/备注，底层 ZIP 逐部件扫描零残留、清单含 `word/comments.xml`/`notesSlide` 定位）；needs_review 不误打；嵌套目录唯一命名；outdir 嵌入输入目录不递归重扫；`--types` 过滤；重名冲突显式失败；PDF 隐藏面（元数据/XMP/附件/批注/书签+原始字节）清洗；PDF 链接目标清洗且无关正常链接保留；压缩 DOCX 附件批注清洗且无关内容保留；表单域值清洗+表单/图像页 `pdf_removed_and_verified=false` 限定
3. 异常（4 项）：不可重写二进制附件显式阻断（failed、输出删除、错误含 UncleanableAttachment）；混合批处理部分失败退出 2 且逐文件记录；二进制部件明文残留被阻断（failed、输出删除、错误含部件名）；输入不存在/不支持的扩展名/空目录/非法 `--types` 退出 2

在仓库根目录运行（使用任务 venv 解释器，临时目录为任务目录下唯一目录）：

```bash
<python> -m pytest tests/office_skills/test_doc_sensitive_redact.py --basetemp <任务目录下唯一临时目录>
```

家族回归（同技能，旧契约）：`tests/office_skills/test_pdf_ppt_family.py::test_redact` 通过。仓库 CLI 校验：`(cwd=cli) python -m openjiuwen_plugin.main validate skills/office/doc-sensitive-redact` → Plugin validation passed；`pack` 成功。

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；未复制外部代码；无 1:1 豆包竞品项，workbuddy 无对应方向可搬运）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE` 分发。
- 运行时依赖：lxml（BSD-3-Clause）、openpyxl（MIT）、**PyMuPDF 1.28.2 为 AGPL-3.0 或商业许可双许可**——本包仅通过其公开 API 调用、不复制源码、不打包其本体，但**不免除宿主遵守 AGPL 或购买商业授权**的义务。任务中安装于任务 venv，未全局安装。
- 初版曾声明 python-docx/python-pptx 为运行时依赖，重写后 DOCX/PPTX 走 lxml 包级处理，二者不再是本技能的直接依赖（Excel/PDF 依赖不变）。

## 未测项（不宣称通过）

- 豆包端同题竞品验收：`parity_status=not_verified`，未伪造竞品结果。
- WPS/Word 桌面端对重写后 DOCX/PPTX/XLSX 的打开复验：未执行（结构合法性已由 python-docx/openpyxl/python-pptx 读回间接验证，不宣称原生办公引擎验收通过）。
- 图片/扫描件 OCR 识别：不做，不宣称。
