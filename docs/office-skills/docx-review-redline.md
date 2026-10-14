# docx-review-redline 交付说明

Word 审校批注与修订交付：按结构化编辑计划对 .docx 产生原生批注与修订稿，接收方在 Word 中逐条接受/拒绝。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 文档 + 编辑计划 → 含批注/修订的 `_REVIEWED.docx` + review-report.json | 是（核心场景） |
| replace / insert / delete / comment 四类操作 | 是 |
| `find` 文本被拆分到多个 w:r（跨 run） | 是，替换继承原 run 字符格式 |
| 同一 `find` 多处命中 | 是：`occurrence`（1 基序号）或 `scope: "all"` |
| 表格内段落、页眉页脚定位 | 是，表格命中报告行列，页眉页脚单独定位 |
| 输入已有批注/修订 | 是，合并保留既有内容，id 不冲突 |
| 接受全部修订后的正文 | 可由测试内独立的 accept 语义推导验证 |
| 渲染级逐页检查 / 目录字段更新 | 否（需 LibreOffice/Word 引擎，本环境无，显式未测） |
| 两版对比 | 否（用 docx-version-diff） |

## 安装与依赖

- Python 3.9+
- python-docx（MIT 许可，`pip install python-docx`）
- 标准库：zipfile / re / json / copy / shutil

技能包结构：

```text
docx-review-redline/
├── SKILL.md
└── scripts/apply_review.py
```

## 用法

```bash
python scripts/apply_review.py --input draft.docx --plan plan.json \
    --output draft_REVIEWED.docx --report review-report.json
```

plan.json：

```json
{"author": "审校人", "operations": [
  {"op": "replace", "find": "旧文本", "replace": "新文本", "comment": "理由"},
  {"op": "insert", "find": "锚文本", "insert": "插入文本", "position": "after"},
  {"op": "delete", "find": "删除文本", "comment": "理由"},
  {"op": "comment", "find": "目标文本", "comment": "意见"}
]}
```

## OOXML 产出约定

- 修订：原文本进 `w:del`（w:t → w:delText，保留原格式），新文本进 `w:ins`（继承模板 run 的 rPr）；插入 = `w:ins`；删除 = 仅 `w:del`
- 批注：`word/comments.xml`（含 w14:paraId）+ `commentRangeStart/End` + `commentReference` + `word/commentsExtended.xml`（w15:commentEx，paraId 与 comments.xml 匹配）；每处命中一条线程
- 包注册：`[Content_Types].xml` 为两个批注 part 注册 Override；document.xml.rels 注册关系；与既有批注合并时无重复 zip 条目
- id 唯一性：新修订/批注 id 从输入包既有 w:ins/w:del/批注 id 之上递增

## 交付报告字段

- `operations_total` / `applied`：计划操作数与实际应用数
- `results[]`：每个应用命中的 `location`（正文段落序号 / `table row R col C` / `section N header|footer`）
- `skipped[]`：未应用操作及原因（text not found / occurrence out of range / unknown op type / empty find / operation is not an object）
- `comments[]`：批注线程 id 与意见文本

## 退出码

- 0：完成（可含 skipped，完整性由报告承载）
- 2：输入错误（损坏/非 docx、计划 JSON 非法、根不是对象），不产出文件

## 验收依据与测试

对应补充基线 S01（Word 新建、保真修改与修订）的修订部分。黑盒测试位于 `tests/office_skills/test_docx_review_redline.py`（11 项）：

1. 正常：replace+批注（跨 run/表格/AAA OLD ZZZ 中间子串）、insert/delete、comment-only
2. 复杂：scope=all 接受语义与表格行列定位、occurrence 第 N 处、顺序操作命中前序写入文本、既有批注/修订合并（无重复条目、id 唯一、既有内容保留）、页眉批注
3. 异常：损坏 docx/非法 JSON/根非对象/文件不存在（退出 2）、未知操作类型/非对象操作/occurrence 越界/空 find（skipped 不崩溃）、空计划字节级原样复制

在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_docx_review_redline.py --basetemp <任务目录下唯一临时目录>
```

未测项（环境缺引擎，不宣称通过）：LibreOffice/Word 渲染逐页检查、目录（TOC）字段更新、Word 客户端实际显示效果。

## 来源与许可

原创实现（无外部代码复制）。竞品参考：doubao/word 1.0.46 的批注/修订读取与写入思路、@clawhub_ivangdavila/word-docx 1.0.2 的修订/批注注意事项均经审计，但前者无同题执行授权、后者无许可证，未搬运任何代码。Jiuwen 既有 harvey 技能的 w:ins/w:del 输出经审计存在中间子串错序与表格 paragraph_index 错位（word-audit/results.json 维度7），本技能按文档顺序定位独立修复该类缺陷，未复用其代码。运行时依赖 python-docx 1.2.0（MIT）。
