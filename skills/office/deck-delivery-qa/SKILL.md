---
name: deck-delivery-qa
description: 演示文稿交付质检：对 .pptx 做确定性几何 lint——逐页检测文字溢出（按字号×字符宽估算 CJK/ASCII 文本高度 vs 形状可用高度）、形状越界（超出画布）、空占位符，并计算内容区利用率；输出逐页 JSON 质检报告，存在不合格页时非零退出。当用户要求"检查这份 PPT 能不能交付/有没有文字溢出/排版问题"时使用。本环境无渲染引擎时以估算模式运行并显式标注 rendered=false，不伪装已渲染。
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

1. **文字溢出**：按文本度量的确定性估算——每个文本框内段落按字号与字符宽度（CJK≈1.0×字号，ASCII≈0.55×字号）折行，估算所需高度与形状可用高度比较，超出即 `text_overflow`
2. **形状越界**：形状边界超出页面画布 → `shape_out_of_bounds`
3. **空占位符**：占位符形状无文本 → `empty_placeholder`
4. **内容区利用率**：页内形状面积和 / 页面面积（数值写入报告）

## 输出（qa-report.json）

```json
{
  "mode": "estimated_geometry",
  "rendered": false,
  "note": "本环境无渲染引擎，几何检查基于文本度量确定性估算；渲染级检查未执行",
  "slides": [{"index": 1, "issues": [{"check": "text_overflow", "shape": "标题 3",
                                      "detail": "估算需要 132pt 高, 可用 68pt"}],
              "utilization": 0.62}],
  "pass": false
}
```

- 有任何 issue → 退出码 1；全部通过 → 0；文件损坏 → 2
- 同输入两次运行结果一致（确定性）

## 诚实边界

- 估算模式不等于渲染结果；标注 `rendered: false`。有渲染环境（LibreOffice/soffice）时优先导出图片做像素级检查，并在报告中改为 `rendered: true`
- 不评审内容质量（逻辑/美观），只做几何与完整性确定性检查；内容评审由调用方完成

## 依赖

Python 3.9+，python-pptx（`pip install python-pptx`）。
