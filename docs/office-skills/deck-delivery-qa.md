# deck-delivery-qa 交付说明

对 `.pptx` 做交付前质检：逐页确定性几何 lint——文字溢出（估算所需高度 vs 形状可用高度）、形状越界、空占位符、内容区利用率，并识别 0 页空演示。输出逐页 JSON 报告，存在不合格页或空演示时非零退出。

## 能力边界

| 场景 | 是否支持 |
|---|---|
| 已生成 .pptx 的交付前几何检查 | 是（核心场景） |
| 段内混合字号（如 10pt 前缀 + 72pt 正文）的文本溢出 | 是：逐 run 用各自字号累加宽度、按段内最大字号计行高 |
| run 无显式字号、由段落 defRPr（`paragraph.font.size`）继承的字号 | 是：`run.font.size` 缺失时回退到段落默认字号 |
| 文本框折行估算（CJK≈1.0×字号，ASCII≈0.55×字号，扣左右内边距） | 是 |
| 形状越界（超出页面画布，含 0.02in 容差） | 是：`shape_out_of_bounds` |
| 空占位符（placeholder 无文本） | 是：`empty_placeholder` |
| 内容区利用率（页内形状面积和 / 页面面积） | 是：数值写入报告，可独立复算 |
| 0 页空演示（合法容器、无幻灯片） | 是：`deck_issues: [empty_deck]`，判不可交付、非零退出 |
| 渲染引擎探测 | 是：探测 `soffice`/`libreoffice`/`soffice.exe`，写入 `render_engine_detected`，不硬写"无引擎" |
| 渲染级像素检查（导出图片核对遮挡/像素溢出） | 否：脚本不做自动渲染，`rendered` 恒为 false；有引擎时另行导出图片检查 |
| 内容质量评审（逻辑/美观/文案） | 否：只做几何与完整性确定性判定，内容评审由调用方完成 |
| 图表/动画/媒体对象内部渲染检查 | 否：仅对外层形状边界与文本度量做检查 |

## 安装与依赖

- Python 3.9+
- python-pptx（MIT）
- 标准库：argparse / json / math / shutil / sys

技能包结构：

```text
deck-delivery-qa/
├── SKILL.md
└── scripts/deck_qa.py
```

## 用法

```bash
python scripts/deck_qa.py --input deck.pptx --report qa-report.json
```

## 输出

- `qa-report.json`
  - `mode`：恒为 `"estimated_geometry"`
  - `rendered`：恒为 `false`（估算不等于渲染）
  - `render_engine_detected`：探测到的 soffice/libreoffice 路径，未探测到为 `null`
  - `render_note`：据实描述估算与渲染的关系
  - `slide_count`：页数
  - `deck_issues`：报告级问题（如 `empty_deck`）
  - `slides[]`：每页 `index` / `issues[]`（`text_overflow` / `shape_out_of_bounds` / `empty_placeholder`）/ `utilization` / `pass`
  - `pass`：无不合格 issue 且无 `deck_issues` 时为 true
- stdout：JSON 汇总（pass / slides / deck_issues / failing_slides）

## 退出码

- 0：全部合格
- 1：存在不合格页或空演示
- 2：文件损坏/无法打开

## 验收依据与测试

对应基线 M03（PPT 修改与交付检查：逐页给出溢出元素与利用率数值、不合格页判不合格、缺渲染环境时标注未渲染）与补充基准 S03（质检侧：页数与数字核对、渲染检查溢出/遮挡方向；新建/局部修改归 pptx-craft）。黑盒测试位于 `tests/office_skills/test_deck_delivery_qa.py`（8 项，独立推导预期值，不 mock 实现）：

1. 正常：短标题在充裕框内 → 通过、退出 0、`rendered=false`、`mode=estimated_geometry`、利用率数值
2. 复杂：10pt 前缀 + 20 个 72pt 汉字（5in×1in）→ `text_overflow`，detail 含最大字号 72pt、估算高 450pt；只取首个显式字号 run（10pt）会漏报
3. 复杂：run 无显式字号、段落 defRPr=72pt → `text_overflow`；把 `run.font.size=None` 当默认 18pt 会漏报
4. 异常：形状越界（right=14in > 10in 画布）→ `shape_out_of_bounds`、退出 1
5. 复杂：利用率独立复算（2in×1in 矩形 / 10in×7.5in 画布 = 0.0267）
6. 异常：0 页空演示 → `deck_issues=[empty_deck]`、退出 1、不声称可交付
7. 异常：损坏 pptx → 退出 2
8. 复杂：两次运行报告逐字节一致（确定性）；`render_note` 含"渲染"、`rendered=false`

在仓库根目录运行：

```bash
python -m pytest tests/office_skills/test_deck_delivery_qa.py --basetemp <任务目录下唯一临时目录>
```

修复记录（2026-10-14，相对初版）：主控缺陷探针 `benchmarks/deck-complex` 冻结四例，初版对 `mixed-font`、`paragraph-font`、`empty-deck` 全部误报通过（`reported_pass=true`）。根因：`para_font_pt` 只返回"第一个有显式字号的 run"，导致 (a) 混合字号段整体按首个 run 字号（10pt）低估宽度；(b) 段落级 `paragraph.font.size` 被忽略，`run.font.size=None` 时回退到默认 18pt 而非 72pt。修复：改为逐 run 取生效字号（run→段落 defRPr→默认）累加宽度、按段内最大字号计行高。同时 (c) 0 页演示此前 `slides=[]`、`any_fail=False` 被误判可交付，现增加报告级 `deck_issues`。另按用户要求 (d) 不再硬写"本环境无渲染引擎"，改为真实探测 soffice/libreoffice 并写入 `render_engine_detected`。对保留的原始三个 PPTX 用修复后代码复跑，结果与冻结 `expected.json` 一致（normal 通过；mixed-font/paragraph-font 溢出；empty-deck 不可交付）。

未测项（不宣称通过）：渲染级像素检查（脚本不自动渲染，未导出图片核对）；豆包端同题竞品验收（`parity_status=not_verified`，未伪造）；大量形状/组合/图表的复杂版式覆盖率。

## 来源与许可

原创实现（`decision-register.json`: independent_implement；无外部代码复制；竞品 doubao/ppt 1.0.29 与 workbuddy slides-maker/powerpoint-pptx 均 `no_license_found`，仅功能对标未搬运）。python-pptx 1.0.2（MIT）为运行时依赖，安装于任务 venv，未全局安装。
