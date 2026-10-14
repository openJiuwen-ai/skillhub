#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deck-delivery-qa: PPTX 确定性几何 lint（文字溢出估算/越界/空占位符/利用率）。

用法:
  python deck_qa.py --input deck.pptx --report qa-report.json

退出码: 0=全部合格 1=存在不合格页 2=文件损坏
"""
import argparse
import json
import math
import sys

from pptx import Presentation
from pptx.util import Emu

EMU_PER_PT = 12700
EMU_PER_INCH = 914400
DEFAULT_FONT_PT = 18.0
LINE_SPACING = 1.25
TOLERANCE = 1.05


def est_text_width_pt(text, font_pt):
    """CJK≈1.0×字号, ASCII≈0.55×字号（pt）。"""
    w = 0.0
    for ch in text:
        if "\u4e00" <= ch <= "\u9fff" or ch in "，。：；！？（）【】、—…“”‘’":
            w += font_pt
        else:
            w += font_pt * 0.55
    return w


def para_font_pt(para):
    for run in para.runs:
        if run.font.size is not None:
            return run.font.size.pt
    return DEFAULT_FONT_PT


def est_shape_text_height_pt(shape):
    """估算文本框所需高度（pt）。返回 (est_height, font_pt, n_paras)。"""
    if not shape.has_text_frame:
        return 0.0, DEFAULT_FONT_PT, 0
    tf = shape.text_frame
    usable_w_pt = max(10.0, shape.width / EMU_PER_PT - 0.3 * 72)  # 扣两侧默认内边距
    total_h = 0.0
    font_pt = DEFAULT_FONT_PT
    n = 0
    for para in tf.paragraphs:
        text = "".join(run.text for run in para.runs)
        fp = para_font_pt(para)
        font_pt = fp
        if not text:
            total_h += fp * 0.6
            n += 1
            continue
        n += 1
        need_w = est_text_width_pt(text, fp)
        lines = max(1, math.ceil(need_w / usable_w_pt))
        total_h += lines * fp * LINE_SPACING
    return total_h, font_pt, n


def check_slide(slide, slide_w_emu, slide_h_emu):
    issues = []
    used_area = 0
    tol_emu = int(0.02 * EMU_PER_INCH)
    for shape in slide.shapes:
        try:
            left, top = shape.left, shape.top
            width, height = shape.width, shape.height
        except Exception:
            continue
        if left is None or top is None or width is None or height is None:
            continue
        in_bounds = (left >= -tol_emu and top >= -tol_emu and
                     left + width <= slide_w_emu + tol_emu and
                     top + height <= slide_h_emu + tol_emu)
        if not in_bounds:
            issues.append({
                "check": "shape_out_of_bounds",
                "shape": shape.name,
                "detail": f"left={left / EMU_PER_INCH:.2f}in top={top / EMU_PER_INCH:.2f}in "
                          f"right={(left + width) / EMU_PER_INCH:.2f}in "
                          f"bottom={(top + height) / EMU_PER_INCH:.2f}in "
                          f"canvas={slide_w_emu / EMU_PER_INCH:.2f}x{slide_h_emu / EMU_PER_INCH:.2f}in"})
        # 利用率: 页内形状面积
        if in_bounds:
            used_area += max(0, width) * max(0, height)
        if shape.has_text_frame:
            tf = shape.text_frame
            text_all = tf.text.strip()
            if not text_all:
                if shape.is_placeholder:
                    issues.append({"check": "empty_placeholder", "shape": shape.name,
                                   "detail": "占位符无内容"})
                continue
            est_h, font_pt, n_paras = est_shape_text_height_pt(shape)
            avail_h_pt = height / EMU_PER_PT - 0.1 * 72  # 扣上下内边距
            if est_h > avail_h_pt * TOLERANCE:
                issues.append({
                    "check": "text_overflow",
                    "shape": shape.name,
                    "detail": f"估算需要 {est_h:.0f}pt 高（{n_paras}段, 主字号{font_pt:.0f}pt）, "
                              f"可用 {avail_h_pt:.0f}pt"})
    utilization = round(used_area / (slide_w_emu * slide_h_emu), 4) if slide_w_emu else 0.0
    return issues, utilization


def main():
    ap = argparse.ArgumentParser(description="PPTX交付质检")
    ap.add_argument("--input", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    try:
        prs = Presentation(args.input)
    except Exception as e:
        print(f"ERROR: cannot open pptx: {e}", file=sys.stderr)
        return 2
    slide_w = prs.slide_width
    slide_h = prs.slide_height
    slides_out = []
    any_fail = False
    for idx, slide in enumerate(prs.slides, start=1):
        issues, util = check_slide(slide, slide_w, slide_h)
        if any(i["check"] in ("text_overflow", "shape_out_of_bounds") for i in issues):
            any_fail = True
        slides_out.append({"index": idx, "issues": issues, "utilization": util,
                           "pass": not any(i["check"] in ("text_overflow", "shape_out_of_bounds")
                                           for i in issues)})
    report = {
        "input": args.input,
        "mode": "estimated_geometry",
        "rendered": False,
        "render_note": "本环境无渲染引擎（LibreOffice/soffice 未安装），几何检查基于文本度量"
                       "的确定性估算；渲染级检查未执行，不冒充渲染结果",
        "slides": slides_out,
        "pass": not any_fail,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"pass": report["pass"],
                      "slides": len(slides_out),
                      "failing_slides": [s["index"] for s in slides_out if not s["pass"]]},
                     ensure_ascii=False))
    return 0 if not any_fail else 1


if __name__ == "__main__":
    sys.exit(main())
