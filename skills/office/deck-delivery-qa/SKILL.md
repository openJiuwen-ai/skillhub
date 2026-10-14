---
name: deck-delivery-qa
description: 演示文稿交付质检：对 .pptx 做确定性几何 lint——逐页检测文字溢出（逐 run 按各自字号×字符宽估算 CJK/ASCII 文本高度 vs 形状可用高度，兼容段内混合字号与段落继承字号）、形状越界（超出画布）、空占位符，并计算内容区利用率；识别 0 页空演示并判不可交付；输出逐页 JSON 质检报告，存在不合格页或空演示时非零退出。对渲染引擎做真实探测并在报告中如实标注（rendered=false 时说明是估算而非渲染），不硬写"环境无引擎"。当用户要求"检查这份 PPT 能不能交付/有没有文字溢出/排版问题"时使用。
---

# 演示文稿交付质检（确定性 lint）

## 何时使用

- 已生成的 .pptx → 交付质检报告（能不能发出去）
- 用户问"帮我检查这份 PPT 有没有问题/文字会不会溢出"

## 用法

```
python scripts/deck_qa.py --input deck.pptx --report qa-report.json
```

## 检查项（逐页，确定性）

1. **文字溢出**：按文本度量的确定性估算——**逐 run** 用各 run 的生效字号（`run.font.size` → 段落默认字号 `paragraph.font.size` → 18pt）累加文字宽度（CJK≈1.0×字号，ASCII≈0.55×字号），按该段最大字号计行高，折行后估算所需高度与形状可用高度比较，超出即 `text_overflow`。段内混合字号（如 10pt 前缀 + 72pt 正文）与段落级 defRPr 继承字号均正确计入，不只取"第一个有显式字号的 run"
2. **形状越界**：形状边界超出页面画布 → `shape_out_of_bounds`
3. **空占位符**：占位符形状无文本 → `empty_placeholder`
4. **空演示**：合法 PPTX 容器但 0 页 → 报告级 `deck_issues: [empty_deck]`，判定不可交付
5. **内容区利用率**：页内形状面积和 / 页面面积（数值写入报告）

## 输出（qa-report.json）

```json
{
  "mode": "estimated_geometry",
  "rendered": false,
  "render_engine_detected": null,
  "render_note": "未检测到 soffice/libreoffice 渲染引擎；几何判定基于文本度量的确定性估算，渲染级检查未执行，不冒充渲染结果",
  "slide_count": 1,
  "deck_issues": [],
  "slides": [{"index": 1, "issues": [{"check": "text_overflow", "shape": "标题 3",
                                      "detail": "估算需要 450pt 高（1段, 主字号72pt）, 可用 65pt"}],
              "utilization": 0.62, "pass": false}],
  "pass": false
}
```

- 有任何判定不合格的 issue 或 `deck_issues` → 退出码 1；全部通过 → 0；文件损坏 → 2
- 同输入两次运行结果一致（确定性）

## 诚实边界

- 估算模式不等于渲染结果；`rendered` 恒为 false，`render_engine_detected` 记录实际探测到的 soffice/libreoffice 路径（未探测到则 null），`render_note` 据实描述；不把"未执行渲染检查"谎报成"本环境没有引擎"
- 有渲染环境时，渲染级像素检查需另行执行（导出图片），本脚本不自动渲染
- 不评审内容质量（逻辑/美观），只做几何与完整性确定性检查；内容评审由调用方完成

## 依赖

Python 3.9+，python-pptx（`pip install python-pptx`）。
