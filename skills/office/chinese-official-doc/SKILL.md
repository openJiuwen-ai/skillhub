---
name: chinese-official-doc
description: 中文公文、事务性材料与正式工作文稿的起草、改写、压缩、审校与 Word 版式交付。当用户要求起草/修改通知、请示、报告、函、纪要、总结、讲话稿等中文公文或正式材料，要求文种路由、去口语化、降 AI 味、GB/T 9704 版式排版成 docx 时使用。含确定性风格检查（只报告不改写）与版式元素齐全性机检；不用于英文或文学创作。
display_name: 中文公文起草与排版
tags: [办公与生产力, 文档处理]
license: Apache-2.0 AND MIT
---

# 中文公文起草与模板排版

（本技能基于 MIT 许可的 chinese-official-writing 2.0.33 适配，来源与许可声明见 NOTICE.md）

## 适用范围

处理中文公文、事务性材料、正式工作材料、新闻消息与新闻评论的起草、改写、压缩、润色、审校、
文种核对、事实边界复核和 Word 正文整理。英文、文学、营销软文、社交媒体文案、个人求职信走其他路径。

## 入口契约

### 第一步：理解用户需求

- 寒暄、概念问答及无关请求直接按所问处理。
- 按顺序确定材料边界：用户明确设为模拟/虚构时按试写（读 `references/trial-draft-routing.md`）；
  有旧稿、现实材料、具体事项，或要求实际发送/据实/只用已给内容时按真实稿；其余仅给文种或宽泛
  主题的成稿请求按试写。模式确定后不重新选路。
- 从请求中识别：任务与交付件（起草/改写/压缩/审核/排版）、稿件用途（谁以什么身份写给谁）、
  材料与修改范围、篇幅与形式（是否要 docx、是否 GB/T 9704）。
- 信息不足以判断文种时，集中询问影响选路的信息，不连续追问。

### 第二步：选择文种

- 用户明确指定文字替换、其余逐字保留时，按 `references/targeted-checks.md` 处理。
- 仅整理已有空白模板不改业务内容时，按 `references/field-editing.md`。
- 需要处理正文内容时，按 `references/reference-index.md` 为每份稿件选定一个主叶；冲突时用
  `references/genre-routing.md` 判定实际用途。

### 第三步：按任务加读

- 起草：按主文种组织必要要素与合理分析。
- 改写：以最新版底稿为基础，按调整类型加读相应页（结构 `structure-editing.md`、字段
  `field-editing.md`、压缩 `compression-details.md`）。
- 审核：读主文种叶 + `references/review-checklist.md`。
- 需要 Word/docx/GB/T 9704/红头版式时叠加 `references/format-gbt9704.md`。

## 正文形态

- 正式正文直接呈现事实、办理事项和有据分析；AI 身份自述、提示词披露、起草步骤、免责话术严禁出现。
- 纯文本主标题独立成行、行末省略句号、标题后空一行；编号内容本身是完整正文句时保留句末标点。

## 写作与交付步骤

按 `references/writing-rules.md` 四步执行：材料与分析 → 成稿与篇幅 → 复核 → 交付。

复核阶段的工具使用：

```
python scripts/prose_lint.py --json 交付文本.md
python scripts/draft_length.py --json 交付文本.md
```

- `prose_lint.py` 只报告风险不改正文；同稿已合并检查不重复运行。
- 两次运行命中规则类别与位置应一致（确定性规则层）；报告不得表述为 AI 概率或作者判定。

## Word / GB/T 9704 版式交付

用户要求 docx 或 GB/T 9704 版式时，正文定稿后执行排版：

```
python scripts/gbt9704_layout.py --draft 定稿.txt --output 公文.docx --report layout-report.json
python scripts/gbt9704_layout.py --check 公文.docx --report check-report.json
```

- 草稿约定：首行 `标题：…`；`主送：…` 顶行；空行分段正文；末尾 `落款：…` 与 `日期：…`。
- 排版默认参数见 `references/format-gbt9704.md`（A4；边距上37/下35/左28/右26毫米；标题二号
  小标宋居中；正文三号仿宋、首行缩进2字符；落款右对齐）。
- `--check` 输出元素齐全性机检结果（标题居中与字号、正文字号与缩进、页边距）；未给的主送/
  落款/日期列为待补，不编造发文字号、签发人、印章等真实发文信息。
- 排版后交付 docx + 风格与形式风险报告（只报告不改写）。

## 材料与通用写法核查

遇到不熟悉的新文种、特殊事务场景时读 `references/external-research.md` 进行定向核查；
用户事实以用户材料为准。

## 依赖

Python 3.9+；排版脚本需 python-docx（`pip install python-docx`）；其余上游脚本仅用标准库。
