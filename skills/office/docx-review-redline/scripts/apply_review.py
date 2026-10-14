#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""docx-review-redline: 应用结构化审校计划，产出原生批注+修订稿。

用法:
  python apply_review.py --input draft.docx --plan plan.json --output out_REVIEWED.docx [--report r.json]

plan.json:
  {
    "author": "审校人",
    "operations": [
      {"op": "replace", "find": "旧文本", "replace": "新文本", "comment": "理由"},
      {"op": "insert",  "find": "锚文本", "insert": "插入文本", "position": "after"},
      {"op": "delete",  "find": "删除文本", "comment": "理由"},
      {"op": "comment", "find": "目标文本", "comment": "意见"}
    ]
  }
  每个操作可选 "occurrence"（默认 1，1 基，选择文档顺序第 N 处命中）
  或 "scope": "all"（全部命中逐一应用）。

行为要点:
  - find 支持跨多个 run；按文档顺序（含表格内段落、页眉页脚）定位
  - 替换 = 原文本进 w:del + 新文本进 w:ins；插入 = w:ins；删除 = w:del
  - 每处命中挂一条原生批注线程（comments.xml + commentRangeStart/End +
    commentReference + commentsExtended，paraId 互相匹配）
  - 输入已有批注/修订时合并：不产生重复 zip 条目，修订/批注 id 不冲突
  - 空计划：原样字节复制输出并报告 0 操作

退出码: 0=完成(可含skipped) 2=输入错误
"""
import argparse
import copy
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime

from docx import Document
from docx.oxml.ns import qn

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
W14_NS = "http://schemas.microsoft.com/office/word/2010/wordml"
W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"
CT_COMMENTS = "application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"
CT_COMMENTS_EXT = "application/vnd.openxmlformats-officedocument.wordprocessingml.commentsExtended+xml"
REL_COMMENTS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments"
REL_COMMENTS_EXT = "http://schemas.microsoft.com/office/2011/relationships/commentsExtended"

OP_KINDS = ("replace", "insert", "delete", "comment")

_ID = {"next": 100}


def next_id():
    _ID["next"] += 1
    return _ID["next"]


def init_ids(input_path):
    """扫描输入包内既有 w:ins/w:del/批注锚点/批注的 w:id，保证新 id 不冲突。"""
    ids = [100]
    try:
        with zipfile.ZipFile(input_path) as zf:
            for name in zf.namelist():
                if not name.endswith(".xml"):
                    continue
                txt = zf.read(name).decode("utf-8", "ignore")
                pat = (r'<w:(?:ins|del|moveFrom|moveTo|commentRangeStart|'
                       r'commentRangeEnd|commentReference|comment)\b[^>]*\bw:id="(\d+)"')
                ids.extend(int(m) for m in re.findall(pat, txt))
    except Exception:
        pass
    _ID["next"] = max(ids)


def para_runs(p_el):
    """段落内有效 run（跳过已在 w:del 中的），含 w:ins 中的 run。"""
    runs = []
    for r in p_el.iter(qn("w:r")):
        parent = r.getparent()
        if parent is not None and parent.tag == qn("w:del"):
            continue
        ts = r.findall(qn("w:t"))
        if ts:
            runs.append((r, ts))
    return runs


def run_text(ts):
    return "".join(t.text or "" for t in ts)


def paragraph_text(p_el):
    return "".join(run_text(ts) for _, ts in para_runs(p_el))


def run_boundaries(p_el):
    boundaries = []
    pos = 0
    for r, ts in para_runs(p_el):
        text = run_text(ts)
        boundaries.append([pos, pos + len(text), r, ts])
        pos += len(text)
    return boundaries


def set_t_text(t_el, text):
    t_el.text = text
    if text != text.strip() or text == "":
        t_el.set(qn("xml:space"), "preserve")


def split_run(run, ts, offset):
    """把 run 在字符偏移处拆成两个 run; 返回(前(run,ts)或None, 后(run,ts)或None)。"""
    text = run_text(ts)
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


def make_ins(new_text, template_run):
    ins = template_run.makeelement(qn("w:ins"), {})
    ins.set(qn("w:id"), str(next_id()))
    ins.set(qn("w:author"), author_global[0])
    ins.set(qn("w:date"), date_global[0])
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
        if t.tag == qn("w:t"):
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


def add_comment_meta(results_meta, comment_id, comment_text):
    results_meta["comments"].append({"id": comment_id, "text": comment_text})


def apply_revision_op(p_el, start, end, new_text, comment_id, comment_text,
                      results_meta):
    """在段落内 [start,end) 应用修订+批注。new_text 为 None 表示纯删除；
    为字符串表示替换（空字符串=删除并留空）。返回 True/False。"""
    boundaries = run_boundaries(p_el)
    affected = [b for b in boundaries if b[0] < end and b[1] > start]
    if not affected:
        return False
    # 拆首 run，使匹配起点落在 run 边界
    if start > affected[0][0]:
        _, tail = split_run(affected[0][2], affected[0][3], start - affected[0][0])
        if tail is None:
            return False
        boundaries = run_boundaries(p_el)
        affected = [b for b in boundaries if b[0] < end and b[1] > start]
        if not affected:
            return False
    # 拆尾 run，使匹配终点落在 run 边界
    if end < affected[-1][1]:
        head, _ = split_run(affected[-1][2], affected[-1][3], end - affected[-1][0])
        if head is None:
            return False
        boundaries = run_boundaries(p_el)
        affected = [b for b in boundaries if b[0] < end and b[1] > start]
        if not affected:
            return False
    seg = [b for b in run_boundaries(p_el)
           if b[0] >= start and b[1] <= end and b[1] > start]
    if not seg:
        return False
    template_run = copy.deepcopy(seg[0][2])
    old_text = "".join(run_text(ts) for _, _, _, ts in seg)
    first_del = None
    last_el = None
    for _, _, r, ts in seg:
        dele = wrap_run_in_del(r, ts)
        if first_del is None:
            first_del = dele
        last_el = dele
    anchor_end = last_el
    if new_text:
        ins = make_ins(new_text, template_run)
        last_el.addnext(ins)
        anchor_end = ins
    make_comment_anchor(p_el, first_del, anchor_end, comment_id)
    if not comment_text:
        if new_text:
            comment_text = f"将“{old_text}”改为“{new_text}”"
        else:
            comment_text = f"删除“{old_text}”"
    add_comment_meta(results_meta, comment_id, comment_text)
    return True


def apply_insert_op(p_el, start, end, new_text, position, comment_id,
                    comment_text, results_meta):
    """在命中区间前/后插入 w:ins 新文本并挂批注。返回 True/False。"""
    cut = start if position == "before" else end
    boundaries = run_boundaries(p_el)
    if not boundaries:
        return False
    ins = None
    for s0, e0, r, ts in boundaries:
        if s0 < cut < e0:
            head, tail = split_run(r, ts, cut - s0)
            if tail is None:
                return False
            ins = make_ins(new_text, r)
            tail[0].addprevious(ins)
            break
        if cut == e0:
            ins = make_ins(new_text, r)
            r.addnext(ins)
            break
        if cut == s0:
            ins = make_ins(new_text, r)
            r.addprevious(ins)
            break
    else:
        ins = make_ins(new_text, boundaries[-1][2])
        boundaries[-1][2].addnext(ins)
    make_comment_anchor(p_el, ins, ins, comment_id)
    add_comment_meta(results_meta, comment_id,
                     comment_text or f"插入“{new_text}”")
    return True


def apply_comment_op(p_el, start, end, comment_id, comment_text, results_meta):
    boundaries = run_boundaries(p_el)
    affected = [b for b in boundaries if b[0] < end and b[1] > start]
    if not affected:
        return False
    make_comment_anchor(p_el, affected[0][2], affected[-1][2], comment_id)
    add_comment_meta(results_meta, comment_id, comment_text)
    return True


def iter_all_paragraphs(doc):
    """文档顺序产出 (定位描述, 段落元素)。正文+表格混排统一编号；页眉页脚单独。"""
    body = doc.element.body
    idx = 0
    for p in body.iter(qn("w:p")):
        loc = f"paragraph {idx} (body)"
        node = p
        tbl = None
        while node is not None:
            if node.tag == qn("w:tbl"):
                tbl = node
                break
            node = node.getparent()
        if tbl is not None:
            for ri, row in enumerate(tbl.findall(qn("w:tr"))):
                found = False
                for ci, cell in enumerate(row.findall(qn("w:tc"))):
                    if any(pp is p for pp in cell.iter(qn("w:p"))):
                        loc = f"table row {ri} col {ci} (paragraph {idx})"
                        found = True
                        break
                if found:
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


def xml_escape(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def initials_of(author):
    parts = [w for w in str(author).split() if w]
    return ("".join(w[0] for w in parts[:2]).upper()[:4]) or "RV"


def comment_element_str(c, author, date):
    return (
        f'<w:comment xmlns:w="{W_NS}" xmlns:w14="{W14_NS}" w:id="{c["id"]}" '
        f'w:author="{xml_escape(author)}" w:date="{xml_escape(date)}" '
        f'w:initials="{xml_escape(initials_of(author))}">'
        f'<w:p w14:paraId="{c["para_id"]}">'
        f'<w:r><w:t xml:space="preserve">{xml_escape(c["text"])}</w:t></w:r>'
        f'</w:p></w:comment>')


def assign_para_ids(comments, existing_texts):
    """为每条新批注分配唯一 paraId（与 comments.xml/commentsExtended 既有值不冲突）。"""
    used = set()
    for t in existing_texts:
        used |= set(re.findall(r'paraId="([0-9A-Fa-f]{8})"', t))
    n = 0x20000001
    for c in comments:
        while format(n, "08X") in used:
            n += 1
        c["para_id"] = format(n, "08X")
        used.add(c["para_id"])
        n += 1


def splice_before_close(xml_text, snippet, localname):
    """把 snippet 插到既有 XML 根元素闭合标签前（保留原文其余字节）。"""
    m = re.search(r"</[A-Za-z_][\w.-]*:" + localname + r"\s*>", xml_text)
    if not m:
        raise ValueError(f"closing tag not found for {localname}")
    return xml_text[:m.start()] + snippet + xml_text[m.start():]


def ensure_content_types(data):
    txt = data.decode("utf-8")
    add = ""
    if 'PartName="/word/comments.xml"' not in txt:
        add += f'<Override PartName="/word/comments.xml" ContentType="{CT_COMMENTS}"/>'
    if 'PartName="/word/commentsExtended.xml"' not in txt:
        add += f'<Override PartName="/word/commentsExtended.xml" ContentType="{CT_COMMENTS_EXT}"/>'
    if add:
        txt = txt.replace("</Types>", add + "</Types>")
    return txt.encode("utf-8")


def ensure_rels(data):
    txt = data.decode("utf-8")

    def next_rid(t):
        ids = [int(x) for x in re.findall(r'Id="rId(\d+)"', t)]
        return (max(ids) + 1) if ids else 1

    if 'Target="comments.xml"' not in txt:
        nid = next_rid(txt)
        txt = txt.replace("</Relationships>",
                          f'<Relationship Id="rId{nid}" Type="{REL_COMMENTS}" '
                          f'Target="comments.xml"/></Relationships>')
    if 'Target="commentsExtended.xml"' not in txt:
        nid = next_rid(txt)
        txt = txt.replace("</Relationships>",
                          f'<Relationship Id="rId{nid}" Type="{REL_COMMENTS_EXT}" '
                          f'Target="commentsExtended.xml"/></Relationships>')
    return txt.encode("utf-8")


def add_comments_part(docx_path, comments, author, date):
    """把批注写入 docx 包：合并既有 comments/commentsExtended（不产生重复 zip
    条目），注册 content type 与关系。"""
    if not comments:
        return
    with zipfile.ZipFile(docx_path) as zin:
        entries = [(n, zin.read(n)) for n in zin.namelist()]
    existing_names = {n for n, _ in entries}
    has_comments = "word/comments.xml" in existing_names
    has_ext = "word/commentsExtended.xml" in existing_names
    old_comments = next((d for n, d in entries if n == "word/comments.xml"), b"")
    old_ext = next((d for n, d in entries if n == "word/commentsExtended.xml"), b"")

    assign_para_ids(comments, [old_comments.decode("utf-8", "ignore"),
                               old_ext.decode("utf-8", "ignore")])
    new_comments_xml = "".join(comment_element_str(c, author, date)
                               for c in comments)
    new_ext_xml = "".join(
        f'<w15:commentEx xmlns:w15="{W15_NS}" w15:paraId="{c["para_id"]}" '
        f'w15:done="0"/>' for c in comments)

    if has_comments:
        merged_comments = splice_before_close(
            old_comments.decode("utf-8"), new_comments_xml, "comments")
    else:
        merged_comments = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                           f'<w:comments xmlns:w="{W_NS}" xmlns:w14="{W14_NS}">'
                           + new_comments_xml + '</w:comments>')
    if has_ext:
        merged_ext = splice_before_close(
            old_ext.decode("utf-8"), new_ext_xml, "commentsEx")
    else:
        merged_ext = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
                      f'<w15:commentsEx xmlns:w15="{W15_NS}">'
                      + new_ext_xml + '</w15:commentsEx>')

    tmp = docx_path + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, data in entries:
            if n == "word/comments.xml":
                data = merged_comments.encode("utf-8")
            elif n == "word/commentsExtended.xml":
                data = merged_ext.encode("utf-8")
            elif n == "[Content_Types].xml":
                data = ensure_content_types(data)
            elif n == "word/_rels/document.xml.rels":
                data = ensure_rels(data)
            zout.writestr(n, data)
        if not has_comments:
            zout.writestr("word/comments.xml", merged_comments.encode("utf-8"))
        if not has_ext:
            zout.writestr("word/commentsExtended.xml", merged_ext.encode("utf-8"))
    shutil.move(tmp, docx_path)


author_global = [""]
date_global = [""]


def skipped(op, find, reason):
    return {"op": op, "find": find, "status": "skipped", "reason": reason}


def main():
    ap = argparse.ArgumentParser(description="Word审校批注与修订")
    ap.add_argument("--input", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report")
    args = ap.parse_args()

    try:
        with open(args.plan, encoding="utf-8") as f:
            plan = json.load(f)
    except Exception as e:
        print(f"ERROR: cannot read plan: {e}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("ERROR: plan root must be a JSON object", file=sys.stderr)
        return 2
    ops = plan.get("operations", [])
    if not isinstance(ops, list):
        print("ERROR: plan.operations must be a list", file=sys.stderr)
        return 2
    author = plan.get("author", "review-skill")
    date = plan.get("date", datetime.now().isoformat(timespec="seconds") + "Z")
    author_global[0] = author
    date_global[0] = date

    try:
        doc = Document(args.input)
    except Exception as e:
        print(f"ERROR: cannot open input: {e}", file=sys.stderr)
        return 2

    if not ops:
        # 空计划：原样字节复制并报告 0 操作
        try:
            if os.path.abspath(args.input) != os.path.abspath(args.output):
                shutil.copyfile(args.input, args.output)
        except Exception as e:
            print(f"ERROR: cannot copy output: {e}", file=sys.stderr)
            return 2
        report = {"input": args.input, "output": args.output,
                  "operations_total": 0, "applied": 0, "skipped": [],
                  "results": [], "comments": []}
        if args.report:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        print(json.dumps(report, ensure_ascii=False))
        return 0

    init_ids(args.input)
    results = []
    comments_meta = {"comments": []}
    paragraphs = list(iter_all_paragraphs(doc))

    for op in ops:
        if not isinstance(op, dict):
            results.append(skipped(None, None, "operation is not an object"))
            continue
        kind = op.get("op")
        if kind not in OP_KINDS:
            results.append(skipped(kind, op.get("find", ""), "unknown op type"))
            continue
        find = op.get("find", "")
        if not find:
            results.append(skipped(kind, "", "empty find"))
            continue
        # 每个操作现算段落文本（前序操作可能已改写段落）
        matches = []  # (loc, p, start, end) 文档顺序
        for loc, p in paragraphs:
            text = paragraph_text(p)
            if find in text:
                for m in re.finditer(re.escape(find), text):
                    matches.append((loc, p, m.start(), m.end()))
        scope = op.get("scope", "first")
        if scope == "all":
            targets = matches
        else:
            occ_raw = op.get("occurrence", 1)
            try:
                occ = int(occ_raw)
            except (TypeError, ValueError):
                results.append(skipped(kind, find,
                                       f"invalid occurrence: {occ_raw!r}"))
                continue
            if occ < 1:
                results.append(skipped(kind, find, "occurrence must be >= 1"))
                continue
            if occ > len(matches):
                results.append(skipped(
                    kind, find,
                    f"occurrence {occ} out of range ({len(matches)} match(es) found)"))
                continue
            targets = [matches[occ - 1]]
        applied = 0
        # 从后往前应用，保证未处理命中的偏移仍有效
        for loc, p, s, e in reversed(targets):
            comment_id = next_id()
            if kind == "replace":
                ok = apply_revision_op(p, s, e, op.get("replace", ""), comment_id,
                                       op.get("comment", ""), comments_meta)
            elif kind == "delete":
                ok = apply_revision_op(p, s, e, None, comment_id,
                                       op.get("comment", ""), comments_meta)
            elif kind == "insert":
                ok = apply_insert_op(p, s, e, op.get("insert", ""),
                                     op.get("position", "after"), comment_id,
                                     op.get("comment", ""), comments_meta)
            else:
                ok = apply_comment_op(p, s, e, comment_id,
                                      op.get("comment", ""), comments_meta)
            if ok:
                applied += 1
                results.append({"op": kind, "find": find, "status": "applied",
                                "location": loc})
        if applied == 0:
            results.append(skipped(
                kind, find, "text not found in any single paragraph"))

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
