---
name: doc-translate-layout
description: 文档翻译并保持版式：从 docx/pptx 抽取段落级待译工作表（含样式与位置，与源文件 sha256 强绑定），宿主提供译文后回填——完整替换多 run 段落不留原文残留、同页不同 shape 段落 id 唯一不互相覆盖、源已变更的旧工作表被拒绝而非静默套用；术语表校验命中译法，超长译文降字号回退并记录，混合 run 格式降级显式列出，verify 子命令回读核对结构与译文并输出长文本风险清单。当用户要求"翻译这份 Word/PPT 并保持格式/版式不变"时使用。本技能是完整翻译工作流的版式保持与回读验证环节，译文由调用方（宿主模型或人工）产出，翻译质量不在本技能验证范围（无 MT 引擎时标 not_verified），不冒充机器翻译质量。
display_name: 文档译文回填与结构保持
tags: [办公与生产力, 文档处理]
license: Apache-2.0
---

# 文档翻译并保持版式

## 何时使用

- docx/pptx + 目标语言 → 版式不变的译文文档
- 用户要求"翻译并保持格式/别动排版"

## 完整工作流（宿主职责，不能只跑替换器）

本技能是三步确定性流水线；**翻译本身由宿主完成**（模型翻译或人工），宿主必须走完回读检查再交付：

```
# 第 1 步: 抽取待译工作表（与源文件 sha256 绑定）
python scripts/translate_layout.py extract --input source.docx --worksheet ws.json

# 第 2 步: 宿主产出译文
#   - 逐项翻译 ws.json 的 items（id → 译文），遵守术语表用词
#   - 译文写为 tr.json：{"p-0000": "Approved budget", ...}
#     （也接受 [{"id": "p-0000", "translation": "..."}] 列表形态）
#   - 不翻译的项可以不给出：该段保留原文并列入 untranslated_ids，
#     交付前必须逐项确认这是有意的留空而不是漏译

# 第 3 步: 回填译文（保持版式，校验源绑定）
python scripts/translate_layout.py apply --input source.docx --worksheet ws.json \
    --translations tr.json --output translated.docx \
    --glossary terms.json --report report.json

# 第 4 步: 回读验证（结构与译文核对 + 长文本风险清单）
python scripts/translate_layout.py verify --input source.docx --worksheet ws.json \
    --output translated.docx --translations tr.json --report verify-report.json
```

### 宿主交付前检查清单（逐项处理，不得跳过）

1. **未翻译项**：`report.json.untranslated_ids` 必须为空，或逐项确认有意保留原文（页眉页脚常被有意保留，需向用户说明）。
2. **术语检查**：`glossary_misses` 列出源文含术语但译文未用指定译法的项——修改译文重新 apply，直至清零或用户明确接受偏差。
3. **长文本风险回读**：`fallback_list` / `verify-report.json.long_text_risks` 列出译文显著长于原文（默认 >2×，`--overflow-ratio` 可调）的段落——docx 有显式字号的已自动降 1pt（最低 9pt），pptx 与继承字号项仅标记 `needs_attention`，宿主须打开产物检查是否溢出文本框/表格，必要时精简译文或手工调整版式。
4. **混合格式降级**：`format_degradations` 列出原文含多种 run 字符格式（如粗体+普通混排）被合并为首 run 格式的段落——需要保留混排效果时手工重排格式。
5. **结构回读**：`verify` 退出码 0 且 `structure_preserved=true` 才可交付；id/样式/位置/段数变化即结构被破坏，必须排查。

- pptx 同理（按扩展名自动识别）；表格内段落、页眉页脚均在工作表内。

## 保证与输出

- **源绑定**：工作表记录源文件 sha256；`apply`/`verify` 与当前输入不符即拒绝（退出 2，不产出文件），绝不把旧位置译文套到已变更的文档
- **唯一稳定定位**：段落 id 含 slide+shape 标识，同页不同 shape 不共享 id，译文不会互相覆盖
- **完整替换**：多 run 段落回填后清空全部 run 文本，译文后不残留原文；run 级其余元素（样式、表格结构、分节、页眉页脚框架）不动
- **超长回退**：译文长度 > 2× 原文（默认阈值，可调）时 docx 有显式字号的降 1pt（最低 9pt）并记入 `fallback_list`；其余记录 `needs_attention`
- **诚实边界**：`mt_engine: "external"`——译文质量不在本技能验证范围内（无 MT 引擎时翻译质量为 not_verified），报告只保证版式与流程确定性

## 失败与降级

- 输入损坏 / 文件不存在 / 无法解析：退出码 2
- 工作表无绑定信息或与当前源文件不一致：退出码 2，要求重新 extract，不产出输出
- 译文引用工作表中不存在的 id：退出码 2（疑似译错了文档），不产出输出
- 工作表缺某段译文：该段保留原文并列入 `untranslated_ids`，不静默
- pptx 图形/图片内文字不可抽取：不进入工作表（宿主需另行说明未覆盖范围）

## 依赖

Python 3.9+，python-docx / python-pptx（按格式安装，MIT 许可）。
