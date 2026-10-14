# Office Skills（办公技能包）

本目录包含 18 个办公领域技能包，由 GLM-5.3 会话开发（分支 `codex/office-skills-glm53`），用于 Git 仓库导入（技能根路径 `skills/office`，见《Git仓库导入Skill》）。

## 技能清单

| 技能 | 职责 | 关键脚本 |
|---|---|---|
| word-template-editor | Word 模板保真填充（跨 run 占位/编号保持/OPC 校验） | `fill_template.py` |
| docx-review-redline | Word 原生批注 + 修订（replace/insert/delete/comment，与既有批注/修订合并） | `apply_review.py` |
| docx-version-diff | 两版 Word 结构级对比（段落/表格/页眉/分节） | `docx_diff.py` |
| chinese-official-doc | 中文公文起草 + GB/T 9704 排版（复用 MIT 上游 2.0.33，见 NOTICE.md） | `gbt9704_layout.py` 等 |
| meeting-minutes-actions | 会议纪要 + 待办追踪（否定式引用不误计/未提及状态原样保留/数字冲突双方保留） | `minutes_actions.py` |
| sheet-consolidate-reconcile | 多源表格合并对账（合计行剔除/重复主键明细） | `consolidate.py` |
| deck-delivery-qa | PPT 交付质检（确定性几何 lint，未渲染显式声明） | `deck_qa.py` |
| pdf-structured-extract | PDF 结构化提取（表格/稀疏页渲染/数字两轮校验） | `extract_pdf.py` |
| pdf-nl-edit | PDF 指定页局部编辑（底层删除+其余页不变验证） | `pdf_edit.py` |
| doc-sensitive-redact | 敏感信息打码（双轨识别/PDF 真删除回提验证） | `redact.py` |
| doc-translate-layout | 翻译保持版式（工作表回填/术语核对/超长回退） | `translate_layout.py` |
| evidence-digest | 多源证据台账（来源定位/冲突清单/问题覆盖） | `evidence_ledger.py` |
| input-preflight-check | 材料齐全性三态核对 + 数据范围校验（阻断制） | `preflight.py` |
| tax-compliance | 税务材料归集 + 规则溯源分析（不代申报） | `tax_review.py` |
| proposal-bid-generation | 招标响应矩阵 + 投标草稿（缺口不编造） | `bid_matrix.py` |
| month-end-close | 试算平衡勾稽（恒等式复算/合计行防重复） | `close_check.py` |
| hr-material-analysis | 薪资考勤比对/名单汇总/简报（敏感字段脱敏） | `hr_compare.py` |
| online-office-delivery | 在线办公交付与回读（宿主授权探测，无授权显式 blocked） | `online_delivery.py` |

## 使用

每个技能包独立可用：`SKILL.md` 为入口（触发条件、输入输出、失败路径、依赖），`scripts/` 为可执行脚本（`python scripts/<name>.py --help` 查看用法），`references/` 为参考素材。

依赖（按需安装）：`python-docx`、`openpyxl`、`python-pptx`、`pymupdf`。

## 测试

黑盒测试位于 `tests/office_skills/`（58 项，全部通过；金标准为手工推导值，不复用被测实现）：

```
python -m pytest tests/office_skills -q
```

## 许可

- `chinese-official-doc` 复用 `@user_f3d82da7/chinese-official-writing` 2.0.33（MIT），保留 `LICENSE.md` 与 `NOTICE.md`
- 其余技能为本任务自主实现；运行依赖许可见各 SKILL.md（PyMuPDF 为 AGPL/商业双许可，作为独立安装依赖使用）
