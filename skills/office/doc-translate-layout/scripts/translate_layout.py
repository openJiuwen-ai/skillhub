#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc-translate-layout: 文档翻译并保持版式（extract/apply 两步）。

用法:
  python translate_layout.py extract --input src.docx --worksheet ws.json
  python translate_layout.py apply --input src.docx --worksheet ws.json --translations tr.json \
      --output out.docx [--glossary g.json] [--report r.json] [--overflow-ratio 2.0]

退出码: 0=成功 2=输入错误
"""
import argparse
import json
import os
import sys


def docx_paragraphs(doc):
    """[(id, style, text, paragraph_element)] 含正文(表格内)+页眉页脚。"""
    from docx.oxml.ns import qn
    items = []
    idx = 0
    body = doc.element.body
    for p in body.iter(qn("w:p")):
        runs = [r for r in p.iter(qn("w:r"))
                if r.getparent() is not None and r.getparent().tag != qn("w:del")
                and r.findall(qn("w:t"))]
        text = "".join("".join(t.text or "" for t in r.findall(qn("w:t"))) for r in runs)
        if not text.strip():
            continue
        node = p.getparent()
        in_table = False
        while node is not None:
            if node.tag == qn("w:tbl"):
                in_table = True
                break
            node = node.getparent()
        style = ""
        try:
            style = p.get_or_add_pPr().find(qn("w:pStyle")).get(qn("w:val")) or ""
        except Exception:
            style = ""
        items.append({"id": f"p-{idx:04d}", "text": text, "style": style,
                      "location": "table" if in_table else "body", "el": p})
        idx += 1
    for si, sec in enumerate(doc.sections):
        for kind in ("header", "footer"):
            try:
                el = getattr(sec, kind)._element
            except Exception:
                continue
            if el is None:
                continue
            for p in el.iter(qn("w:p")):
                runs = [r for r in p.iter(qn("w:r")) if r.findall(qn("w:t"))]
                text = "".join("".join(t.text or "" for t in r.findall(qn("w:t"))) for r in runs)
                if not text.strip():
                    continue
                items.append({"id": f"{kind[0]}-{si}-{idx:04d}", "text": text,
                              "style": "", "location": f"section{si} {kind}", "el": p})
                idx += 1
    return items


def pptx_paragraphs(prs):
    from pptx.oxml.ns import qn
    items = []
    idx = 0
    for si, slide in enumerate(prs.slides, start=1):
        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            for pi, para in enumerate(shape.text_frame.paragraphs):
                text = "".join(r.text or "" for r in para.runs)
                if not text.strip():
                    continue
                items.append({"id": f"s{si:02d}-p{pi:03d}", "text": text, "style": "",
                              "location": f"slide {si}", "para": para})
                idx += 1
    return items


def set_docx_para_text(item, new_text):
    from docx.oxml.ns import qn
    p = item["el"]
    runs = [r for r in p.iter(qn("w:r"))
            if r.getparent() is not None and r.getparent().tag != qn("w:del")
            and r.findall(qn("w:t"))]
    if not runs:
        return False
    for r in runs:
        ts = r.findall(qn("w:t"))
        for t in ts:
            t.text = ""
        ts[0].text = new_text
        ts[0].set(qn("xml:space"), "preserve")
        return True
    return False


def shrink_docx_para_font(item, min_pt=9.0):
    """对有显式字号的 run 降 1pt；返回是否实际缩放。"""
    from docx.oxml.ns import qn
    p = item["el"]
    shrunk = False
    for r in p.iter(qn("w:r")):
        rPr = r.find(qn("w:rPr"))
        if rPr is None:
            continue
        sz = rPr.find(qn("w:sz"))
        if sz is None:
            continue
        half_pts = int(sz.get(qn("w:val")))
        pt = half_pts / 2.0
        if pt > min_pt:
            sz.set(qn("w:val"), str(int(max(min_pt, pt - 1) * 2)))
            shrunk = True
    return shrunk


def cmd_extract(args):
    if args.input.lower().endswith(".pptx"):
        from pptx import Presentation
        prs = Presentation(args.input)
        items = pptx_paragraphs(prs)
    else:
        from docx import Document
        doc = Document(args.input)
        items = docx_paragraphs(doc)
        for it in items:
            it.pop("el", None)
    with open(args.worksheet, "w", encoding="utf-8") as f:
        json.dump({"source": args.input,
                   "items": [{"id": it["id"], "text": it["text"],
                              "style": it["style"], "location": it["location"]}
                             for it in items]}, f, ensure_ascii=False, indent=2)
    print(json.dumps({"items": len(items), "worksheet": args.worksheet},
                     ensure_ascii=False))
    return 0


def cmd_apply(args):
    try:
        with open(args.worksheet, encoding="utf-8") as f:
            ws = json.load(f)
        with open(args.translations, encoding="utf-8") as f:
            tr = json.load(f)
        glossary = {}
        if args.glossary and os.path.exists(args.glossary):
            with open(args.glossary, encoding="utf-8") as f:
                glossary = json.load(f)
    except Exception as e:
        print(f"ERROR: bad input files: {e}", file=sys.stderr)
        return 2
    trans_map = {k: v for k, v in (tr.items() if isinstance(tr, dict)
                                   else [(x["id"], x.get("translation", "")) for x in tr])}

    if args.input.lower().endswith(".pptx"):
        from pptx import Presentation
        prs = Presentation(args.input)
        items = pptx_paragraphs(prs)
        translated = 0
        untranslated = []
        fallback = []
        glossary_hits = []
        glossary_misses = []
        for it in items:
            tid = it["id"]
            if tid not in trans_map or not trans_map[tid]:
                untranslated.append(tid)
                continue
            new_text = trans_map[tid]
            for term, tgt in glossary.items():
                if term in it["text"]:
                    if tgt in new_text:
                        glossary_hits.append({"id": tid, "term": term})
                    else:
                        glossary_misses.append({"id": tid, "term": term, "expected": tgt})
            para = it["para"]
            if para.runs:
                para.runs[0].text = new_text
                for r in para.runs[1:]:
                    r.text = ""
                translated += 1
            if len(new_text) > args.overflow_ratio * max(1, len(it["text"])):
                fallback.append({"id": tid, "src_len": len(it["text"]),
                                 "tgt_len": len(new_text), "action": "flagged"})
        prs.save(args.output)
    else:
        from docx import Document
        doc = Document(args.input)
        items = docx_paragraphs(doc)
        translated = 0
        untranslated = []
        fallback = []
        glossary_hits = []
        glossary_misses = []
        for it in items:
            tid = it["id"]
            if tid not in trans_map or not trans_map[tid]:
                untranslated.append(tid)
                continue
            new_text = trans_map[tid]
            for term, tgt in glossary.items():
                if term in it["text"]:
                    if tgt in new_text:
                        glossary_hits.append({"id": tid, "term": term})
                    else:
                        glossary_misses.append({"id": tid, "term": term, "expected": tgt})
            ok = set_docx_para_text(it, new_text)
            if ok:
                translated += 1
            if len(new_text) > args.overflow_ratio * max(1, len(it["text"])):
                shrunk = shrink_docx_para_font(it)
                fallback.append({"id": tid, "src_len": len(it["text"]),
                                 "tgt_len": len(new_text),
                                 "action": "font_shrunk" if shrunk else "needs_attention"})
        doc.save(args.output)

    report = {
        "input": args.input,
        "output": args.output,
        "items_total": len(items),
        "translated": translated,
        "untranslated_ids": untranslated,
        "glossary_hits": glossary_hits,
        "glossary_misses": glossary_misses,
        "fallback_list": fallback,
        "overflow_ratio_threshold": args.overflow_ratio,
        "mt_engine": "external (caller-provided translations)",
        "note": "结构保持为确定性验证范围; 译文质量未验证(由提供方负责), 见 SKILL.md 诚实边界",
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"translated": translated, "untranslated": len(untranslated),
                      "fallback": len(fallback),
                      "glossary_misses": len(glossary_misses)}, ensure_ascii=False))
    return 0


def main():
    ap = argparse.ArgumentParser(description="文档翻译保持版式")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("extract")
    p1.add_argument("--input", required=True)
    p1.add_argument("--worksheet", required=True)
    p1.set_defaults(func=cmd_extract)
    p2 = sub.add_parser("apply")
    p2.add_argument("--input", required=True)
    p2.add_argument("--worksheet", required=True)
    p2.add_argument("--translations", required=True)
    p2.add_argument("--output", required=True)
    p2.add_argument("--glossary")
    p2.add_argument("--report")
    p2.add_argument("--overflow-ratio", type=float, default=2.0)
    p2.set_defaults(func=cmd_apply)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
