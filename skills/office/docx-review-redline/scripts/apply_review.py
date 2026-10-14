#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docx-review-redline: 应用结构化审校计划，产出原生批注+修订稿。

用法:
  python apply_review.py --input draft.docx --plan plan.json --output out_REVIEWED.docx [--report r.json]

退出码: 0=完成(可含skipped) 2=输入错误
"""
import argparse
import copy
import json
import re
import shutil
import sys
import zipfile

from docx import Document
from docx.oxml.ns import qn

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
CT_COMMENTS_EXT = "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"
REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
REL_COMMENTS_EXT = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"

_ID = {"next": 100}


def next_id():
    _ID["next"] += 1
    return _ID["next"]


def para_runs(p_el):
    runs = []
    for r in p_el.iter(qn("w:r")):
        parent = r.getparent()
        if parent is not None and parent.tag == qn("w:del"):
            continue
        ts = r.findall(qn("w:t"))
        if ts:
            runs.append((r, ts))
    return runs


def set_t_text(t_el, text):
    t_el.text = text
    if text != text.strip() or text == "":
        t_el.set(qn("xml:space"), "preserve")


def split_run(run, ts, offset):
    """把 run 在字符偏移处拆成两个 run; 返回(前run, 后run)。offset==0 或末尾时返回(None或run)。"""
    text = "".join(t.text or "" for t in ts)
    if offset <= 0:
        return None, (run, ts)
    if offset >= len(text):
        return (run, ts), None
    head, tail = text[:offset], text[offset:]
    tail_run = copy.deepcopy(run)
    tail_ts = tail_run.findall(qn("w:t"))
    for t in tail_ts:
        t.text = ""
    set_t_text(tail_ts[0], tail)
    for t in ts:
        t.text = ""
    set_t_text(ts[0], head)
    run.addnext(tail_run)
    return (run, ts), (tail_run, tail_ts)


def make_ins(new_text, template_run, author, date):
    ins = template_run.makeelement(qn("w:ins"), {})
    ins.set(qn("w:id"), str(next_id()))
    ins.set(qn("w:author"), author)
    ins.set(qn("w:date"), date)
    r = copy.deepcopy(template_run)
    ts = r.findall(qn("w:t"))
    for t in ts:
        t.text = ""
    set_t_text(ts[0], new_text)
    ins.append(r)
    return ins


def wrap_run_in_del(run, ts):
    """把 run 包进 w:del, w:t -> w:delText。返回 w:del 元素。"""
    parent = run.getparent()
    idx = list(parent).index(run)
    dele = parent.makeelement(qn("w:del"), {})
    dele.set(qn("w:id"), str(next_id()))
    dele.set(qn("w:author"), author_global[0])
    dele.set(qn("w:date"), date_global[0])
    parent.remove(run)
    parent.insert(idx, dele)
    dele.append(run)
    for t in ts:
        tag = t.tag
        if tag == qn("w:t"):
            dt = t.makeelement(qn("w:delText"), {})
            dt.text = t.text
            dt.set(qn("xml:space"), "preserve")
            t.getparent().replace(t, dt)
    return dele


def make_comment_anchor(p_el, before_el, after_el, comment_id):
    """在 before_el 前插 commentRangeStart, after_el 后插 commentRangeEnd + reference run。"""
    crs = p_el.makeelement(qn("w:commentRangeStart"), {})
    crs.set(qn("w:id"), str(comment_id))
    before_el.addprevious(crs)
    cre = p_el.makeelement(qn("w:commentRangeEnd"), {})
    cre.set(qn("w:id"), str(comment_id))
    after_el.addnext(cre)
    ref_run = p_el.makeelement(qn("w:r"), {})
    ref = ref_run.makeelement(qn("w:commentReference"), {})
    ref.set(qn("w:id"), str(comment_id))
    ref_run.append(ref)
    cre.addnext(ref_run)


def apply_replace_op(p_el, start, end, new_text, comment_id, comment_text, results_meta):
    """在段落内 [start,end) 应用修订+批注。返回 True/False。"""
    runs = para_runs(p_el)
    boundaries = []
    pos = 0
    for r, ts in runs:
        text = "".join(t.text or "" for t in ts)
        boundaries.append([pos, pos + len(text), r, ts])
        pos += len(text)
    full = "".join("".join(t.text or "" for t in ts) for _, ts in runs)
    affected = [b for b in boundaries if b[0] < end and b[1] > start]
    if not affected:
        return False
    # 拆首尾 run，使匹配区间整 run 化
    first = affected[0]
    if start > first[0]:
        _, tail = split_run(first[2], first[3], start - first[0])
        if tail is None:
            return False
        first[2], first[3] = tail
        first[0] = start
    runs = para_runs(p_el)
    boundaries = []
    pos = 0
    for r, ts in runs:
        text = "".join(t.text or "" for t in ts)
        boundaries.append([pos, pos + len(text), r, ts])
        pos += len(text)
    affected = [b for b in boundaries if b[0] < end and b[1] > start]
    if not affected:
        return False
    last = affected[-1]
    if end < last[1]:
        head, _ = split_run(last[2], last[3], end - last[0])
        if head is None:
            return False
        last[2], last[3] = head
        last[1] = end
    # 重新收集受影响 run（此时均为整 run 处于区间内）
    runs = para_runs(p_el)
    boundaries = []
    pos = 0
    for r, ts in runs:
        text = "".join(t.text or "" for t in ts)
        boundaries.append([pos, pos + len(text), r, ts])
        pos += len(text)
    seg_runs = [(r, ts) for s, e, r, ts in boundaries if s >= start and e <= end and (s < end and e > start)]
    if not seg_runs:
        return False
    template_run = copy.deepcopy(seg_runs[0][0])
    # 包 w:del
    dels = []
    for r, ts in seg_runs:
        dels.append(wrap_run_in_del(r, ts))
    first_del = dels[0]
    last_el = dels[-1]
    # w:ins
    ins = make_ins(new_text, template_run, author_global[0], date_global[0])
    last_el.addnext(ins)
    # 批注锚点
    make_comment_anchor(p_el, first_del, ins, comment_id)
    results_meta["comments"].append({"id": comment_id, "text": comment_text or f"将“{full[start:end]}”改为“{new_text}”"})
    return True


def apply_comment_op(p_el, start, end, comment_id, comment_text, results_meta):
    runs = para_runs(p_el)
    boundaries = []
    pos = 0
    for r, ts in runs:
        text = "".join(t.text or "" for t in ts)
        boundaries.append([pos, pos + len(text), r, ts])
        pos += len(text)
    affected = [b for b in boundaries if b[0] < end and b[1] > start]
    if not affected:
        return False
    first = affected[0]
    last = affected[-1]
    make_comment_anchor(p_el, first[2], last[2], comment_id)
    results_meta["comments"].append({"id": comment_id, "text": comment_text})
    return True


def iter_all_paragraphs(doc):
    """文档顺序产出 (定位描述, 段落元素)。正文+表格混排统一编号；页眉页脚单独。"""
    body = doc.element.body
    idx = 0
    for p in body.iter(qn("w:p")):
        # 计算表格内定位
        loc = f"paragraph {idx} (body)"
        anc = p.getparent()
        tbl = None
        node = p
        while node is not None:
            if node.tag == qn("w:tbl"):
                tbl = node
                break
            node = node.getparent()
        if tbl is not None:
            # 行列定位
            row_idx = -1
            for ri, row in enumerate(tbl.findall(qn("w:tr"))):
                for ci, cell in enumerate(row.findall(qn("w:tc"))):
                    if any(pp is p for pp in cell.iter(qn("w:p"))):
                        row_idx = ri
                        loc = f"table row {row_idx} col {ci} (paragraph {idx})"
                        break
                if row_idx >= 0:
                    break
        yield loc, p
        idx += 1
    for si, section in enumerate(doc.sections):
        for kind, hf in (("header", section.header), ("footer", section.footer),
                         ("first_page_header", section.first_page_header),
                         ("first_page_footer", section.first_page_footer)):
            try:
                el = hf._element
            except Exception:
                continue
            if el is None:
                continue
            for p in el.iter(qn("w:p")):
                yield f"section {si} {kind}", p


def find_in_paragraph(p_el, needle):
    runs = para_runs(p_el)
    full = "".join("".join(t.text or "" for t in ts) for _, ts in runs)
    return [m.span() for m in re.finditer(re.escape(needle), full)]


def build_comments_xml(comments, author, date):
    esc = lambda s: (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                     .replace('"', "&quot;"))
    items = []
    for c in comments:
        items.append(
            f'<w:comment w:id="{c["id"]}" w:author="{esc(author)}" w:date="{date}" w:initials="RE">'
            f'<w:p><w:r><w:t xml:space="preserve">{esc(c["text"])}</w:t></w:r></w:p></w:comment>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:comments xmlns:w="' + W_NS + '">' + "".join(items) + '</w:comments>')


def build_comments_extended_xml(comments):
    items = []
    for c in comments:
        items.append(
            '<w15:commentEx w15:paraId="00000000" w15:paraIdParent="00000000" '
            'w15:done="0"/>'.replace("w15:paraId", "w15:paraId"))
    # 简化: 每条评论一个 commentEx, paraId 需与 comments.xml 对应; 用递增假 paraId
    items = []
    for i, c in enumerate(comments):
        pid = format(0x10000000 + c["id"], "08X")
        items.append(f'<w15:commentEx w15:paraId="{pid}" w15:done="0"/>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w15:commentsEx xmlns:w15="http://schemas.microsoft.com/office/word/2012/wordml">'
            + "".join(items) + '</w15:commentsEx>')


def add_comments_part(docx_path, comments, author, date):
    """把 comments.xml / commentsExtended.xml 写入 docx 包并注册 content type 与关系。"""
    if not comments:
        return
    tmp = docx_path + ".tmp"
    with zipfile.ZipFile(docx_path) as zin:
        names = zin.namelist()
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for n in names:
                data = zin.read(n)
                if n == "[Content_Types].xml":
                    txt = data.decode("utf-8")
                    add = ""
                    if CT_COMMENTS not in txt:
                        add += f'<Default Extension="comments" ContentType="{CT_COMMENTS}"/>'
                    txt = txt.replace("</Types>", add + "</Types>")
                    data = txt.encode("utf-8")
                elif n == "word/_rels/document.xml.rels":
                    txt = data.decode("utf-8")
                    ids = [int(x) for x in re.findall(r'Id="rId(\d+)"', txt)]
                    nid = max(ids) + 1 if ids else 1
                    add = (f'<Relationship Id="rId{nid}" Type="{REL_COMMENTS}" Target="comments.xml"/>'
                           f'<Relationship Id="rId{nid + 1}" Type="{REL_COMMENTS_EXT}" Target="commentsExtended.xml"/>')
                    txt = txt.replace("</Relationships>", add + "</Relationships>")
                    data = txt.encode("utf-8")
                zout.writestr(n, data)
            zout.writestr("word/comments.xml", build_comments_xml(comments, author, date))
            zout.writestr("word/commentsExtended.xml", build_comments_extended_xml(comments))
    shutil.move(tmp, docx_path)


author_global = [""]
date_global = [""]


def main():
    ap = argparse.ArgumentParser(description="Word审校批注与修订")
    ap.add_argument("--input", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report")
    args = ap.parse_args()

    try:
        plan = json.loads(open(args.plan, encoding="utf-8").read())
    except Exception as e:
        print(f"ERROR: cannot read plan: {e}", file=sys.stderr)
        return 2
    ops = plan.get("operations", [])
    author = plan.get("author", "review-skill")
    from datetime import datetime
    date = plan.get("date", datetime.now().isoformat(timespec="seconds") + "Z")
    author_global[0] = author
    date_global[0] = date

    try:
        doc = Document(args.input)
    except Exception as e:
        print(f"ERROR: cannot open input: {e}", file=sys.stderr)
        return 2

    results = []
    comments_meta = {"comments": []}
    paragraphs = list(iter_all_paragraphs(doc))
    # 预提取每段文本供查找
    para_texts = [(loc, p, "".join("".join(t.text or "" for t in ts) for _, ts in para_runs(p)))
                  for loc, p in paragraphs]

    for op in ops:
        kind = op.get("op")
        find = op.get("find", "")
        if not find:
            results.append({"op": kind, "find": find, "status": "skipped", "reason": "empty find"})
            continue
        scope = op.get("scope", "first")
        applied = 0
        for loc, p, text in para_texts:
            if find not in text:
                continue
            spans = [m.span() for m in re.finditer(re.escape(find), text)]
            # 从后往前应用
            for s, e in reversed(spans):
                comment_id = next_id()  # 每处命中独立批注线程
                if kind == "replace":
                    ok = apply_replace_op(p, s, e, op.get("replace", ""), comment_id,
                                          op.get("comment", ""), comments_meta)
                elif kind == "comment":
                    ok = apply_comment_op(p, s, e, comment_id, op.get("comment", ""), comments_meta)
                else:
                    ok = False
                if ok:
                    applied += 1
                    results.append({"op": kind, "find": find, "status": "applied", "location": loc})
                    # 更新段落文本缓存
                    text = "".join("".join(t.text or "" for t in ts) for _, ts in para_runs(p))
                if scope != "all":
                    break
            if applied and scope != "all":
                break
        if applied == 0:
            results.append({"op": kind, "find": find, "status": "skipped",
                            "reason": "text not found in any single paragraph"})

    # 保存并注入批注 part
    try:
        doc.save(args.output)
    except Exception as e:
        print(f"ERROR: cannot save: {e}", file=sys.stderr)
        return 2

    add_comments_part(args.output, comments_meta["comments"], author, date)

    report = {
        "input": args.input,
        "output": args.output,
        "operations_total": len(ops),
        "applied": sum(1 for r in results if r["status"] == "applied"),
        "skipped": [r for r in results if r["status"] == "skipped"],
        "results": results,
        "comments": comments_meta["comments"],
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
