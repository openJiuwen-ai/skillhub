# 运行环境与许可证

本清单对应 Windows x64 / Python 3.12 的独立 clean venv（仅安装 `requirements.txt`，未安装 PyMuPDF/fitz）。其他平台或版本组合应重新核对。发布包不附带第三方二进制、字体或 Python 环境；依赖通过官方包管理器独立安装，依赖自身的许可证、版权与通知要求继续适用。

## 运行时依赖（实测版本）

| 发行版 | 实测版本 | wheel 许可声明 | 用途 |
|---|---|---|---|
| lxml | 6.0.2 | BSD-3-Clause | DOCX/PPTX/附件 XML 解析（仅 `lxml.etree`） |
| openpyxl | 3.1.5 | MIT | XLSX 读写、单元格批注与文档属性 |
| pypdf | 6.19.0 | BSD-3-Clause | PDF 结构读写与对象手术 |
| pdfplumber | 0.11.10 | MIT | PDF 页面文本定位 |
| pdfminer.six | 20260107 | MIT | pdfplumber 传递依赖；标准字体宽度数据 |
| pillow | 12.3.0 | MIT-CMU | pdfplumber 传递依赖 |
| pypdfium2 | 5.14.0 | BSD-3-Clause OR Apache-2.0 | pdfplumber 传递依赖（本技能不调用其渲染接口） |
| charset-normalizer | 3.5.2 | MIT | pdfminer.six 传递依赖 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | pdfminer.six 传递依赖（加密 PDF 处理） |
| cffi | 2.1.1 | MIT-0 | cryptography 传递依赖 |
| pycparser | 3.11 | BSD-3-Clause | cffi 传递依赖 |
| et_xmlfile | 2.0.0 | MIT | openpyxl 传递依赖 |

无 GPL/AGPL/LGPL 运行时依赖。`pypdfium2` 内嵌 PDFium 及 FreeType/ICU/libjpeg-turbo/libpng/zlib 等原生库，各自许可不能仅用 Python 包的顶层声明概括；对应 wheel 随附完整许可文本。

### lxml 许可说明（不笼统写“整包全 BSD”）

lxml 6.0.2 wheel 的 `LICENSES.txt` 除 BSD-3-Clause 外，还列入：isoschematron 可选资源（附单独/未声明许可说明）与一个 GPL 许可的测试脚本（当前 wheel 未随包该 test 文件）。**本技能只 `import lxml.etree`，不调用 isoschematron，也不分发 lxml wheel**；这些可选资源/测试文件不进入本技能的运行路径。许可结论仅针对实际运行的 `lxml.etree` 模块，不覆盖未使用的可选组件。

## 复用来源

`scripts/redact.py` 中 PDF 字体编解码与内容流分词/命中切除逻辑，复用自本仓库 `skills/office/pdf-nl-edit/scripts/pdf_edit.py`（本仓库自有实现，随仓库根 `LICENSE` 分发），为使本技能自包含而复制，未修改 pdf-nl-edit。目的即避免重复实现复杂字体缺陷。除此之外未复制任何外部来源代码；无 1:1 竞品移植。

## 已移除依赖

初版运行时依赖 PyMuPDF（AGPL-3.0/商业双许可）。本次迁移将其全部移除：PDF 路径改用上述宽松栈自行实现（内容流算子级删除、Form XObject 递归、元数据/XMP/书签/批注/链接/表单 V&AP/附件清洗、孤立对象清理），不再 import PyMuPDF，也不调用其子进程。

## 发布准备复核

主控另建仅安装上述发布依赖的 Python 3.12 环境，共 12 个依赖发行包，未安装 PyMuPDF、fitz 或 pytest。已归档 30 份随 wheel 提供的许可文件及其 SHA-256。此清单针对实际运行模块；不把 lxml 可选组件或 pypdfium2 的文档许可笼统归入顶层代码许可证。
