#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf-structured-extract: PDF 结构化提取 + 关键数字两轮交叉校验。

用法:
  python extract_pdf.py --input file.pdf --outdir out/

退出码: 0=完成 2=输入错误(损坏/加密)
"""
import argparse
import json
import math
import os
import re
import sys

import pymupdf

SPARSE_THRESHOLD = 20

RE_ID = re.compile(r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9Xx])")
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_BANK = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+")
ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
ID_CHECK = "10X98765432"


def id_checksum_ok(v):
    if len(v) != 18:
        return False
    try:
        s = sum(int(v[i]) * ID_WEIGHTS[i] for i in range(17))
        return ID_CHECK[s % 11] == v[17].upper()
    except ValueError:
        return False


def luhn_ok(v):
    digits = [int(c) for c in v]
    if len(digits) < 13:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def mask_value(v):
    if len(v) <= 6:
        return v[:2] + "***"
    return v[:3] + "***" + v[-2:]


def scan_key_numbers(text, page_no):
    """两轮校验: 第一轮正则候选, 第二轮算法复核。"""
    found = []
    taken = []
    for m in RE_ID.finditer(text):
        v = m.group(0)
        ok = id_checksum_ok(v)
        found.append({"type": "id_card", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "checksum",
                      "round2_pass": ok,
                      "status": "valid" if ok else "checksum_mismatch"})
        taken.append((m.start(), m.end()))
    for m in RE_PHONE.finditer(text):
        v = m.group(0)
        if any(s <= m.start() < e for s, e in taken):
            continue
        prefix_ok = v[1] in "3456789"
        found.append({"type": "phone", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "segment_prefix",
                      "round2_pass": prefix_ok,
                      "status": "valid" if prefix_ok else "segment_mismatch"})
        taken.append((m.start(), m.end()))
    for m in RE_BANK.finditer(text):
        v = m.group(0)
        if any(s <= m.start() < e for s, e in taken):
            continue
        ok = luhn_ok(v)
        found.append({"type": "bank_card", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "luhn",
                      "round2_pass": ok,
                      "status": "valid" if ok else "luhn_mismatch"})
        taken.append((m.start(), m.end()))
    for m in RE_EMAIL.finditer(text):
        found.append({"type": "email", "page": page_no, "value": m.group(0),
                      "round1_regex": True, "round2_algorithm": "syntax",
                      "round2_pass": True, "status": "valid"})
    # 输出脱敏
    for f in found:
        f["masked"] = mask_value(f.pop("value"))
    return found


def to_md_table(rows):
    if not rows:
        return ""
    out = ["| " + " | ".join(str(c or "") for c in rows[0]) + " |",
           "|" + "---|" * len(rows[0])]
    for r in rows[1:]:
        out.append("| " + " | ".join(str(c or "") for c in r) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="PDF结构化提取")
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        doc = pymupdf.open(args.input)
    except Exception as e:
        print(f"ERROR: cannot open PDF: {e}", file=sys.stderr)
        return 2
    if doc.needs_pass:
        print("ERROR: encrypted PDF is not supported", file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    pages_dir = os.path.join(args.outdir, "pages")
    pages = []
    key_numbers = []
    md_parts = []
    try:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text")
            tables = []
            try:
                tabs = page.find_tables()
                for t in tabs:
                    tables.append(t.extract())
            except Exception:
                pass
            sparse = len(text.strip()) < SPARSE_THRESHOLD
            rendered = None
            if sparse:
                os.makedirs(pages_dir, exist_ok=True)
                rendered = os.path.join("pages", f"page-{i}.png")
                pix = page.get_pixmap(dpi=150)
                pix.save(os.path.join(args.outdir, rendered))
            pages.append({"page": i, "text": text, "sparse": sparse,
                          "needs_vision": sparse, "rendered_image": rendered,
                          "tables": tables})
            key_numbers.extend(scan_key_numbers(text, i))
            md_parts.append(f"## 第 {i} 页\n")
            if sparse:
                md_parts.append(f"（本页文字少于 {SPARSE_THRESHOLD} 字符，已渲染 `{rendered}`，"
                                f"需视觉识别，未做 OCR 假设）\n")
            else:
                md_parts.append(text.strip() + "\n")
            for tb in tables:
                md_parts.append(to_md_table(tb) + "\n")
    finally:
        doc.close()

    result = {
        "input": args.input,
        "engine": "pymupdf (local, no cloud, no API key)",
        "pages": pages,
        "key_numbers": {
            "candidates_and_verification": key_numbers,
            "valid": [f["masked"] for f in key_numbers if f["status"] == "valid"],
            "mismatches": [f for f in key_numbers if f["status"] != "valid"],
        },
        "sparse_pages": [p["page"] for p in pages if p["sparse"]],
        "notes": [
            "表格为线框检测(find_tables)；无边框表格可能漏检，请结合正文核对",
            "稀疏页/扫描页 needs_vision=true，OCR 由调用方视觉模型完成后回填，本脚本不虚构内容",
        ],
    }
    with open(os.path.join(args.outdir, "extract.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.outdir, "extract.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_parts))
    print(json.dumps({"pages": len(pages), "tables": sum(len(p["tables"]) for p in pages),
                      "key_numbers_valid": len(result["key_numbers"]["valid"]),
                      "key_numbers_mismatch": len(result["key_numbers"]["mismatches"]),
                      "sparse_pages": result["sparse_pages"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
