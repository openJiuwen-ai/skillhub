# 文档敏感信息脱敏

对 DOCX、XLSX、PPTX 和 PDF 的支持内容识别并处理身份证、手机号、银行卡号和邮箱，交付脱敏副本及逐文件检查清单。PDF 检查还包括链接、批注、元数据、表单值和附件等；图片中的文字、表单结构等未完全核查内容会保留复核标记。

## 使用

在独立 Python 3.12 环境安装 `requirements.txt`，再按 [SKILL.md](SKILL.md) 调用入口。输入文件应另存备份，交付时同时查看产物及 `redact-manifest.json`，不能把 `needs_review` 当成全面脱敏成功。

```shell
python -m pip install -r requirements.txt
python scripts/redact.py --input input.pdf --outdir out
```

## 许可与来源

本技能实现遵循 Apache-2.0，全文见 LICENSE。内容流辅助逻辑复用自本仓库 PDF 修改技能，第三方依赖分别遵循自己的许可；具体范围和 lxml 可选组件例外见 [DEPENDENCIES.md](DEPENDENCIES.md)。发布包不包含第三方 wheel、字体或 Python 环境。
