#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""proposal-bid-generation: 招标条款响应矩阵 + 投标草稿。"""
import argparse
import json
import os
import re
import sys

from docx import Document
from openpyxl import Workbook

STOPWORDS = set("以及对于根据按照如下要求具有应当不得可以意见有关及其当为并且")
CLAUSE_RE = re.compile(r"^\s*(?:第?[一二三四五六七八九十\d]+[、\.．)）:：]|[（(]\s*[一二三四五六七八九十\d]+\s*[)）])\s*(.*)")


def parse_tender(path):
    """返回 (clauses, parse_mode)。clause: {no, text}"""
    texts = []
    if path.lower().endswith(".docx"):
        doc = Document(path)
        texts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for t in doc.tables:
            for row in t.rows:
                for c in row.cells:
                    if c.text.strip():
                        texts.append(c.text.strip())
    else:
        with open(path, encoding="utf-8") as f:
            texts = [ln.strip() for ln in f if ln.strip()]
    numbered = [t for t in texts if CLAUSE_RE.match(t)]
    clauses = []
    if len(numbered) >= max(3, len(texts) // 3):
        mode = "numbered"
        no = 0
        for t in texts:
            m = CLAUSE_RE.match(t)
            if m and len(m.group(1)) >= 6:
                no += 1
                clauses.append({"no": f"R{no:02d}", "text": m.group(1).strip()})
    else:
        mode = "paragraph_fallback"
        no = 0
        for t in texts:
            if len(t) >= 8:
                no += 1
                clauses.append({"no": f"R{no:02d}", "text": t})
    return clauses, mode


def keywords(text):
    """中文token（长词展开为4-gram便于命中文件名）+ 字母数字token。"""
    kws = []
    for tok in re.findall(r"[\u4e00-\u9fa5]{2,}", text):
        if tok in STOPWORDS:
            continue
        if len(tok) > 6:
            seen = set()
            for i in range(len(tok) - 3):
                g = tok[i:i + 4]
                if g not in seen and g not in STOPWORDS:
                    seen.add(g)
                    kws.append(g)
        else:
            kws.append(tok)
    kws += [t for t in re.findall(r"[A-Za-z0-9]{3,}", text)]
    return kws[:40]


def load_quals(quals_dir):
    quals = []
    for root, _dirs, names in sorted(os.walk(quals_dir)):
        for n in sorted(names):
            p = os.path.join(root, n)
            ext = os.path.splitext(n)[1].lower()
            content = ""
            if ext in (".md", ".txt"):
                try:
                    with open(p, encoding="utf-8", errors="ignore") as f:
                        content = f.read(100000)
                except Exception:
                    pass
            elif ext == ".docx":
                try:
                    d = Document(p)
                    content = "\n".join(x.text for x in d.paragraphs)
                except Exception:
                    pass
            quals.append({"file": os.path.relpath(p, quals_dir), "name": n, "content": content})
    return quals


def match_clause(clause, quals):
    kws = keywords(clause["text"])
    if not kws:
        return "gap", [], []
    best = []
    for q in quals:
        name_hit = sum(1 for k in kws if k in q["name"])
        text_hit = sum(1 for k in kws if k in q["content"])
        score = name_hit * 2 + text_hit
        if score > 0:
            best.append({"file": q["file"], "score": score,
                         "matched": [k for k in kws if k in q["name"] or k in q["content"]][:5]})
    best.sort(key=lambda x: -x["score"])
    strong = [b for b in best if b["score"] >= 2]
    if strong:
        return "responded", strong[:3], kws
    return "gap", [], kws


def main():
    ap = argparse.ArgumentParser(description="招标响应矩阵与投标草稿")
    ap.add_argument("--tender", required=True)
    ap.add_argument("--quals", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        clauses, mode = parse_tender(args.tender)
    except Exception as e:
        print(f"ERROR: cannot parse tender: {e}", file=sys.stderr)
        return 2
    quals = load_quals(args.quals)

    results = []
    gaps = []
    for c in clauses:
        status, evidence, kws = match_clause(c, quals)
        results.append({"no": c["no"], "text": c["text"], "status": status,
                        "evidence": [e["file"] for e in evidence],
                        "matched_terms": kws[:8]})
        if status == "gap":
            gaps.append({"no": c["no"], "text": c["text"]})

    os.makedirs(args.outdir, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "响应矩阵"
    ws.append(["条款编号", "招标要求", "响应状态", "证据文件", "匹配词"])
    for r in results:
        ws.append([r["no"], r["text"],
                   "responded" if r["status"] == "responded" else "gap",
                   "；".join(r["evidence"]), "、".join(r["matched_terms"])])
    wb.save(os.path.join(args.outdir, "response-matrix.xlsx"))

    with open(os.path.join(args.outdir, "gaps.json"), "w", encoding="utf-8") as f:
        json.dump({"gaps": gaps, "total": len(clauses),
                   "note": "缺口条款无证据支撑，不编造资质"}, f, ensure_ascii=False, indent=2)

    doc = Document()
    doc.add_heading("投标响应文件（草稿）", level=1)
    doc.add_paragraph("本稿由招标条款响应矩阵生成：有证据条款引用资质文件；缺口条款标注待补充。"
                      "商务内容（报价、方案细节）需投标方补充，本工具不编造资质与业绩。")
    for r in results:
        doc.add_heading(f"{r['no']} {r['text'][:30]}", level=2)
        p = doc.add_paragraph()
        if r["status"] == "responded":
            p.add_run("响应：满足。支撑材料：")
            p.add_run("；".join(r["evidence"])).bold = True
        else:
            p.add_run("【待补充-缺口】本条款暂无对应资质材料，需人工确认或补充。").bold = True
    doc.save(os.path.join(args.outdir, "bid-draft.docx"))

    report = {"tender": args.tender, "parse_mode": mode,
              "clauses": len(clauses),
              "responded": sum(1 for r in results if r["status"] == "responded"),
              "gaps": len(gaps),
              "quals_files": len(quals)}
    with open(os.path.join(args.outdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
