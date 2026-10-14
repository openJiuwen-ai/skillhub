#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf-nl-edit: PDF 指定页局部文字编辑（底层删除+同位插入，增量保存）。

用法:
  python pdf_edit.py --input in.pdf --page 2 --find 旧标题 --replace 新标题 --output out.pdf [--report r.json]

退出码: 0=成功 1=目标文本未找到 2=输入错误(页码越界/损坏/加密/插入失败)
"""
import argparse
import json
import os
import shutil
import sys

import pymupdf


def fit_insert(page, base_rect, text, prefer_size):
    """在 base_rect 处插入文本，字号自适应直到放得下。返回 (ok, fontsize)。"""
    font = "china-s" if any("\u4e00" <= ch <= "\u9fff" for ch in text) else "helv"
    size = prefer_size
    for _ in range(10):
        rect = pymupdf.Rect(base_rect.x0 - 0.5, base_rect.y0 - 1.0,
                            base_rect.x0 + max(base_rect.width, len(text) * size) + 4,
                            base_rect.y1 + 2.0)
        try:
            leftover = page.insert_textbox(rect, text, fontsize=size,
                                           fontname=font, color=(0, 0, 0))
        except Exception:
            return False, size
        if leftover >= 0:
            return True, size
        size -= 1.0
        if size < 5.0:
            # 最后一次尝试: 更大的矩形
            rect = pymupdf.Rect(base_rect.x0 - 0.5, base_rect.y0 - 2.0,
                                base_rect.x0 + max(base_rect.width, len(text) * size) + 20,
                                base_rect.y1 + 6.0)
            try:
                leftover = page.insert_textbox(rect, text, fontsize=max(5.0, size),
                                               fontname=font, color=(0, 0, 0))
                return leftover >= 0, max(5.0, size)
            except Exception:
                return False, size
    return False, size


def main():
    ap = argparse.ArgumentParser(description="PDF局部文字编辑(1基页码)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--page", type=int, required=True, help="1基页码")
    ap.add_argument("--find", required=True)
    ap.add_argument("--replace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report")
    args = ap.parse_args()

    try:
        src = pymupdf.open(args.input)
    except Exception as e:
        print(f"ERROR: cannot open PDF: {e}", file=sys.stderr)
        return 2
    if src.needs_pass:
        print("ERROR: encrypted PDF", file=sys.stderr)
        return 2
    if args.page < 1 or args.page > src.page_count:
        print(f"ERROR: page {args.page} out of range (1..{src.page_count})", file=sys.stderr)
        src.close()
        return 2
    pno = args.page - 1

    before_texts = [src[i].get_text("text") for i in range(src.page_count)]
    rects = src[pno].search_for(args.find)
    if not rects:
        result = {"status": "not_found", "page": args.page, "find": args.find}
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        src.close()
        return 1
    src.close()

    # 复制后编辑（增量保存保留未编辑对象原始字节）
    shutil.copyfile(args.input, args.output)
    doc = pymupdf.open(args.output)
    page = doc[pno]
    for rect in rects:
        page.add_redact_annot(rect, fill=(1, 1, 1))
    page.apply_redactions()
    base = rects[0]
    prefer = max(6.0, min(24.0, base.height * 0.75))
    ok, fontsize = fit_insert(page, base, args.replace, prefer)
    if not ok:
        doc.close()
        shutil.copyfile(args.input, args.output)  # 不留半成品
        print("ERROR: cannot insert replacement text (font fit failed)", file=sys.stderr)
        return 2
    try:
        doc.save(args.output, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
    except Exception:
        doc.save(args.output + ".tmp", garbage=1)
        doc.close()
        os.replace(args.output + ".tmp", args.output)
        doc = pymupdf.open(args.output)
    after_texts = [doc[i].get_text("text") for i in range(doc.page_count)]
    unchanged = all(after_texts[i] == before_texts[i]
                    for i in range(len(before_texts)) if i != pno)
    doc.close()

    report = {
        "input": args.input,
        "output": args.output,
        "page": args.page,
        "page_base": "1-based (documented; no trial-and-error)",
        "edits": [{"page": args.page, "before": args.find, "after": args.replace,
                   "rect": [round(v, 1) for v in base], "fontsize": round(fontsize, 1)}],
        "occurrences_replaced": len(rects),
        "font_note": "插入使用内置字体(helv/china-s)，与原文档嵌入字体可能不一致",
        "other_pages_unchanged": unchanged,
        "target_page_contains_new_text": args.replace in after_texts[pno],
        "target_page_contains_old_text": args.find in after_texts[pno],
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    if not (unchanged and report["target_page_contains_new_text"]
            and not report["target_page_contains_old_text"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
