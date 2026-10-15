# Word 批注与修订

对 .docx 应用结构化审校计划，产出含原生批注（comments.xml + commentReference）与修订（w:ins/w:del）的审校稿，供接收方在 Word 中逐条接受/拒绝。当用户要求"审校这份文档并给批注/修订/红线稿/track changes"时使用。输入是文档+编辑计划（查找文本、替换/插入/删除文本、审校意见），输出 _REVIEWED.docx 与意见定位对照表。不用于模板占位填充（用 word-template-editor），不做两版对比（用 docx-version-diff）。

## 使用

安装到支持 Skill 的宿主后，说明要处理的文件、目标和输出位置。具体参数、产物格式及失败行为见 [SKILL.md](SKILL.md)。依赖与验证环境见 [DEPENDENCIES.md](DEPENDENCIES.md)。

```shell
python -m pip install -r requirements.txt
```

运行前保留输入备份，并为外部平台操作配置最小必要权限。检查结果中的缺失、待确认、失败项属于交付内容，不能省略或改成成功。

## 许可证

本包新增实现按 Apache-2.0 分发，全文见 LICENSE。
