---
name: doc-sensitive-redact
description: 文档敏感信息识别与打码交付（身份证/手机号/银行卡/邮箱），支持 DOCX/XLSX/PPTX/PDF 单文件或目录批量。双轨识别（正则形状 + 校验位/Luhn 算法复核）避免误杀；不只处理可见正文——删除修订、批注、数值单元格、PPT 备注、docProps、PDF 元数据/XMP/书签/批注/链接目标/表单域值/附件（含压缩 Office 附件逐条目重写）等隐藏可达内容一并清洗；每个输出做底层回读验证，无法清洗的残留会被显式阻断而不是笼统成功。当用户要求"脱敏/打码/去敏感信息后再发出去"时使用。15 位数字与校验位不符的 18 位数字标 needs_review 不误打。
---

# 文档敏感信息识别与打码交付

## 何时使用

- DOCX/XLSX/PPTX/PDF（单文件或整个目录）→ 打码后文件 + 打码清单
- 用户说"把这批文件里的身份证/手机号/银行卡/邮箱打码后交付"

## 用法

```
python scripts/redact.py --input 材料目录_or_单文件 --outdir out/ [--types id_card,phone,bank_card,email] [--report manifest.json]
```

退出码：0=全部完成且通过底层回读验证（可含 needs_review）；2=输入错误，或任一文件失败/被阻断（混合批处理不再以 0 掩盖部分失败）。

## 识别规则（双轨，脚本保证）

| 类型 | 第一轨（形状） | 第二轨（算法复核） | 处理 |
|---|---|---|---|
| 身份证 | 18 位含出生日期模式 | GB11643 校验位 | 两轨都过 → 打码；形状对校验位错 → `needs_review` 不打 |
| 手机号 | 1[3-9] 开头 11 位 | 号段复核 | 两轨都过 → 打码 |
| 银行卡 | 13-19 位数字 | Luhn | Luhn 过 → 打码；不过 → 视为普通数字不打 |
| 邮箱 | 邮箱正则 | 语法 | 打码 |
| 15 位数字 | 形状接近证件 | 无校验位 | `needs_review` 不打，避免误杀 |

`--types` 可只启用部分类型（其余类型保持原样且不进入验证范围）。

## 处理方式（按格式，覆盖隐藏可达内容）

- **DOCX/PPTX**：直接在包内全部 XML 部件上做三级替换——段落 run 级（跨 run 完整替换，含 `w:delText` 删除修订、`a:fld` 域）、文本节点级、属性级；覆盖正文/表格/页眉页脚/批注/脚注尾注/PPT 备注/幻灯片/版式/母版/图表文本/docProps/rels。未改动部件按原始字节复制。
- **XLSX**：openpyxl 处理字符串单元格、数值型手机号/证件号（转为打码文本）、单元格批注（含作者）、文档属性；保存后再对包内剩余 XML 部件做底层清洗；openpyxl 会丢弃的线程批注会被保留（清洗后回填）；openpyxl 无法建模而被丢弃的部件（图表/OLE/ActiveX 等）在清单 `dropped_unmodeled_parts` 中如实列出。
- **PDF**：页面文本 `search_for` 定位 → `add_redact_annot` → `apply_redactions`（内容流底层删除，非画黑框）；元数据、XMP、书签标题、批注信息（content/title/subject）、链接目标（URI/启动文件等字符串字段）、表单域值一并清洗；文件附件批注内容按 zip/文本视图逐条目重写并保留无关内容（无法可靠重写的二进制明文/加密/超深嵌套显式阻断整份交付）；含敏感值的文档级附件被移除并在清单计数；含 AcroForm 或图像页时标记 needs_review 且不宣称 `pdf_removed_and_verified`。

## 底层回读验证（交付阻断）

每个输出文件保存后重新逐部件检查：已识别的敏感值不得再出现在任何包部件（XML 按全文视图、二进制按原始字节；PDF 按页面文本/链接目标/表单域值/批注/附件（zip 递归展开）/元数据/XMP/书签/原始字节，且对全部启用类型重新识别，而非仅搜索此前已识别的值）。任何残留 → 该文件标记 `failed`、输出删除、进程退出 2——支持范围外的内容（如二进制部件内的明文、不可靠重写的附件内容）被显式阻断，不做笼统成功。PDF 的 `pdf_removed_and_verified` 仅在无表单/图像页等未核验表面时为 true，否则为 false 并附 `unverified_surfaces` 说明。包内存在嵌入对象（embeddings）时标记 `embedded_object_needs_manual_review`。

## 输出（outdir/）

- 单文件：`<原名>_REDACTED.<ext>`；目录批量：按相对路径展平命名（`子目录\名.docx` → `子目录__名_REDACTED.docx`），重名冲突会显式失败而非覆盖。
- `redact-manifest.json`：逐文件 `{redactions: [{type, count, locations}], needs_review: [{type, count}], pdf_removed_and_verified, unverified_surfaces, removed_embedded_files, dropped_unmodeled_parts, threaded_comments_preserved, failed, error}`
  - 清单**不含**原始敏感值，只有类型/位置/计数；位置精确到部件与段落/单元格/页。

## 失败与降级

- 损坏文件 → 该文件记 failed，批量继续，最终退出 2
- PDF 中文字被拆分到多个 span 导致 search 未命中 → `search_miss_needs_manual` 提示人工复核
- needs_review 项（坏校验位/15 位号）保持原样交付，仅在清单提示
- 局限：图片/扫描件中的像素文字、嵌入对象内部不做底层识别，需人工复核（清单有对应标记，PDF 含图像页时不宣称已完全验证）；PDF 表单字段值会被清洗，但表单结构/脚本仍需人工复核

## 依赖

实测栈：Python 3.12.9（2026-10-15 任务 venv 实测，依据 inputs/runtime-dependency-metadata.json）。依赖最低版本：PyMuPDF 1.28.2 要求 Python >=3.10（lxml >=3.8、openpyxl >=3.8）；更旧的依赖版本组合未验证、不作承诺。lxml（BSD-3-Clause，DOCX/PPTX 底层处理）、openpyxl（MIT，XLSX）、PyMuPDF（AGPL-3.0/商业双许可，作为独立安装依赖使用；用于封闭场景或需商业授权分发时应购买商业许可或替换依赖）。
