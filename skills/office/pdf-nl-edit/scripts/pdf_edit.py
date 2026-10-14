#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf-nl-edit: PDF 指定页局部文字编辑（底层删除 + 同位插入）。

语义：默认把指定页内**所有** `--find` 命中全部替换为 `--replace`，
并逐处插入新文本；不会“删多处只插一处”。命中数为 0 时退出 1 且不产出文件。

用法:
  python pdf_edit.py --input in.pdf --page 2 --find 旧标题 --replace 新标题 \
      --output out.pdf [--report r.json] [--purge]

退出码:
  0 = 成功
  1 = 目标文本未找到（不产出输出文件）
  2 = 输入/参数错误（页码越界/损坏/加密/同输入输出路径/替换文本放不下/覆盖邻近内容/写入失败）

安全性说明: 普通编辑使用增量保存，会保留旧对象版本，**不构成可恢复的敏感原文删除**；
如需不可恢复重写请使用 `--purge`（仍非取证级安全擦除），或使用 doc-sensitive-redact。
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

import pymupdf

PAGE_MARGIN = 6.0
MIN_FONTSIZE = 4.0


def _has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _measure(font, size, text):
    return pymupdf.get_text_length(text, fontname=font, fontsize=size)


def _rect_close(a, b, tol=0.5):
    return (abs(a.x0 - b.x0) < tol and abs(a.y0 - b.y0) < tol
            and abs(a.x1 - b.x1) < tol and abs(a.y1 - b.y1) < tol)


def _dedupe_rects(rects):
    out = []
    for r in rects:
        if not any(_rect_close(r, u) for u in out):
            out.append(r)
    return out


def _overlaps_other(page, box, ignore_rects, replace_text):
    """插入框是否覆盖被替换文本之外的既有内容。"""
    for w in page.get_text("words"):
        wr = pymupdf.Rect(w[:4])
        if wr.is_empty:
            continue
        if replace_text in w[4]:
            continue
        if any(wr.intersects(r) for r in ignore_rects):
            continue
        if box.intersects(wr):
            return True
    return False


def plan_insert(page, base_rect, text, ignore_rects):
    """选取不越页且尽量不覆盖邻近内容的字号与插入框。

    返回 (ok, fontsize, box, overlapped, reason)
    - ok=True: box 可用
    - ok=False, overlapped=True: 能放进页面但会覆盖邻近内容
    - ok=False, overlapped=False: 即使最小字号也放不下（越页）
    """
    font = "china-s" if _has_cjk(text) else "helv"
    pr = page.rect
    max_right = pr.x1 - PAGE_MARGIN
    max_bottom = pr.y1 - PAGE_MARGIN
    prefer = max(6.0, min(24.0, base_rect.height * 0.75))
    size = prefer
    last_within = None
    while size >= MIN_FONTSIZE - 1e-9:
        w = _measure(font, size, text)
        box = pymupdf.Rect(base_rect.x0 - 0.5, base_rect.y0 - 1.0,
                           base_rect.x0 - 0.5 + max(w, base_rect.width) + 2.0,
                           base_rect.y1 + 2.0)
        within = (box.x0 >= 0 and box.x1 <= max_right
                  and box.y0 >= 0 and box.y1 <= max_bottom)
        if within:
            last_within = (size, box)
            if not _overlaps_other(page, box, ignore_rects, text):
                return True, size, box, False, ""
        size -= 0.5
    if last_within is not None:
        return False, last_within[0], last_within[1], True, "replacement would overlap neighbouring content"
    return False, MIN_FONTSIZE, None, False, "replacement text cannot fit on the page without overflowing"


def _write_report(path, obj):
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="PDF局部文字编辑(1基页码; 默认替换该页全部命中)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--page", type=int, required=True, help="1基页码")
    ap.add_argument("--find", required=True)
    ap.add_argument("--replace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report")
    ap.add_argument("--purge", action="store_true",
                    help="整文件重写(garbage=4)以清除旧对象版本; 仍非取证级擦除")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2
    if os.path.realpath(args.input) == os.path.realpath(args.output):
        print("ERROR: --input and --output must differ (would corrupt source)", file=sys.stderr)
        return 2
    outdir = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(outdir):
        print(f"ERROR: output directory does not exist: {outdir}", file=sys.stderr)
        return 2

    try:
        src = pymupdf.open(args.input)
    except Exception as e:
        print(f"ERROR: cannot open PDF: {e}", file=sys.stderr)
        return 2
    if src.needs_pass:
        print("ERROR: encrypted PDF", file=sys.stderr)
        src.close()
        return 2
    if args.page < 1 or args.page > src.page_count:
        print(f"ERROR: page {args.page} out of range (1..{src.page_count})", file=sys.stderr)
        src.close()
        return 2
    pno = args.page - 1

    before_texts = [src[i].get_text("text") for i in range(src.page_count)]
    rects = _dedupe_rects(src[pno].search_for(args.find))
    if not rects:
        src.close()
        result = {"status": "not_found", "page": args.page, "find": args.find,
                  "output": args.output, "occurrences_found": 0}
        _write_report(args.report, result)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    # 先在真实页面上规划每处插入框（需原始 words 判定邻近内容）
    plans = []
    for r in rects:
        ok, size, box, overlapped, reason = plan_insert(src[pno], r, args.replace, rects)
        if not ok:
            src.close()
            result = {"status": "error", "page": args.page, "find": args.find,
                      "output": args.output, "reason": reason,
                      "occurrences_found": len(rects)}
            _write_report(args.report, result)
            print(f"ERROR: {reason}", file=sys.stderr)
            return 2
        plans.append((r, size, box))
    src.close()

    # 复制到临时文件编辑；只有全部成功且校验通过后才原子替换到输出，
    # 失败时绝不动既有输出文件。
    fd, tmp = tempfile.mkstemp(prefix=".pdfedit-", suffix=".pdf", dir=outdir)
    os.close(fd)
    try:
        shutil.copyfile(args.input, tmp)
        doc = pymupdf.open(tmp)
        page = doc[pno]
        for r, _size, _box in plans:
            page.add_redact_annot(r, fill=(1, 1, 1))
        page.apply_redactions()
        font = "china-s" if _has_cjk(args.replace) else "helv"
        for r, size, box in plans:
            leftover = page.insert_textbox(box, args.replace, fontsize=size,
                                           fontname=font, color=(0, 0, 0))
            if leftover < 0:
                doc.close()
                print("ERROR: cannot insert replacement text (font fit failed)", file=sys.stderr)
                return 2
        mode = "purge" if args.purge else "incremental"
        if args.purge:
            # 整文件重写必须写到他处再原子替换（不能原地全量保存）
            doc.save(tmp + ".purge", garbage=4, deflate=True,
                     encryption=pymupdf.PDF_ENCRYPT_KEEP)
            doc.close()
            os.replace(tmp + ".purge", tmp)
        else:
            try:
                doc.save(tmp, incremental=True, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                doc.close()
            except Exception:
                doc.save(tmp + ".full", garbage=1, encryption=pymupdf.PDF_ENCRYPT_KEEP)
                doc.close()
                os.replace(tmp + ".full", tmp)
                mode = "full_fallback"

        # 从落盘文件独立回读校验
        vdoc = pymupdf.open(tmp)
        after_texts = [vdoc[i].get_text("text") for i in range(vdoc.page_count)]
        edits = []
        targets_ok = True
        for r, size, box in plans:
            region = vdoc[pno].get_text("text", clip=pymupdf.Rect(
                box.x0 - 2, box.y0 - 2, box.x1 + 2, box.y1 + 2))
            if args.replace not in region:
                targets_ok = False
            edits.append({"page": args.page, "before": args.find, "after": args.replace,
                          "rect": [round(v, 1) for v in r], "fontsize": round(size, 1)})
        vdoc.close()

        unchanged = all(after_texts[i] == before_texts[i]
                        for i in range(len(before_texts)) if i != pno)
        report = {
            "input": args.input,
            "output": args.output,
            "page": args.page,
            "page_base": "1-based (documented; no trial-and-error)",
            "edits": edits,
            "occurrences_found": len(plans),
            "occurrences_replaced": len(plans),
            "font_note": "插入使用内置字体(helv/china-s)，与原文档嵌入字体可能不一致",
            "other_pages_unchanged": unchanged,
            "target_page_contains_new_text": args.replace in after_texts[pno],
            "target_page_contains_old_text": args.find in after_texts[pno],
            "replace_all_occurrences": True,
            "mode": mode,
            "irreversible": mode in ("purge", "full_fallback"),
            "security_note": ("增量保存会保留旧对象版本，不构成不可恢复的敏感原文删除；"
                              "不可逆脱敏请使用 doc-sensitive-redact"),
        }
        ok = (unchanged and targets_ok and report["target_page_contains_new_text"]
              and not report["target_page_contains_old_text"])
        _write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False))
        if not ok:
            return 1
        os.replace(tmp, args.output)
        tmp = None
        return 0
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
