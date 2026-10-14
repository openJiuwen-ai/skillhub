#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence-digest: 多源材料证据台账（来源定位+冲突+问题覆盖）。"""
import argparse
import json
import os
import re
import sys

from docx import Document
from openpyxl import load_workbook

NUM_RE = re.compile(r"\d+(?:\.\d+)?")
CLAUSE_RE = re.compile(r"([^，。；;、\s]{2,14}?)\s*(?:为|是|:|：)?\s*(\d+(?:\.\d+)?)\s*(%|万元|亿元|元|万人|人|个|天|次|家|项|件)")


def norm_label(label_part):
    """取数字前的尾端中文词（2-6字），归一化口径标签。"""
    runs = re.findall(r"[\u4e00-\u9fff]{2,6}", label_part)
    return runs[-1] if runs else label_part[-6:]


def extract_md_lines(path):
    with open(path, encoding="utf-8") as f:
        for i, line in enumerate(f, start=1):
            line = line.rstrip()
            if line.strip():
                yield f"行{i}", line


def extract_docx_paras(path):
    doc = Document(path)
    for i, p in enumerate(doc.paragraphs, start=1):
        if p.text.strip():
            yield f"段落{i}", p.text.strip()
    for ti, t in enumerate(doc.tables, start=1):
        for ri, row in enumerate(t.rows, start=1):
            for ci, c in enumerate(row.cells, start=1):
                if c.text.strip():
                    yield f"表{ti}行{ri}列{ci}", c.text.strip()


def extract_xlsx_cells(path):
    wb = load_workbook(path, data_only=True)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            cells = [(c.coordinate, c.value) for c in row
                     if c.value is not None and str(c.value).strip()]
            if not cells:
                continue
            label = ""
            if len(cells) > 1 and not isinstance(cells[0][1], (int, float)):
                label = str(cells[0][1]).strip()
            for coord, v in cells:
                s = str(v).strip()
                if label and coord != cells[0][0] and any(ch.isdigit() for ch in s):
                    yield f"{ws.title}!{coord}", f"{label}: {s}"
                else:
                    yield f"{ws.title}!{coord}", s
    wb.close()


def scan_materials(materials_dir):
    items = []
    vision = []
    for root, _dirs, names in sorted(os.walk(materials_dir)):
        for n in sorted(names):
            p = os.path.join(root, n)
            ext = os.path.splitext(n)[1].lower()
            rel = os.path.relpath(p, materials_dir)
            try:
                if ext in (".md", ".txt"):
                    src = extract_md_lines(p)
                elif ext == ".docx":
                    src = extract_docx_paras(p)
                elif ext == ".xlsx":
                    src = extract_xlsx_cells(p)
                elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
                    vision.append({"file": rel, "needs_vision": True,
                                   "note": "图片内容需视觉识别后回填, 不编造"})
                    continue
                else:
                    continue
                for loc, text in src:
                    items.append({"file": rel, "location": loc, "text": text})
            except Exception as e:
                items.append({"file": rel, "location": "ERROR", "text": f"<读取失败: {e}>"})
    return items, vision


def build_ledger(items):
    ledger = []
    for i, it in enumerate(items, start=1):
        ledger.append({"id": f"E-{i:04d}", "text": it["text"],
                       "source_file": it["file"], "location": it["location"],
                       "has_numbers": bool(NUM_RE.search(it["text"]))})
    return ledger


def find_conflicts(ledger):
    """条款级 标签+数值+单位 冲突检测。"""
    metrics = {}
    for e in ledger:
        if not e["has_numbers"]:
            continue
        for m in CLAUSE_RE.finditer(e["text"]):
            label, value, unit = norm_label(m.group(1)), m.group(2), m.group(3)
            key = (label.strip(), unit)
            metrics.setdefault(key, []).append(
                {"value": float(value), "id": e["id"], "file": e["source_file"],
                 "location": e["location"]})
    conflicts = []
    for (label, unit), hits in sorted(metrics.items()):
        values = {h["value"] for h in hits}
        if len(values) > 1 and len(hits) >= 2:
            conflicts.append({"metric": f"{label}（{unit}）", "entries": hits})
    return conflicts


def load_questions(path):
    qs = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            qs.append(re.sub(r"^[-*\d\.\、\)\s]+", "", line))
    return qs


def keywords_of(question):
    toks = re.findall(r"[\u4e00-\u9fa5A-Za-z0-9]{2,}", question)
    return [t for t in toks if len(t) >= 2]


def coverage(questions, ledger):
    cov = []
    open_qs = []
    for qi, q in enumerate(questions, start=1):
        kws = keywords_of(q)
        hits = [e["id"] for e in ledger
                if any(k in e["text"] for k in kws)]
        entry = {"q_no": qi, "question": q, "evidence_ids": hits[:20]}
        if hits:
            cov.append(entry)
        else:
            entry["status"] = "no_evidence"
            open_qs.append(entry)
    return cov, open_qs


def main():
    ap = argparse.ArgumentParser(description="多源证据台账")
    ap.add_argument("--materials", required=True)
    ap.add_argument("--questions", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    if not os.path.isdir(args.materials):
        print("ERROR: materials dir not found", file=sys.stderr)
        return 2
    items, vision = scan_materials(args.materials)
    ledger = build_ledger(items)
    conflicts = find_conflicts(ledger)
    try:
        questions = load_questions(args.questions)
    except Exception as e:
        print(f"ERROR: cannot read questions: {e}", file=sys.stderr)
        return 2
    cov, open_qs = coverage(questions, ledger)

    os.makedirs(args.outdir, exist_ok=True)
    result = {
        "materials_dir": args.materials,
        "ledger": ledger,
        "image_sources_needing_vision": vision,
        "conflicts": conflicts,
        "coverage": cov,
        "stats": {"evidence_items": len(ledger),
                  "with_numbers": sum(1 for e in ledger if e["has_numbers"]),
                  "conflicts": len(conflicts),
                  "questions": len(questions),
                  "questions_no_evidence": len(open_qs)},
        "note": "台账每条含来源文件与定位; 无来源推断不入台账; 图片needs_vision不编造",
    }
    with open(os.path.join(args.outdir, "evidence-ledger.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    lines = ["# 证据台账", ""]
    for e in ledger:
        lines.append(f"- **{e['id']}** `{e['source_file']}@{e['location']}`：{e['text']}")
    lines += ["", "## 冲突指标", ""]
    for c in conflicts:
        lines.append(f"- {c['metric']}: " + "；".join(
            f"{h['value']}（{h['file']}@{h['location']}）" for h in c["entries"]))
    lines += ["", "## 无证据问题", ""]
    lines += [f"- {o['question']}" for o in open_qs] or ["（无）"]
    with open(os.path.join(args.outdir, "evidence-ledger.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    with open(os.path.join(args.outdir, "open-questions.json"), "w", encoding="utf-8") as f:
        json.dump({"open_questions": open_qs}, f, ensure_ascii=False, indent=2)
    print(json.dumps(result["stats"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
