#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc-sensitive-redact: DOCX/XLSX/PPTX/PDF 敏感信息打码（双轨识别 + 底层删除）。

用法:
  python redact.py --input <file_or_dir> --outdir out/ [--report manifest.json]

退出码: 0=完成(可含 needs_review) 2=输入错误
"""
import argparse
import json
import os
import re
import sys

RE_ID = re.compile(r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9Xx])")
RE_ID_15 = re.compile(r"(?<!\d)[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)")
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_BANK = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
ID_CHECK = "10X98765432"


def id_checksum_ok(v):
    try:
        s = sum(int(v[i]) * ID_WEIGHTS[i] for i in range(17))
        return ID_CHECK[s % 11] == v[17].upper()
    except (ValueError, IndexError):
        return False


def luhn_ok(v):
    digits = [int(c) for c in v]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def find_hits(text):
    """返回 (hits, needs_review)。hits: [{type, value, start, end}] 按位置排序。"""
    hits = []
    review = []
    taken = []
    for m in RE_ID.finditer(text):
        v = m.group(0)
        if id_checksum_ok(v):
            hits.append({"type": "id_card", "value": v, "start": m.start(), "end": m.end()})
            taken.append((m.start(), m.end()))
        else:
            review.append({"type": "id_card_invalid_checksum", "value": v,
                           "start": m.start(), "end": m.end()})
            taken.append((m.start(), m.end()))
    for m in RE_ID_15.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        review.append({"type": "legacy_15_digit", "value": m.group(0),
                       "start": m.start(), "end": m.end()})
        taken.append((m.start(), m.end()))
    for m in RE_PHONE.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        hits.append({"type": "phone", "value": m.group(0), "start": m.start(), "end": m.end()})
        taken.append((m.start(), m.end()))
    for m in RE_BANK.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        if luhn_ok(m.group(0)):
            hits.append({"type": "bank_card", "value": m.group(0),
                         "start": m.start(), "end": m.end()})
        taken.append((m.start(), m.end()))
    for m in RE_EMAIL.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        hits.append({"type": "email", "value": m.group(0), "start": m.start(), "end": m.end()})
    hits.sort(key=lambda h: h["start"], reverse=True)
    review.sort(key=lambda h: h["start"], reverse=True)
    return hits, review


def mask_for(value):
    return "■" * min(len(value), 12)


# ---------- DOCX ----------

def redact_docx(in_path, out_path):
    from docx import Document
    from docx.oxml.ns import qn
    doc = Document(in_path)
    counts = {}
    locations = []
    review_counts = {}
    paras = list(doc.element.body.iter(qn("w:p")))
    for si, section in enumerate(doc.sections):
        for kind in ("header", "footer"):
            try:
                el = getattr(section, kind)._element
            except Exception:
                continue
            if el is not None:
                paras.extend(el.iter(qn("w:p")))
    for pi, p in enumerate(paras):
        runs = []
        for r in p.iter(qn("w:r")):
            parent = r.getparent()
            if parent is not None and parent.tag == qn("w:del"):
                continue
            ts = r.findall(qn("w:t"))
            if ts:
                runs.append((r, ts))
        if not runs:
            continue
        full = "".join("".join(t.text or "" for t in ts) for _, ts in runs)
        hits, review = find_hits(full)
        for h in review:
            review_counts[h["type"]] = review_counts.get(h["type"], 0) + 1
        for h in hits:
            mask = mask_for(h["value"])
            start, end = h["start"], h["end"]
            pos = 0
            first_done = False
            for r, ts in runs:
                rt = "".join(t.text or "" for t in ts)
                rs, re_ = pos, pos + len(rt)
                if re_ <= start or rs >= end:
                    pos = re_
                    continue
                prefix = rt[:max(0, start - rs)]
                suffix = rt[max(0, min(len(rt), end - rs)):]
                new_text = prefix + (mask if not first_done else "") + suffix
                first_done = True
                for t in ts:
                    t.text = ""
                ts[0].text = new_text
                if new_text != new_text.strip() or new_text == "":
                    ts[0].set(qn("xml:space"), "preserve")
                pos = re_
            counts[h["type"]] = counts.get(h["type"], 0) + 1
            locations.append({"type": h["type"], "location": f"paragraph {pi}"})
    doc.save(out_path)
    return {"redactions": [{"type": k, "count": v,
                            "locations": [l["location"] for l in locations if l["type"] == k]}
                           for k, v in counts.items()],
            "needs_review": [{"type": k, "count": v} for k, v in review_counts.items()]}


# ---------- XLSX ----------

def redact_xlsx(in_path, out_path):
    from openpyxl import load_workbook
    wb = load_workbook(in_path)
    counts = {}
    review_counts = {}
    locations = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value:
                    text = cell.value
                    hits, review = find_hits(text)
                    for h in review:
                        review_counts[h["type"]] = review_counts.get(h["type"], 0) + 1
                    for h in hits:
                        text = text[:h["start"]] + mask_for(h["value"]) + text[h["end"]:]
                        counts[h["type"]] = counts.get(h["type"], 0) + 1
                        locations.append({"type": h["type"],
                                          "location": f"{ws.title}!{cell.coordinate}"})
                    if hits:
                        cell.value = text
    wb.save(out_path)
    return {"redactions": [{"type": k, "count": v,
                            "locations": [l["location"] for l in locations if l["type"] == k]}
                           for k, v in counts.items()],
            "needs_review": [{"type": k, "count": v} for k, v in review_counts.items()]}


# ---------- PPTX ----------

def redact_pptx(in_path, out_path):
    from pptx import Presentation
    prs = Presentation(in_path)
    counts = {}
    review_counts = {}
    locations = []
    for si, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                text = "".join(r.text for r in para.runs)
                if not text:
                    continue
                hits, review = find_hits(text)
                for h in review:
                    review_counts[h["type"]] = review_counts.get(h["type"], 0) + 1
                if hits:
                    new_text = text
                    for h in hits:
                        new_text = new_text[:h["start"]] + mask_for(h["value"]) + new_text[h["end"]:]
                        counts[h["type"]] = counts.get(h["type"], 0) + 1
                        locations.append({"type": h["type"], "location": f"slide {si}"})
                    if para.runs:
                        para.runs[0].text = new_text
                        for r in para.runs[1:]:
                            r.text = ""
    prs.save(out_path)
    return {"redactions": [{"type": k, "count": v,
                            "locations": [l["location"] for l in locations if l["type"] == k]}
                           for k, v in counts.items()],
            "needs_review": [{"type": k, "count": v} for k, v in review_counts.items()]}


# ---------- PDF ----------

def redact_pdf(in_path, out_path):
    import pymupdf
    doc = pymupdf.open(in_path)
    counts = {}
    review_counts = {}
    locations = []
    for pno, page in enumerate(doc, start=1):
        text = page.get_text("text")
        hits, review = find_hits(text)
        for h in review:
            review_counts[h["type"]] = review_counts.get(h["type"], 0) + 1
        for h in hits:
            rects = page.search_for(h["value"])
            if not rects:
                review_counts["search_miss_needs_manual"] = review_counts.get(
                    "search_miss_needs_manual", 0) + 1
                continue
            for rect in rects:
                page.add_redact_annot(rect, fill=(0, 0, 0))
            counts[h["type"]] = counts.get(h["type"], 0) + len(rects)
            locations.append({"type": h["type"], "location": f"page {pno}"})
        page.apply_redactions()
    doc.save(out_path, garbage=1)
    doc.close()
    # 回提验证: 原始值必须不再出现
    verify = pymupdf.open(out_path)
    all_text = "".join(p.get_text("text") for p in verify)
    verify.close()
    leaked = False
    for type_ in counts:
        pass
    # 重新检查所有 hits 值
    doc2 = pymupdf.open(in_path)
    for pno, page in enumerate(doc2):
        text = page.get_text("text")
        hits, _ = find_hits(text)
        for h in hits:
            if h["value"] in all_text:
                leaked = True
    doc2.close()
    return {"redactions": [{"type": k, "count": v,
                            "locations": [l["location"] for l in locations if l["type"] == k]}
                           for k, v in counts.items()],
            "needs_review": [{"type": k, "count": v} for k, v in review_counts.items()],
            "pdf_removed_and_verified": not leaked}


def main():
    ap = argparse.ArgumentParser(description="敏感信息打码(DOCX/XLSX/PPTX/PDF)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--report")
    args = ap.parse_args()

    files = []
    if os.path.isdir(args.input):
        for root, _dirs, names in os.walk(args.input):
            for n in names:
                if n.lower().endswith((".docx", ".xlsx", ".pptx", ".pdf")) \
                        and not n.startswith("~$"):
                    files.append(os.path.join(root, n))
    elif os.path.isfile(args.input):
        files = [args.input]
    else:
        print("ERROR: input not found", file=sys.stderr)
        return 2
    if not files:
        print("ERROR: no processable files", file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    results = []
    total = 0
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        stem = os.path.splitext(os.path.basename(f))[0]
        out_path = os.path.join(args.outdir, f"{stem}_REDACTED{ext}")
        try:
            if ext == ".docx":
                r = redact_docx(f, out_path)
            elif ext == ".xlsx":
                r = redact_xlsx(f, out_path)
            elif ext == ".pptx":
                r = redact_pptx(f, out_path)
            else:
                r = redact_pdf(f, out_path)
            r["file"] = f
            r["output"] = out_path
            r["failed"] = False
            total += sum(x["count"] for x in r["redactions"])
        except Exception as e:
            r = {"file": f, "output": None, "failed": True, "error": str(e),
                 "redactions": [], "needs_review": []}
        results.append(r)

    manifest = {"files": results,
                "summary": {"files_processed": len([r for r in results if not r["failed"]]),
                            "files_failed": len([r for r in results if r["failed"]]),
                            "total_redactions": total}}
    report_path = args.report or os.path.join(args.outdir, "redact-manifest.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    return 0 if not all(r["failed"] for r in results) else 2


if __name__ == "__main__":
    sys.exit(main())
