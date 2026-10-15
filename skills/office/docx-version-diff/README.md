# Word 版本差异对比

对同一 Word 文档的两个版本 .docx 做结构级对比，输出段落/表格单元格/页眉页脚/分节的变更清单（新增/删除/修改+定位）与变更报告 docx。当用户给出 v1/v2 两版文档问"改了什么/两版差异/对比这两份合同"时使用。不做批注修订（用 docx-review-redline），不读 PDF。

## 使用

安装到支持 Skill 的宿主后，说明要处理的文件、目标和输出位置。具体参数、产物格式及失败行为见 [SKILL.md](SKILL.md)。依赖与验证环境见 [DEPENDENCIES.md](DEPENDENCIES.md)。

```shell
python -m pip install -r requirements.txt
```

运行前保留输入备份，并为外部平台操作配置最小必要权限。检查结果中的缺失、待确认、失败项属于交付内容，不能省略或改成成功。

## 许可证

本包新增实现按 Apache-2.0 分发，全文见 LICENSE。
