# 运行环境与许可证据

本清单对应 Windows x64 / Python 3.12 的独立运行环境。安装 `requirements.txt` 后运行；该环境未安装 PyMuPDF/fitz。其他平台或版本组合应重新核对。发布包不附带第三方二进制、字体或 Python 环境；依赖通过官方包管理器独立安装，依赖自身的许可证、版权与通知要求继续适用。

| 发行包 | 实测版本 | wheel 许可声明 | 随包许可证据文件数 |
|---|---|---|---|
| cffi | 2.1.1 | MIT-0 | 1 |
| charset-normalizer | 3.5.2 | MIT | 1 |
| cryptography | 50.0.2 | Apache-2.0 OR BSD-3-Clause | 3 |
| pdfminer.six | 20260107 | MIT | 1 |
| pdfplumber | 0.11.10 | License :: OSI Approved :: MIT License | 1 |
| pillow | 12.3.0 | MIT-CMU | 1 |
| pycparser | 3.11 | BSD-3-Clause | 1 |
| pypdf | 6.19.0 | BSD-3-Clause | 1 |
| pypdfium2 | 5.14.0 | BSD-3-Clause, Apache-2.0, dependency licenses | 18 |

PDFium 原生依赖还包括 FreeType（FTL）、ICU、libjpeg-turbo、libpng、zlib 等，各自许可不能仅用 Python 包的一项许可证概括。对应 wheel 随附完整许可文本；本次逐文件哈希证据保留在发布前审计资料中。pypdfium2 中文档许可与运行时代码许可分别适用。
