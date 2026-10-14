---
name: word-template-editor
description: 在用户提供的 .docx 模板上原位填充结构化数据并保真交付。当用户给出含占位符（如 {{name}}）的 Word 模板和字段数据，要求"按模板填充/生成合同/套打/按模板出报告"且不破坏原格式时使用。保留 run 级字符格式、命名样式、原生多级编号(numId/ilvl)、分节与页眉页脚；输出未匹配占位清单和 OOXML/OPC 交付校验结果。不用于从零生成新文档（用 doc-generator），也不做批注修订（用 docx-review-redline）。
---

# Word 模板保真编辑

## 何时使用

- 用户模板（.docx）+ 结构化数据（JSON）→ 填充后的 .docx
- 要求格式必须与模板一致：字体、编号、表格、页眉页脚都不能变

## 工作流程

1. 读取模板，先用 `scripts/fill_template.py --inspect` 查看占位符分布（可选）。
2. 准备数据 JSON：`{"占位名": "值", ...}`。
3. 执行填充：

```
python scripts/fill_template.py --template 模板.docx --data data.json --output 填充后.docx --report fill-report.json
```

4. 查看 fill-report.json：
   - `matched`：已填充占位与替换次数
   - `unmatched`：模板中存在但数据未提供的占位（原样保留，交付时提醒用户）
   - `unused_data`：数据提供但模板没有的键
   - `opc_check`：zip 结构 / XML 可解析性 / 关系完整性检查结果
5. 向用户交付填充后文件 + 未匹配占位清单。存在未匹配项时明确说明，不要谎称全部填充。

## 保真规则（脚本保证）

- 占位符跨多个 run 被拆分时，能完整匹配并替换，替换文本继承占位第一个 run 的字符格式
- 不改写 `numbering.xml`（numId/ilvl 定义原样保留），新增列表项复用既有编号
- 段落样式名、分节(sectPr)、页眉页脚、表格结构不动，只替换占位文本
- 表格单元格、页眉页脚中的占位符同样处理

## 失败与降级

- 模板损坏或不是合法 zip：退出码 2，报错，不产出文件
- 数据 JSON 解析失败：退出码 2
- 占位符数据缺失：不算失败，进 `unmatched` 清单，退出码仍为 0（交付完整性由报告承载）

## 依赖

Python 3.9+，python-docx（`pip install python-docx`）。无外部服务依赖。
