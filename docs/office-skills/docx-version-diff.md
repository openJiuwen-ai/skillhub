# docx-version-diff 交付说明

Word 文档版本结构级对比：对同一文档的两个 .docx 版本输出段落/表格单元格/页眉页脚/分节的变更清单（JSON）与变更报告 docx。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| v1/v2 两版 .docx → diff-report.json + 变更报告 docx（`--changes-doc`） | 是（核心场景） |
| 正文段落 modified/added/removed（difflib 序列对齐，带原始定位） | 是 |
| 表格单元格级差异（表N 行R 列C，旧值→新值） | 是 |
| 表格行列数变化 | 是，记 structural（`2x2 → 3x2`），不做单元格比对 |
| 表格数量变化 | 是，多出的表记 added/removed，可配对的继续单元格比对 |
| 页眉/页脚文本按节比较 | 是，计入 `header_footer_changed` |
| 分节变化（节数、页面尺寸、四边页边距） | 是，计入 `section_changed`，`page setup` 项给出四边页边距前后值 |
| 相同文件 | 是，空变更集、`identical: true`、退出码 0 |
| 损坏文件 / 非 docx zip / 文件不存在 | 是，退出码 2，不产出报告 |
| 跨 run 字符格式差异 / 修订标记（w:ins/w:del）语义级对比 | 否（纯文本层对比，不做格式与修订语义 diff） |
| 批注修订交付 | 否（用 docx-review-redline） |
| PDF 对比 | 否 |

## 安装与依赖

- Python 3.9+
- python-docx（MIT 许可，`pip install python-docx`）
- 标准库：argparse / difflib / json / sys

技能包结构：

```text
docx-version-diff/
├── SKILL.md
└── scripts/docx_diff.py
```

## 用法

```bash
python scripts/docx_diff.py --old v1.docx --new v2.docx \
    --report diff.json [--changes-doc changes.docx]
```

## 报告字段

- `identical`：两版是否无差异
- `summary`：`paragraph_modified` / `paragraph_added` / `paragraph_removed` / `table_cell_modified` / `header_footer_changed`（仅页眉页脚文本变化）/ `section_changed`（节数增删 + 页面尺寸/四边页边距变化）
- `changes[]`：每条含 `type`（paragraph / table / table_cell / header / footer / section）、`change`（modified / added / removed / structural）、`location`、`before`、`after`

## 退出码

- 0：完成（含无差异）
- 2：输入错误（损坏/非 docx/文件不存在），不产出文件

## 验收依据与测试

对应补充基线 S01（Word 新建、保真修改与修订）的两版对比部分。黑盒测试位于 `tests/office_skills/test_docx_version_diff.py`（8 项）：

1. 正常：改一词→1 modified 0 added/removed；表格单元格值变→精确到 `table 0 row 1 col 1`
2. 复杂：新增节+页眉变化；右/下页边距变化被检出且不计入页眉页脚计数；段落改/增/删+单元格改+新增表格+页脚改并存并产出可打开的变更报告 docx；表格行数变化记 structural
3. 异常：相同文件空变更集退出 0；损坏文件（junk 字节）/合法 zip 非 docx/文件不存在均退出 2

在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_docx_version_diff.py --basetemp <任务目录下唯一临时目录>
```

未测项（环境缺引擎，不宣称通过）：Word/LibreOffice 渲染级逐页对比、修订标记（w:ins/w:del）语义级对比、格式（字体/样式）变化检测。

## 来源与许可

原创实现（无外部代码复制）。竞品参考：doubao/word 的 compare_source_to_target + 结构 diff 思路、@user_1a470ba8/wps-office-suite 的两版比较场景均经审计（word-audit/results.json 维度8），但前者无同题执行授权、后者无可移植许可，未搬运任何代码。Jiuwen 既有技能的版本对比为结构化清单输出，无可复用实现。运行时依赖 python-docx 1.2.0（MIT）。
