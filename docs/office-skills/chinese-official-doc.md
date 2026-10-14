# chinese-official-doc 交付说明

中文公文起草与模板排版：文种路由、去口语化/降 AI 味风格检查（只报告不改写）、GB/T 9704 版式 docx 交付与要素齐全性机检。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 通知/请示/报告/函/纪要等中文公文起草、改写、压缩、审校 | 是（SKILL.md 三步入口 + references 文种路由） |
| 定稿文本 → GB/T 9704 版式 .docx（标题小标宋二号居中、正文仿宋三号缩进2字符、落款右空2字） | 是，`scripts/gbt9704_layout.py` |
| 排版后 docx 要素齐全性机检（页边距/标题/正文/落款） | 是，`--check` 模式 |
| 确定性风格风险检查（套话/口语/AI 味规则，只报告不改写） | 是，MIT 复用的 `scripts/prose_lint.py` |
| 篇幅/压缩核查 | 是，`scripts/draft_length.py` |
| 修改范围界定、材料比对 | 是，`scripts/edit_scope.py`、`scripts/material_diff.py` |
| 编造发文字号、签发人、印章等真实发文信息 | 否，缺失要素列为待补 |
| 英文写作、文学创作、营销文案 | 否 |

## 安装与依赖

- Python 3.9+
- 排版脚本需 python-docx（MIT 许可，`pip install python-docx`）
- 其余上游脚本仅用标准库

技能包结构：

```text
chinese-official-doc/
├── SKILL.md              # 入口契约（上游结构适配 Jiuwen 前置格式）
├── LICENSE.md            # 上游 MIT 全文（原样保留）
├── NOTICE.md             # 复用范围与来源声明
├── README.upstream.md    # 上游 README
├── references/           # 83 个上游素材（文种路由/写作规则/GB/T 9704 格式等）
└── scripts/
    ├── draft_length.py   # 上游（MIT）
    ├── edit_scope.py     # 上游（MIT）
    ├── material_diff.py  # 上游（MIT）
    ├── prose_lint.py     # 上游（MIT）
    └── gbt9704_layout.py # 本包原创：GB/T 9704 排版与机检
```

## 用法

```bash
# 1. 风格风险检查（只报告不改写，输出 JSON findings）
python scripts/prose_lint.py --json 定稿.txt

# 2. GB/T 9704 排版（草稿约定见 SKILL.md：标题：/主送：/落款：/日期：前缀）
python scripts/gbt9704_layout.py --draft 定稿.txt --output 公文.docx --report layout-report.json

# 3. 对已有 docx 做要素齐全性机检
python scripts/gbt9704_layout.py --check 公文.docx --report check-report.json
```

## 交付报告字段（layout-report.json / check-report.json）

- `checks`：逐项机检（paper_a4、四边页边距、title_centered/title_22pt_erhao/title_xiaobiaosong、body_16pt_sanhaofangsong/body_first_line_indent_2chars/body_fangsong、signoff_right_aligned）
- `elements_complete`：全部机检是否通过
- `missing`：未通过项清单（未提供的主送/落款/日期列为待补，不编造）
- `draft_elements`：草稿解析出的 title/recipient/body/signoff/date

## 退出码（gbt9704_layout.py）

- 0：排版或机检成功（完整性由报告承载）
- 2：输入错误（草稿为空/无标题无正文/文件不存在/损坏），不产出文件

prose_lint.py：0=无命中，1=有命中（风险以 JSON findings 呈现，不修改原稿）。

## 验收依据与测试

对应冻结规格 specs.json（chinese-official-doc 项）与 decision-register 测试计划。黑盒测试位于 `tests/office_skills/test_chinese_official_doc.py`（8 项）：

1. 正常：完整草稿排版 + 独立断言版式（边距 37/35/28/26mm、标题 22pt 居中小标宋、正文 16pt 仿宋缩进 32pt、落款日期右对齐）+ `--check` 机检通过
2. 复杂：markdown 标题变体、一、黑体一级/（一）楷体二级标题字体、标题行距 31 磅/正文 28 磅、落款右空 2 字（right_indent 32pt）；缺主送/落款/日期时 missing 列待补且不编造发文字号
3. 确定性：同稿两次排版 document.xml 字节一致；prose_lint 两次命中（label, line）一致
4. 异常：空草稿、不存在的草稿文件 → 退出码 2
5. 措辞：prose_lint 输出不含"概率"类表述、标签不含 AI 判定、原稿字节不变（只报告不改写）；上游 4 脚本 `--help` 可运行

在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_chinese_official_doc.py --basetemp <唯一临时目录>
```

## 来源与许可

复用 MIT 许可的上游包 `@user_f3d82da7/chinese-official-writing` 2.0.33（Copyright 2026 chinese-official-writing contributors，2026-10-13 快照）：references/ 全部 83 个素材、4 个上游脚本（字节级一致，SHA256 已核验）、SKILL.md 入口结构。MIT 全文见包内 LICENSE.md，复用声明见 NOTICE.md。`scripts/gbt9704_layout.py` 为本仓库原创（GB/T 9704 版式排版与要素机检）。运行时依赖 python-docx 1.2.0（MIT）。竞品 doubao/word 1.0.46 公文方向仅目录对标，未取得复制授权，未搬运任何代码。
