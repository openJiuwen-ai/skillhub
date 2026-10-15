# Word 模板填充与格式保持

在用户提供的 .docx 模板上原位填充结构化数据并保真交付。当用户给出含占位符（如 {{name}}）的 Word 模板和字段数据，要求"按模板填充/生成合同/套打/按模板出报告"且不破坏原格式时使用。保留 run 级字符格式、命名样式、原生多级编号(numId/ilvl)、分节与页眉页脚；输出未匹配占位清单和 OOXML/OPC 交付校验结果。不用于从零生成新文档（用 doc-generator），也不做批注修订（用 docx-review-redline）。

## 使用

安装到支持 Skill 的宿主后，说明要处理的文件、目标和输出位置。具体参数、产物格式及失败行为见 [SKILL.md](SKILL.md)。依赖与验证环境见 [DEPENDENCIES.md](DEPENDENCIES.md)。

```shell
python -m pip install -r requirements.txt
```

运行前保留输入备份，并为外部平台操作配置最小必要权限。检查结果中的缺失、待确认、失败项属于交付内容，不能省略或改成成功。

## 许可证

本包新增实现按 Apache-2.0 分发，全文见 LICENSE。
