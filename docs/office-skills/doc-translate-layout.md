# doc-translate-layout 交付说明

docx/pptx 段落级翻译并保持版式：`extract`（抽取待译工作表，与源文件 sha256 强绑定）→ 宿主产出译文 → `apply`（校验源绑定后**完整**回填）→ `verify`（结构与译文回读验证 + 长文本风险清单）。译文由调用方（宿主模型或人工）提供，本技能保证结构保持的确定性与可验证性，不冒充机器翻译质量。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| docx 正文/表格内段落、页眉页脚抽取与回填 | 是 |
| pptx 文本框段落抽取与回填 | 是 |
| 多 run 段落完整替换（译文后不残留原文） | 是：清空全部 run 文本，译文写入首 run（保留其字符格式） |
| 同页不同 shape 段落 id 唯一 | 是：id 形如 `s01-sp2-p000`（含 shape 标识），译文不互相覆盖 |
| 工作表与源文件强绑定 | 是：工作表记录 `source_sha256`，源变更/无绑定的旧工作表被拒绝（退出 2，不产出） |
| 译文引用工作表不存在的 id | 拒绝（退出 2），防止译错对象 |
| 术语表校验 | 是：源文含术语时校验译文是否用指定译法，未命中记 `glossary_misses` |
| 超长译文回退 | docx 显式字号降 1pt（最低 9pt）记 `font_shrunk`；其余记 `needs_attention` |
| 混合 run 格式降级 | 显式记录 `format_degradations`（合并到首 run 格式），不静默 |
| 结构/译文回读验证 | 是：`verify` 逐项核对 id/style/location 与译文/原文，输出 `long_text_risks` |
| 未翻译项 | 保留原文并列入 `untranslated_ids`，不填占位 |
| 机器翻译质量 | 否：`mt_engine: "external"`，译文质量不在验证范围（无 MT 引擎时标 not_verified） |
| pptx 图形/图片内文字 | 不可抽取，不进入工作表（宿主需说明未覆盖范围） |
| 损坏/不存在输入 | 退出 2 |

## 安装与依赖

- Python 3.9+
- python-docx（MIT）、python-pptx（MIT），按格式安装
- 标准库：argparse / hashlib / json / os / sys

技能包结构：

```text
doc-translate-layout/
├── SKILL.md
└── scripts/translate_layout.py
```

## 用法（完整工作流）

```bash
# 1. 抽取待译工作表（含源绑定 sha256）
python scripts/translate_layout.py extract --input source.docx --worksheet ws.json

# 2. 宿主逐项翻译 ws.json 的 items -> tr.json（{"p-0000": "译文", ...}）

# 3. 回填译文（保持版式，校验源绑定）
python scripts/translate_layout.py apply --input source.docx --worksheet ws.json \
    --translations tr.json --output translated.docx \
    --glossary terms.json --report report.json

# 4. 回读验证（结构 + 译文 + 长文本风险）
python scripts/translate_layout.py verify --input source.docx --worksheet ws.json \
    --output translated.docx --translations tr.json --report verify-report.json
```

交付前宿主检查清单（SKILL.md 同步维护）：`untranslated_ids` 须为空或有确认、`glossary_misses` 清零或用户接受、`fallback_list`/`long_text_risks` 溢出复核、`format_degradations` 混排格式复核、`verify` 退出码 0。

## 输出报告字段（report.json）

- `source_binding`：`{source_sha256, matched: true}`——本次回填确实作用于绑定源
- `translated` / `untranslated_ids` / `items_total`
- `glossary_hits[]` / `glossary_misses[]`（含 `expected` 指定译法）
- `fallback_list[]`：`{id, src_len, tgt_len, action: font_shrunk|needs_attention}`
- `format_degradations[]`：`{id, runs_with_text_before, note}`
- `mt_engine: "external (caller-provided translations)"` 与诚实边界说明

verify-report.json 另含 `structure_preserved` / `structure_problems` / `text_failures` / `long_text_risks` / `verified`。

## 退出码

- 0：成功（verify 时表示结构与译文核对全部通过）
- 2：输入或校验错误（损坏/不存在输入、工作表无绑定或与源不一致、译文含未知 id、无法写出输出；verify 时表示结构或译文核对失败）

## 验收依据与测试

对应 `specs.json` 的 doc-translate-layout（docx/pptx 段落级翻译保持版式：抽取→译文工作表→回填→溢出回退；异常含未翻译清单不静默、超长回退、损坏非零退出）与 `decision-register.json`: independent_implement。本项无 1:1 豆包目录条目（`baseline-matrix.json` 的 `baseline_skills_without_1to1_doubao_item`，WorkBuddy 延伸保留任务）。

黑盒测试位于 `tests/office_skills/test_doc_translate_layout.py`（11 项，独立推导预期值，不 mock 实现，不 import 被测模块）：

1. 正常（2）：docx（标题+表格+多 run 段+页眉）部分翻译——样式/表格/页眉保留、源文件字节不变、报告字段完整；pptx 跨页两框各自正确回填。
2. 复杂（4）：多 run 段完整替换无残留（冻结缺陷 1）；同页两 shape id 唯一且译文不互相覆盖（冻结缺陷 2）；超长译文 docx 降字号 12→11pt、pptx 记 needs_attention；verify 回读通过 + 文本/结构篡改均被检出。
3. 异常（5）：源变更的旧工作表拒绝且不产出（冻结缺陷 3）；无绑定工作表拒绝；未知译文 id 拒绝；工作表原文被篡改拒绝；损坏/缺失输入退出 2。

在仓库根目录运行（任务 venv 解释器，任务目录下唯一临时目录）：

```bash
<python> -m pytest tests/office_skills/test_doc_translate_layout.py --basetemp <任务目录下唯一临时目录>
```

家族回归（同技能）：`tests/office_skills/test_pdf_ppt_family.py::test_translate` 通过。

## 修复记录（2026-10-15，相对初版）

主控冻结探针 `benchmarks/translation-complex/before-review/controller-results.json` 证实三项真实错误，均已修复并复测：

1. **多 run Word 回填残留原文**（`Approved budget预算`）：`set_docx_para_text` 在首 run 后提前 return。修复为清空全部 run 文本、译文写入首 run，并显式记录 `format_degradations`。
2. **PPT 同页段落 id 重复**（两个框都是 `s01-p000`，均被回填 `Timeline`）：id 只含 slide+shape 内段落序号。修复为 id 含 shape 标识（`s01-sp{shape_id}-p{pi}`）并做重复防御。
3. **工作表与源不一致仍退出 0**（把 runs.docx 的旧译文覆盖到 changed.docx）：无任何源绑定校验。修复为工作表记录 `source_sha256`，apply/verify 校验哈希 + 逐项 id/原文核对 + 未知译文 id 拒绝，任何校验失败退出 2 且不产出输出。

冻结验证：`benchmarks/translation-complex/controller-after-fix-20261015-102625/controller-results.json`，`passed=true`（docx_complete_replacement / pptx_unique_ids / pptx_correct_objects / stale_rejected / source_unchanged 全部通过），未覆盖 before-review 历史证据。

另新增 `verify` 子命令承载 SKILL.md 要求的结构与长文本风险回读；`extract` 不再通过 `get_or_add_pPr` 改写内存文档。

## 来源与许可

本技能为原创实现（`decision-register.json`: independent_implement；未复制外部代码；无 1:1 竞品条目，未搬运任何竞品代码）。

- 本技能代码 / SKILL.md / 文档随本仓库按根目录 `LICENSE` 分发。
- 运行时依赖 python-docx 1.2.0（MIT）、python-pptx 1.0.2（MIT），已登记于 `license-register.json` runtime_dependencies。任务中安装于任务 venv，未全局安装。

## 未测项（不宣称通过）

- 豆包端同题竞品验收：`parity_status=not_verified`，未伪造竞品结果（本项无 1:1 豆包目录条目）。
- 机器翻译质量：无 MT 引擎，译文质量由提供方负责，报告标 `not_verified`。
- WPS/Word 桌面端打开复验：结构合法性由 python-docx/python-pptx 回读间接验证，未做渲染级检查。
- pptx 母版/版式 inherited text、SmartArt/图表内文字不在抽取范围。
