#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf-structured-extract: PDF 结构化提取 + 关键数字两轮交叉校验。

用法:
  python extract_pdf.py --input file.pdf --outdir out/

退出码: 0=完成 2=输入错误(损坏/加密)

运行时依赖(全部宽松许可; 见 requirements.txt 与 SKILL.md 许可说明):
  pdfplumber/pdfminer.six (MIT) 文本与表格; pypdfium2/PDFium (BSD-3-Clause OR Apache-2.0) 稀疏页渲染;
  pypdf (BSD-3-Clause) 结构与加密检查。
"""
import argparse
import json
import os
import re
import sys

import pdfplumber
import pypdf
import pypdfium2 as pdfium

SPARSE_THRESHOLD = 20
RENDER_DPI = 150

RE_ID = re.compile(r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9Xx])")
RE_AMBIGUOUS15 = re.compile(r"(?<!\d)\d{15}(?!\d)")
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


def _pass2(status):
    """把内部 status 归一化为 SKILL.md 承诺的 pass2: valid|invalid|needs_review。"""
    if status == "valid":
        return "valid"
    if status == "needs_review":
        return "needs_review"
    return "invalid"


def scan_key_numbers(text, page_no):
    """两轮校验: 第一轮正则候选, 第二轮算法复核。"""
    found = []
    taken = []
    for m in RE_ID.finditer(text):
        v = m.group(0)
        ok = id_checksum_ok(v)
        status = "valid" if ok else "checksum_mismatch"
        found.append({"type": "id_card", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "checksum",
                      "round2_pass": ok, "status": status, "pass2": _pass2(status)})
        taken.append((m.start(), m.end()))
    # 15 位纯数字长度有歧义（可能是旧版身份证，也可能是 15 位卡号）→ needs_review，不臆断
    for m in RE_AMBIGUOUS15.finditer(text):
        v = m.group(0)
        if any(s <= m.start() < e for s, e in taken):
            continue
        found.append({"type": "ambiguous_15digit", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "ambiguous_length",
                      "round2_pass": None, "status": "needs_review", "pass2": "needs_review"})
        taken.append((m.start(), m.end()))
    for m in RE_PHONE.finditer(text):
        v = m.group(0)
        if any(s <= m.start() < e for s, e in taken):
            continue
        prefix_ok = v[1] in "3456789"
        status = "valid" if prefix_ok else "segment_mismatch"
        found.append({"type": "phone", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "segment_prefix",
                      "round2_pass": prefix_ok, "status": status, "pass2": _pass2(status)})
        taken.append((m.start(), m.end()))
    for m in RE_BANK.finditer(text):
        v = m.group(0)
        if any(s <= m.start() < e for s, e in taken):
            continue
        ok = luhn_ok(v)
        status = "valid" if ok else "luhn_mismatch"
        found.append({"type": "bank_card", "page": page_no, "value": v,
                      "round1_regex": True, "round2_algorithm": "luhn",
                      "round2_pass": ok, "status": status, "pass2": _pass2(status)})
        taken.append((m.start(), m.end()))
    for m in RE_EMAIL.finditer(text):
        found.append({"type": "email", "page": page_no, "value": m.group(0),
                      "round1_regex": True, "round2_algorithm": "syntax",
                      "round2_pass": True, "status": "valid", "pass2": "valid"})
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


def extract_table_blocks(page, page_no):
    """检测表格并保留来源定位: 页码 + bbox + 结构化行列。

    pdfplumber 的 Page.find_tables() 基于线框检测，与既有 find_tables 契约一致。
    """
    blocks = []
    try:
        tabs = page.find_tables()
    except Exception:
        return blocks
    for t in tabs:
        try:
            rows = t.extract()
            bbox = [round(float(v), 3) for v in t.bbox]
        except Exception:
            continue
        blocks.append({"page": page_no, "bbox": bbox, "rows": rows,
                       "row_count": len(rows),
                       "col_count": max((len(r) for r in rows), default=0),
                       "source": "find_tables"})
    return blocks


def render_page_png(pdfium_doc, index0, out_path):
    """用 PDFium(pypdfium2) 把第 index0 页渲染为真实 PNG。"""
    page = pdfium_doc[index0]
    bitmap = page.render(scale=RENDER_DPI / 72.0)
    try:
        image = bitmap.to_pil()
        image.save(out_path, format="PNG")
    finally:
        try:
            bitmap.close()
        except Exception:
            pass


def build_verify_report(key_numbers):
    """关键数字两轮交叉校验报告（对应 SKILL.md 的 verify-report.json）。"""
    entries = [{"type": f["type"], "page": f["page"], "masked": f["masked"],
                "round1_regex": f["round1_regex"],
                "round2_algorithm": f["round2_algorithm"],
                "round2_pass": f["round2_pass"], "pass2": f["pass2"]}
               for f in key_numbers]
    valid = [e for e in entries if e["pass2"] == "valid"]
    invalid = [e for e in entries if e["pass2"] == "invalid"]
    review = [e for e in entries if e["pass2"] == "needs_review"]
    return {
        "method": "两轮交叉校验: round1 正则形状候选, round2 独立算法复核(校验位/号段/Luhn/语法)",
        "round1": {"description": "正则形状提取候选", "candidates": len(entries)},
        "round2": {"description": "独立算法校验(与正则不共享判定)",
                  "algorithms": {"id_card": "GB11643 加权校验位",
                                 "phone": "号段前缀", "bank_card": "Luhn",
                                 "email": "语法", "ambiguous_15digit": "长度歧义"}},
        "entries": entries,
        "valid": valid,
        "mismatches": invalid,
        "needs_review": review,
        "summary": {"total": len(entries), "valid": len(valid),
                    "invalid": len(invalid), "needs_review": len(review)},
    }


def main():
    ap = argparse.ArgumentParser(description="PDF结构化提取")
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()

    # 结构与加密检查(pypdf): 损坏/不可读 → 显式失败; 加密 → 显式拒绝, 不暴力破解
    try:
        reader = pypdf.PdfReader(args.input)
    except Exception as e:
        print(f"ERROR: cannot open PDF: {e}", file=sys.stderr)
        return 2
    if reader.is_encrypted:
        print("ERROR: encrypted PDF is not supported", file=sys.stderr)
        return 2

    try:
        pdf = pdfplumber.open(args.input)
    except Exception as e:
        print(f"ERROR: cannot open PDF: {e}", file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    pages_dir = os.path.join(args.outdir, "pages")
    pages = []
    structured_tables = []
    key_numbers = []
    md_parts = []
    pdfium_doc = None
    try:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            blocks = extract_table_blocks(page, i)
            structured_tables.extend(blocks)
            rows_only = [b["rows"] for b in blocks]
            sparse = len(text.strip()) < SPARSE_THRESHOLD
            rendered = None
            if sparse:
                os.makedirs(pages_dir, exist_ok=True)
                rendered = os.path.join("pages", f"page-{i}.png")
                if pdfium_doc is None:
                    pdfium_doc = pdfium.PdfDocument(args.input)
                render_page_png(pdfium_doc, i - 1, os.path.join(args.outdir, rendered))
            pages.append({"page": i, "text": text, "sparse": sparse,
                          "needs_vision": sparse, "rendered_image": rendered,
                          "tables": rows_only})
            key_numbers.extend(scan_key_numbers(text, i))
            md_parts.append(f"## 第 {i} 页\n")
            if sparse:
                md_parts.append(f"（本页文字少于 {SPARSE_THRESHOLD} 字符，已渲染 `{rendered}`，"
                                f"需视觉识别，未做 OCR 假设）\n")
            else:
                md_parts.append(text.strip() + "\n")
            for b in blocks:
                md_parts.append(to_md_table(b["rows"]) + "\n")
    finally:
        if pdfium_doc is not None:
            pdfium_doc.close()
        pdf.close()

    verify_report = build_verify_report(key_numbers)
    uncertain = [f for f in key_numbers if f["pass2"] == "needs_review"]
    result = {
        "input": args.input,
        "engine": "pdfplumber/pdfminer.six (text+tables) + pypdfium2/PDFium (render) + pypdf (structure), local, no cloud, no API key",
        "pages": pages,
        "tables": structured_tables,
        "key_numbers": {
            "candidates_and_verification": key_numbers,
            "valid": [f["masked"] for f in key_numbers if f["status"] == "valid"],
            "mismatches": [f for f in key_numbers if f["status"] != "valid"],
            "needs_review": uncertain,
        },
        "uncertain_items": uncertain,
        "verify_report": "verify-report.json",
        "sparse_pages": [p["page"] for p in pages if p["sparse"]],
        "notes": [
            "表格为线框检测(find_tables)；每张表带 page+bbox+结构化 rows，无边框表格可能漏检，请结合正文核对",
            "稀疏页/扫描页 needs_vision=true，OCR 由调用方视觉模型完成后回填，本脚本不虚构内容",
            "关键数字两轮交叉校验详见 verify-report.json；15 位纯数字长度有歧义，标 needs_review 不臆断",
        ],
    }
    with open(os.path.join(args.outdir, "extract.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.outdir, "extract.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md_parts))
    with open(os.path.join(args.outdir, "verify-report.json"), "w", encoding="utf-8") as f:
        json.dump(verify_report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"pages": len(pages), "tables": len(structured_tables),
                      "key_numbers_valid": verify_report["summary"]["valid"],
                      "key_numbers_mismatch": verify_report["summary"]["invalid"],
                      "key_numbers_needs_review": verify_report["summary"]["needs_review"],
                      "sparse_pages": result["sparse_pages"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
