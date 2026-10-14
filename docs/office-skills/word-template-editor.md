# word-template-editor 交付说明

Word 模板保真编辑：在用户提供的 .docx 模板上原位填充结构化数据，不重建文档、不破坏原格式。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 含 `{{key}}` 占位符的 .docx 模板 + JSON 数据 → 填充后 .docx | 是（核心场景） |
| 占位符被拆分到多个 w:r（跨 run） | 是，替换文本继承占位第一个 run 的字符格式 |
| 表格单元格、页眉页脚中的占位符 | 是 |
| 保留命名样式、numId/ilvl 多级编号、分节、表格结构 | 是，`numbering.xml` 字节级不变并在报告中校验 |
| 未匹配占位清单、OOXML/OPC 交付校验 | 是，随 fill-report.json 输出 |
| 从零生成新文档 | 否（用 doc-generator 类技能） |
| 批注与修订 | 否（用 docx-review-redline） |

## 安装与依赖

- Python 3.9+
- python-docx（MIT 许可，`pip install python-docx`）
- 标准库：zipfile / xml.etree / json / re

技能包结构：

```text
word-template-editor/
├── SKILL.md
└── scripts/fill_template.py
```

## 用法

```bash
# 1. 查看模板占位符分布（可选）
python scripts/fill_template.py --template 模板.docx --inspect

# 2. 填充
python scripts/fill_template.py --template 模板.docx --data data.json \
    --output 填充后.docx --report fill-report.json
```

data.json 形如 `{"project": "智慧园区一期", "amount": "1280000"}`。

## 交付报告字段

- `matched` / `replacements`：已填充占位与总替换次数
- `unmatched`：模板存在但数据未提供的占位（原样保留，需向用户明示）
- `unused_data`：数据提供但模板没有的键
- `residual_placeholders`：填充后仍完整残留的占位名
- `literal_brace_fragments`：嵌套（`{{a{{b}}`）、未闭合（`{{x`）、空名（`{{}}`）等非法占位语法的孤立花括号片段统计——按字面保留并记录
- `numbering_xml_unchanged`：numbering.xml 是否字节级未改动
- `opc_check`：zip 必需 part、XML 可解析性、关系目标完整性

## 退出码

- 0：成功（可含未匹配占位，完整性由报告承载）
- 1：填充完成但 OPC 交付校验失败（文件仍产出，报告中有 errors）
- 2：输入错误（模板损坏 / 非 zip、数据 JSON 非法），不产出文件

## 验收依据与测试

对应基线 M04（Word 文档处理与检查）与补充基线 S01 的模板填充部分。黑盒测试位于 `tests/office_skills/test_word_template_editor.py`：

1. 常规：跨 run 占位、表格、页眉页脚、多级编号模板全量填充，断言文本/格式/编号/OPC
2. 复杂：占位完全跨 run + Heading 样式名不变 + numbering part 字节不变
3. 异常：数据缺失进未匹配清单（退出 0）、损坏 docx / 非法 JSON（退出 2）、嵌套/未闭合/空名占位语法按字面处理并记录

在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_word_template_editor.py
```

## 来源与许可

原创实现（无外部代码复制）。竞品参考：doubao/word 1.0.46 模板保真编辑思路与 @user_1a470ba8/wps-office-suite 5.3.0 的占位替换行为均经审计但未取得复制授权，未搬运任何代码。运行时依赖 python-docx 1.2.0（MIT）。
