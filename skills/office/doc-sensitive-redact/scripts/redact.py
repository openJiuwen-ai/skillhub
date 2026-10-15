#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc-sensitive-redact: DOCX/XLSX/PPTX/PDF 敏感信息打码（双轨识别 + 底层删除 + 交付级回读验证）。

用法:
  python redact.py --input <file_or_dir> --outdir out/ [--report manifest.json]
                   [--types id_card,phone,bank_card,email]

处理范围（按格式）:
  DOCX/PPTX: 直接在包内所有 XML 部件上做 run 级/文本节点级/属性级替换
             （正文、表格、页眉页脚、删除修订 w:delText、批注、脚注尾注、
              PPT 备注、幻灯片/版式/母版、图表文本、docProps、rels 等），
             未改动的部件按原始字节复制。
  XLSX: openpyxl 处理字符串/数值单元格、单元格批注、文档属性；
        保存后再对包内剩余 XML 部件做底层清洗，并保留（清洗后的）线程批注。
  PDF: 页面文本 redaction 底层删除 + 元数据/XMP/书签/批注信息/链接目标
       (URI/启动文件等字符串字段)/表单域值清洗 + 文件附件批注内容清洗
       (zip/Office 附件逐条目重写并保留无关内容, 无法可靠重写的二进制
       明文/加密/超深嵌套显式阻断) + 含敏感值的文档级附件移除;
       保存后对页面文本/链接/批注/附件(zip 递归展开)/元数据/XMP/书签
       做全类型回读验证, 表单/含图像页等未核验范围以 needs_review
       标注且不宣称 pdf_removed_and_verified。

安全策略:
  - 每个输出文件都做底层回读验证（逐部件搜索已识别的敏感值，
    二进制部件按原始字节搜索；PDF 额外对全部启用类型重新识别，
    覆盖链接目标与附件内容等新表面，而非仅搜索此前已识别的值）；
    任何残留 => 该文件标记 failed 并删除输出，
    不做笼统成功。无法清洗的位置（如二进制部件内的明文、不可靠
    重写的附件内容）会被显式阻断。
  - 打码清单不包含任何原始敏感值。

退出码: 0=全部完成且通过回读验证（可含 needs_review） 2=输入错误或任一文件失败/被阻断
"""
import argparse
import io
import json
import math
import os
import posixpath
import re
import sys
import zipfile

RE_ID = re.compile(r"(?<![0-9Xx])[1-9]\d{5}(?:18|19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?![0-9Xx])")
RE_ID_15 = re.compile(r"(?<!\d)[1-9]\d{5}\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}(?!\d)")
RE_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
RE_BANK = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
RE_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
ID_WEIGHTS = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
ID_CHECK = "10X98765432"
ALL_TYPES = ("id_card", "phone", "bank_card", "email")

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
SS_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT_NS = "http://schemas.openxmlformats.org/package/2006/content-types"
REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
W_P, W_R = "{%s}p" % W_NS, "{%s}r" % W_NS
W_T, W_DELTEXT = "{%s}t" % W_NS, "{%s}delText" % W_NS
A_P, A_R, A_FLD, A_T = "{%s}p" % A_NS, "{%s}r" % A_NS, "{%s}fld" % A_NS, "{%s}t" % A_NS
SS_V = "{%s}v" % SS_NS
PARA_SPECS = {
    "docx": (W_P, (W_R,), (W_T, W_DELTEXT)),
    "pptx": (A_P, (A_R, A_FLD), (A_T,)),
}
THREADED_CT = "application/vnd.ms-excel.threadedcomments+xml"
THREADED_REL_TYPE = "http://schemas.microsoft.com/office/2017/10/relationships/threadedComment"
DROP_WARN_RE = re.compile(r"(chart|oleobject|activex|control|customxml|querytable|connection)", re.I)


class VerificationLeak(Exception):
    def __init__(self, parts):
        self.parts = parts
        super().__init__(
            "verification_leak: sensitive value remains accessible in parts: "
            + ", ".join(parts[:10]))


class UncleanableAttachment(Exception):
    """附件内容含敏感值但格式无法可靠重写（二进制明文/加密/超深嵌套），
    必须显式阻断交付而不是笼统宣称安全。"""


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


def find_hits(text, types=None):
    """返回 (hits, needs_review)。hits: [{type, value, start, end}]，按位置倒序。

    types 为 None 时识别全部类型；否则只保留指定类型（review 项随 id_card 开关）。
    """
    hits = []
    review = []
    taken = []
    want_id = types is None or "id_card" in types
    if want_id:
        for m in RE_ID.finditer(text):
            v = m.group(0)
            if id_checksum_ok(v):
                hits.append({"type": "id_card", "value": v, "start": m.start(), "end": m.end()})
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
        taken.append((m.start(), m.end()))
        if types is not None and "phone" not in types:
            continue
        hits.append({"type": "phone", "value": m.group(0), "start": m.start(), "end": m.end()})
    for m in RE_BANK.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        taken.append((m.start(), m.end()))
        if types is not None and "bank_card" not in types:
            continue
        if luhn_ok(m.group(0)):
            hits.append({"type": "bank_card", "value": m.group(0),
                         "start": m.start(), "end": m.end()})
    for m in RE_EMAIL.finditer(text):
        if any(s <= m.start() < e for s, e in taken):
            continue
        if types is not None and "email" not in types:
            continue
        hits.append({"type": "email", "value": m.group(0), "start": m.start(), "end": m.end()})
    hits.sort(key=lambda h: h["start"], reverse=True)
    review.sort(key=lambda h: h["start"], reverse=True)
    return hits, review


def mask_for(value):
    return "■" * min(len(value), 12)


def _replace_in(text, hits):
    out = []
    last = 0
    for h in sorted(hits, key=lambda x: x["start"]):
        out.append(text[last:h["start"]])
        out.append(mask_for(h["value"]))
        last = h["end"]
    out.append(text[last:])
    return "".join(out)


def _keep_space(el, text_tags):
    if el.tag in text_tags and (el.text != (el.text or "").strip() or el.text == ""):
        el.set(XML_SPACE, "preserve")


def _direct_runs(p, para_tag, run_tags):
    """段落直属 run（排除嵌套段落如文本框内的段落，避免跨结构拼接与重复处理）。"""
    runs = []
    for r in p.iter():
        if r.tag not in run_tags:
            continue
        anc = r.getparent()
        nested = False
        while anc is not None and anc is not p:
            if anc.tag == para_tag:
                nested = True
                break
            anc = anc.getparent()
        if not nested:
            runs.append(r)
    return runs


def _splice_runs(run_nodes, text_tags, hits):
    """按命中（位置倒序、互不重叠）在 run 序列上做跨 run 完整替换。"""
    for h in hits:
        start, end = h["start"], h["end"]
        mask = mask_for(h["value"])
        pos = 0
        first_done = False
        for r in run_nodes:
            ts = [c for c in r if c.tag in text_tags]
            if not ts:
                continue
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
            _keep_space(ts[0], text_tags)
            pos = re_


def scrub_tree(root, para_spec, types, loc_prefix="", skip_v=False):
    """对一棵 XML 树做三级清洗: 段落 run 级 / 文本节点级 / 属性级。

    返回 (hits, review, modified)；hits/review 均为清洗前在原文上识别到的内容。
    """
    hits = []
    review = []
    modified = False
    text_tags_all = para_spec[2] if para_spec is not None else (W_T, W_DELTEXT, A_T)
    if para_spec is not None:
        para_tag, run_tags, text_tags = para_spec
        for pi, p in enumerate(root.iter(para_tag)):
            run_nodes = _direct_runs(p, para_tag, run_tags)
            ts_all = []
            parts = []
            for r in run_nodes:
                ts = [c for c in r if c.tag in text_tags]
                if ts:
                    ts_all.append(ts)
                    parts.append("".join(t.text or "" for t in ts))
            if not ts_all:
                continue
            full = "".join(parts)
            hh, rr = find_hits(full, types)
            review.extend(rr)
            if hh:
                for h in hh:
                    h["location"] = "%s#p%d" % (loc_prefix, pi)
                hits.extend(hh)
                _splice_runs(run_nodes, text_tags, hh)
                modified = True
    run_tags_all = para_spec[1] if para_spec is not None else ()
    for el in root.iter():
        if skip_v and el.tag == SS_V:
            continue
        if el.tag in text_tags_all and run_tags_all:
            parent = el.getparent()
            if parent is not None and parent.tag in run_tags_all:
                continue
        txt = el.text
        if isinstance(txt, str) and txt:
            hh, rr = find_hits(txt, types)
            review.extend(rr)
            if hh:
                el.text = _replace_in(txt, hh)
                _keep_space(el, (W_T, W_DELTEXT, A_T))
                for h in hh:
                    h["location"] = loc_prefix
                hits.extend(hh)
                modified = True
    for el in root.iter():
        for k, v in list(el.attrib.items()):
            if isinstance(v, str) and v:
                hh, rr = find_hits(v, types)
                review.extend(rr)
                if hh:
                    el.set(k, _replace_in(v, hh))
                    for h in hh:
                        h["location"] = loc_prefix + "(attribute)"
                    hits.extend(hh)
                    modified = True
    return hits, review, modified

# ---------- XML 基础 ----------

def parse_xml(data):
    from lxml import etree
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return etree.fromstring(data)
    except Exception:
        return None


def serialize_xml(root):
    from lxml import etree
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)


def _xml_text_blob(data):
    """全文视图: 所有文本节点 + 属性值，各项之间以换行分隔（防止跨节点误拼接）。"""
    root = parse_xml(data)
    if root is None:
        return None
    parts = []
    for el in root.iter():
        if el.text:
            parts.append(el.text)
        for v in el.attrib.values():
            parts.append(v)
    return "\n".join(parts)


XML_SUFFIXES = (".xml", ".rels", ".vml")


def detect_in_part(name, data, types):
    """底层识别: XML 类部件用全文视图，其余按字节 latin-1 识别。"""
    vals = []
    if name.lower().endswith(XML_SUFFIXES):
        blob = _xml_text_blob(data)
        if blob is not None:
            hits, _ = find_hits(blob, types)
            vals.extend(h["value"] for h in hits)
            return vals
    hits, _ = find_hits(data.decode("latin-1"), types)
    vals.extend(h["value"] for h in hits)
    return vals


def _record(result, hits, review):
    for h in hits:
        item = result["redactions"].setdefault(h["type"], {"count": 0, "locations": []})
        item["count"] += 1
        item["locations"].append(h.get("location", ""))
    for h in review:
        result["needs_review"][h["type"]] = result["needs_review"].get(h["type"], 0) + 1


def _finalize(result):
    return {
        "redactions": [{"type": k, "count": v["count"], "locations": v["locations"]}
                       for k, v in result["redactions"].items()],
        "needs_review": [{"type": k, "count": v} for k, v in result["needs_review"].items()],
    }


def verify_ooxml(path, values):
    """底层回读验证: 输出包内任何部件（含二进制）不得再出现已识别的敏感值。"""
    if not values:
        return
    uniq = set(values)
    leaks = []
    with zipfile.ZipFile(path) as z:
        for name in z.namelist():
            data = z.read(name)
            hit = any(v.encode("utf-8") in data for v in uniq)
            if not hit and name.lower().endswith(XML_SUFFIXES):
                blob = _xml_text_blob(data)
                if blob is not None:
                    hit = any(v in blob for v in uniq)
            if hit:
                leaks.append(name)
    if leaks:
        raise VerificationLeak(leaks)


def _write_zip(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)


# ---------- DOCX / PPTX ----------

def process_ooxml(in_path, out_path, kind, types):
    spec = PARA_SPECS[kind]
    with zipfile.ZipFile(in_path) as z:
        entries = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    result = {"redactions": {}, "needs_review": {}}
    detected = []
    new_entries = []
    for name, data in entries:
        if name.lower().endswith(XML_SUFFIXES):
            root = parse_xml(data)
            if root is not None:
                hits, review, modified = scrub_tree(root, spec, types, loc_prefix=name)
                _record(result, hits, review)
                detected.extend(h["value"] for h in hits)
                if modified:
                    data = serialize_xml(root)
        else:
            detected.extend(detect_in_part(name, data, types))
        new_entries.append((name, data))
    if any("/embeddings/" in n for n, _ in entries):
        result["needs_review"]["embedded_object_needs_manual_review"] = 1
    _write_zip(out_path, new_entries)
    verify_ooxml(out_path, detected)
    return _finalize(result), detected


# ---------- XLSX ----------

def _partname_from_rel(base_part, target):
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))


def _sheet_part_map(data):
    """workbook.xml 中 sheet 名称 -> 部件名。"""
    wb = parse_xml(data.get("xl/workbook.xml", b""))
    rels = parse_xml(data.get("xl/_rels/workbook.xml.rels", b""))
    if wb is None or rels is None:
        return {}
    rel_map = {}
    for rel in rels:
        if rel.tag == "{%s}Relationship" % REL_NS:
            rel_map[rel.get("Id")] = rel.get("Target")
    out = {}
    for sh in wb.iter("{%s}sheet" % SS_NS):
        target = rel_map.get(sh.get("{%s}id" % R_NS))
        if target:
            out[sh.get("name")] = _partname_from_rel("xl/workbook.xml", target)
    return out


def _threaded_map(data):
    """sheet 名称 -> 线程批注部件名列表。"""
    out = {}
    sheets = _sheet_part_map(data)
    for name in data:
        m = re.match(r"xl/worksheets/_rels/(sheet\d+)\.xml\.rels$", name)
        if not m:
            continue
        rels = parse_xml(data[name])
        if rels is None:
            continue
        sheet_part = "xl/worksheets/%s.xml" % m.group(1)
        targets = []
        for rel in rels:
            if rel.tag == "{%s}Relationship" % REL_NS \
                    and (rel.get("Type") or "").endswith("/threadedComment"):
                targets.append(_partname_from_rel(sheet_part, rel.get("Target")))
        for sheet_name, part in sheets.items():
            if part == sheet_part and targets:
                out.setdefault(sheet_name, []).extend(targets)
    return out


def _ensure_override(ct_root, partname, content_type):
    pn = "/" + partname
    for ov in ct_root.iter("{%s}Override" % CT_NS):
        if ov.get("PartName") == pn:
            return
    from lxml import etree
    ov = etree.SubElement(ct_root, "{%s}Override" % CT_NS)
    ov.set("PartName", pn)
    ov.set("ContentType", content_type)


def _ensure_sheet_rel(entries_dict, sheet_part, threaded_part):
    from lxml import etree
    rels_part = posixpath.join(posixpath.dirname(sheet_part),
                               "_rels", posixpath.basename(sheet_part) + ".rels")
    target = posixpath.relpath(threaded_part, posixpath.dirname(sheet_part))
    root = None
    if rels_part in entries_dict:
        root = parse_xml(entries_dict[rels_part])
    if root is None:
        root = etree.fromstring(
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
    existing = {rel.get("Id") for rel in root}
    n = 1
    while "rIdTC%d" % n in existing:
        n += 1
    rel = etree.SubElement(root, "{%s}Relationship" % REL_NS)
    rel.set("Id", "rIdTC%d" % n)
    rel.set("Type", THREADED_REL_TYPE)
    rel.set("Target", target)
    entries_dict[rels_part] = serialize_xml(root)
    return True


def process_xlsx(in_path, out_path, types):
    from openpyxl import load_workbook
    with zipfile.ZipFile(in_path) as z:
        orig = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    orig_data = dict(orig)
    result = {"redactions": {}, "needs_review": {}}
    detected = []
    for name, data in orig:
        detected.extend(detect_in_part(name, data, types))
    threaded = _threaded_map(orig_data)

    wb = load_workbook(in_path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                if isinstance(v, str) and v:
                    hits, _ = find_hits(v, types)
                    if hits:
                        cell.value = _replace_in(v, hits)
                        for h in hits:
                            detected.append(h["value"])
                        _record(result, [dict(h, location="%s!%s" % (ws.title, cell.coordinate))], [])
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    if isinstance(v, float):
                        if not math.isfinite(v) or v != int(v):
                            continue
                        s = str(int(v))
                    else:
                        s = str(v)
                    if len(s) < 11:
                        continue
                    hits, review = find_hits(s, types)
                    _record(result, [], review)
                    if hits:
                        cell.value = _replace_in(s, hits)
                        for h in hits:
                            detected.append(h["value"])
                        _record(result, [dict(h, location="%s!%s" % (ws.title, cell.coordinate))], [])
                cmt = cell.comment
                if cmt is not None:
                    if cmt.text:
                        hits, _ = find_hits(cmt.text, types)
                        if hits:
                            cmt.text = _replace_in(cmt.text, hits)
                            for h in hits:
                                detected.append(h["value"])
                            _record(result, [dict(h, location="%s!%s(comment)" % (ws.title, cell.coordinate))], [])
                    if cmt.author:
                        hits, _ = find_hits(cmt.author, types)
                        if hits:
                            cmt.author = _replace_in(cmt.author, hits)
                            for h in hits:
                                detected.append(h["value"])
                            _record(result, [dict(h, location="%s!%s(comment-author)" % (ws.title, cell.coordinate))], [])
    for prop in ("creator", "lastModifiedBy", "title", "subject", "description",
                 "keywords", "identifier", "language", "category", "contentStatus"):
        val = getattr(wb.properties, prop, None)
        if isinstance(val, str) and val:
            hits, _ = find_hits(val, types)
            if hits:
                setattr(wb.properties, prop, _replace_in(val, hits))
                for h in hits:
                    detected.append(h["value"])
                _record(result, [dict(h, location="docProps")], [])
    wb.save(out_path)

    with zipfile.ZipFile(out_path) as z:
        cur = [(i.filename, z.read(i.filename)) for i in z.infolist()]
    entries_dict = dict(cur)
    for name, data in cur:
        if not name.lower().endswith(XML_SUFFIXES):
            continue
        root = parse_xml(data)
        if root is None:
            continue
        hits, review, modified = scrub_tree(
            root, None, types, loc_prefix=name,
            skip_v=name.startswith("xl/worksheets/"))
        _record(result, hits, review)
        detected.extend(h["value"] for h in hits)
        if modified:
            entries_dict[name] = serialize_xml(root)

    preserved = 0
    out_sheets = _sheet_part_map(entries_dict)
    ct = parse_xml(entries_dict.get("[Content_Types].xml", b""))
    for sheet_name, parts in threaded.items():
        sheet_part = out_sheets.get(sheet_name)
        if not sheet_part:
            continue
        for tp in parts:
            if tp in entries_dict:
                continue
            root = parse_xml(orig_data.get(tp, b""))
            if root is None:
                continue
            hits, review, _m = scrub_tree(root, None, types, loc_prefix=tp)
            _record(result, hits, review)
            detected.extend(h["value"] for h in hits)
            entries_dict[tp] = serialize_xml(root)
            if ct is not None:
                _ensure_override(ct, tp, THREADED_CT)
            if _ensure_sheet_rel(entries_dict, sheet_part, tp):
                preserved += 1
    if ct is not None:
        entries_dict["[Content_Types].xml"] = serialize_xml(ct)
    _write_zip(out_path, list(entries_dict.items()))

    with zipfile.ZipFile(out_path) as z:
        out_names = set(z.namelist())
    dropped = sorted(
        n for n, _ in orig
        if n not in out_names and DROP_WARN_RE.search(n)
        and not n.startswith("xl/threadedComments/"))
    fin = _finalize(result)
    if dropped:
        fin["dropped_unmodeled_parts"] = dropped
    if preserved:
        fin["threaded_comments_preserved"] = preserved
    verify_ooxml(out_path, detected)
    return fin, detected

# ---------- PDF ----------

ZIP_HEADS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
MAX_NEST_DEPTH = 4


def _is_zip_data(data):
    return data[:4] in ZIP_HEADS


def _text_blob_clean(data, types):
    """文本视图清洗: 返回 (bytes, hits); 无法按文本处理时返回 None。"""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        hits, _ = find_hits(text, types)
        if hits:
            return _replace_in(text, hits).encode("utf-8"), hits
        return data, []
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        codec = "utf-16-le" if data[:2] == b"\xff\xfe" else "utf-16-be"
        try:
            text = data[2:].decode(codec)
        except UnicodeDecodeError:
            text = None
        if text is not None:
            hits, _ = find_hits(text, types)
            if hits:
                return data[:2] + _replace_in(text, hits).encode(codec), hits
            return data, []
    return None


def _blob_views(data, depth=0):
    """字节内容的可读文本视图: utf-8 / 带 BOM 的 utf-16 / latin-1;
    zip 容器递归展开条目(深度受限)。"""
    if _is_zip_data(data):
        if depth >= MAX_NEST_DEPTH:
            return [data.decode("latin-1", errors="ignore")]
        views = []
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for n in z.namelist():
                    views.extend(_blob_views(z.read(n), depth + 1))
        except Exception:
            views.append(data.decode("latin-1", errors="ignore"))
        return views
    try:
        return [data.decode("utf-8")]
    except UnicodeDecodeError:
        pass
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        codec = "utf-16-le" if data[:2] == b"\xff\xfe" else "utf-16-be"
        try:
            return [data[2:].decode(codec)]
        except UnicodeDecodeError:
            pass
    return [data.decode("latin-1", errors="ignore")]


def _clean_attachment_blob(blob, types, where, depth=0):
    """清洗附件内容, 返回 (cleaned_bytes, all_hits)。

    zip 容器逐条目递归重写(保留无关条目); 文本视图做全类型替换;
    二进制明文命中或超深嵌套/加密压缩包 => 抛 UncleanableAttachment
    阻断整份交付。
    """
    if _is_zip_data(blob):
        if depth >= MAX_NEST_DEPTH:
            raise UncleanableAttachment(
                "%s: nested archive beyond supported depth" % where)
        try:
            with zipfile.ZipFile(io.BytesIO(blob)) as zin:
                items = [(n, zin.read(n)) for n in zin.namelist()]
        except Exception as e:
            raise UncleanableAttachment(
                "%s: unreadable or encrypted archive (%s)"
                % (where, type(e).__name__))
        out = io.BytesIO()
        all_hits = []
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for n, data in items:
                cleaned, hits = _clean_attachment_blob(
                    data, types, "%s:%s" % (where, n), depth + 1)
                zout.writestr(n, cleaned)
                all_hits.extend(hits)
        return out.getvalue(), all_hits
    res = _text_blob_clean(blob, types)
    if res is not None:
        return res
    hits, _ = find_hits(blob.decode("latin-1"), types)
    if hits:
        raise UncleanableAttachment(
            "%s: sensitive values in non-rewritable binary content" % where)
    return blob, []


def _clean_pdf_attachment_annot(annot, result, detected, types, where):
    """文件附件批注: 嵌入内容与文件名含敏感值时清洗重写并保留无关内容;
    无法可靠清洗时抛 UncleanableAttachment(整文件阻断, 不笼统成功)。"""
    try:
        blob = annot.get_file()
    except Exception:
        blob = None
    cleaned = None
    hits_all = []
    if blob:
        cleaned, hits_all = _clean_attachment_blob(blob, types, where)
    info = annot.info or {}
    fname = info.get("content")
    fhits = []
    if isinstance(fname, str) and fname:
        fhits, _ = find_hits(fname, types)
    if not hits_all and not fhits:
        return
    kw = {}
    if cleaned is not None:
        kw["buffer_"] = cleaned
    if fhits:
        new_name = _replace_in(fname, fhits)
        kw["filename"] = new_name
        kw["ufilename"] = new_name
    try:
        annot.update_file(**kw)
    except Exception:
        raise UncleanableAttachment("%s: update_file failed" % where)
    for h in hits_all:
        detected.append(h["value"])
    _record(result, [dict(h, location=where) for h in hits_all], [])
    for h in fhits:
        detected.append(h["value"])
    _record(result, [dict(h, location=where + ":filename") for h in fhits], [])


def _pdf_scopes(path):
    """回读视图: 页面文本/链接目标(URI/启动文件等)/表单域值/批注信息/
    文件附件批注与文档级附件的内容(zip 递归展开)/元数据/XMP/书签。"""
    import pymupdf
    doc = pymupdf.open(path)
    scopes = []
    try:
        for page in doc:
            scopes.append(page.get_text("text"))
            try:
                links = list(page.get_links() or [])
            except Exception:
                links = []
            for lnk in links:
                for key in ("uri", "file", "name", "nameddest"):
                    v = lnk.get(key)
                    if isinstance(v, str) and v:
                        scopes.append(v)
            try:
                widgets = list(page.widgets() or [])
            except Exception:
                widgets = []
            for w in widgets:
                v = w.field_value
                if isinstance(v, str) and v:
                    scopes.append(v)
            try:
                annots = list(page.annots() or [])
            except Exception:
                annots = []
            for a in annots:
                info = a.info or {}
                scopes.extend(str(v) for v in info.values() if isinstance(v, str))
                if a.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                    try:
                        blob = a.get_file()
                    except Exception:
                        blob = None
                    if blob:
                        scopes.extend(_blob_views(blob))
        md = doc.metadata or {}
        scopes.extend(str(v) for v in md.values() if isinstance(v, str))
        try:
            scopes.append(doc.get_xml_metadata() or "")
        except Exception:
            pass
        try:
            scopes.extend(e[1] or "" for e in doc.get_toc())
        except Exception:
            pass
        try:
            for name in doc.embfile_names():
                info = doc.embfile_info(name) or {}
                scopes.extend(str(v) for v in info.values() if isinstance(v, str))
                try:
                    scopes.extend(_blob_views(doc.embfile_get(name)))
                except Exception:
                    pass
        except Exception:
            pass
    finally:
        doc.close()
    return scopes


def process_pdf(in_path, out_path, types):
    import pymupdf
    doc = pymupdf.open(in_path)
    result = {"redactions": {}, "needs_review": {}}
    detected = []
    removed_emb = 0
    try:
        for pno, page in enumerate(doc, start=1):
            text = page.get_text("text")
            hits, review = find_hits(text, types)
            _record(result, [], review)
            for h in hits:
                detected.append(h["value"])
                rects = page.search_for(h["value"])
                if not rects:
                    result["needs_review"]["search_miss_needs_manual"] = \
                        result["needs_review"].get("search_miss_needs_manual", 0) + 1
                    continue
                for rect in rects:
                    page.add_redact_annot(rect, fill=(0, 0, 0))
                _record(result, [dict(h, location="page %d" % pno)], [])
            page.apply_redactions()

            try:
                links = list(page.get_links() or [])
            except Exception:
                links = []
            for lnk in links:
                dirty = False
                for key in ("uri", "file", "name", "nameddest"):
                    v = lnk.get(key)
                    if not isinstance(v, str) or not v:
                        continue
                    lhits, _ = find_hits(v, types)
                    if not lhits:
                        continue
                    lnk[key] = _replace_in(v, lhits)
                    dirty = True
                    for h in lhits:
                        detected.append(h["value"])
                    _record(result, [dict(h, location="link:page %d" % pno)
                                     for h in lhits], [])
                if dirty:
                    try:
                        page.update_link(lnk)
                    except Exception:
                        result["needs_review"]["link_update_failed_needs_manual"] = \
                            result["needs_review"].get(
                                "link_update_failed_needs_manual", 0) + 1

            try:
                widgets = list(page.widgets() or [])
            except Exception:
                widgets = []
            for w in widgets:
                v = w.field_value
                if not isinstance(v, str) or not v:
                    continue
                whits, _ = find_hits(v, types)
                if not whits:
                    continue
                try:
                    w.field_value = _replace_in(v, whits)
                    w.update()
                    for h in whits:
                        detected.append(h["value"])
                    _record(result, [dict(h, location="form_field:page %d" % pno)
                                     for h in whits], [])
                except Exception:
                    result["needs_review"]["form_field_update_failed_needs_manual"] = \
                        result["needs_review"].get(
                            "form_field_update_failed_needs_manual", 0) + 1

        md = dict(doc.metadata or {})
        md_changed = False
        for k, v in md.items():
            if isinstance(v, str) and v:
                hits, _ = find_hits(v, types)
                if hits:
                    md[k] = _replace_in(v, hits)
                    md_changed = True
                    for h in hits:
                        detected.append(h["value"])
                    _record(result, [dict(h, location="metadata:" + k)], [])
        if md_changed:
            doc.set_metadata(md)

        try:
            xmp = doc.get_xml_metadata() or ""
        except Exception:
            xmp = ""
        if xmp:
            hits, _ = find_hits(xmp, types)
            if hits:
                for h in hits:
                    detected.append(h["value"])
                doc.set_xml_metadata(_replace_in(xmp, hits))
                _record(result, [dict(h, location="xmp_metadata") for h in hits], [])

        try:
            toc = doc.get_toc()
        except Exception:
            toc = []
        toc_changed = False
        for entry in toc:
            hits, _ = find_hits(entry[1] or "", types)
            if hits:
                entry[1] = _replace_in(entry[1], hits)
                toc_changed = True
                for h in hits:
                    detected.append(h["value"])
                _record(result, [dict(h, location="toc") for h in hits], [])
        if toc_changed:
            doc.set_toc(toc)

        for pno, page in enumerate(doc, start=1):
            try:
                annots = list(page.annots() or [])
            except Exception:
                annots = []
            for annot in annots:
                info = annot.info or {}
                for key in ("content", "title", "subject"):
                    v = info.get(key)
                    if not isinstance(v, str) or not v:
                        continue
                    hits, _ = find_hits(v, types)
                    if hits:
                        try:
                            annot.set_info(**{key: _replace_in(v, hits)})
                            annot.update()
                        except Exception:
                            pass
                        for h in hits:
                            detected.append(h["value"])
                        _record(result, [dict(h, location="annotation:" + key)], [])
                if annot.type[0] == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                    _clean_pdf_attachment_annot(
                        annot, result, detected, types,
                        "file_attachment:page %d" % pno)

        if getattr(doc, "is_form_pdf", False):
            result["needs_review"]["form_fields_present_needs_manual_review"] = 1

        try:
            names = list(doc.embfile_names())
        except Exception:
            names = []
        for name in names:
            hit_here = False
            try:
                info = doc.embfile_info(name) or {}
            except Exception:
                info = {}
            for key in ("name", "filename", "ufilename", "description"):
                v = info.get(key)
                if isinstance(v, str) and v:
                    hh, _ = find_hits(v, types)
                    if hh:
                        hit_here = True
                        for h in hh:
                            detected.append(h["value"])
            try:
                blob = doc.embfile_get(name)
            except Exception:
                blob = None
            if blob:
                for view in _blob_views(blob):
                    hh, _ = find_hits(view, types)
                    if hh:
                        hit_here = True
                        for h in hh:
                            detected.append(h["value"])
                        break
            if hit_here:
                try:
                    doc.embfile_del(name)
                    removed_emb += 1
                except Exception:
                    pass

        unverified = {}
        if getattr(doc, "is_form_pdf", False):
            unverified["form_fields"] = "表单结构/脚本未逐项核验"
        img_pages = []
        for pno, page in enumerate(doc, start=1):
            try:
                has_img = bool(page.get_images(full=True))
            except Exception:
                has_img = True
            if has_img:
                img_pages.append(pno)
        if img_pages:
            result["needs_review"]["images_present_not_ocr_scanned"] = len(img_pages)
            unverified["image_pages_not_ocr_scanned"] = img_pages

        doc.save(out_path, garbage=3, deflate=True)
    finally:
        doc.close()

    uniq = set(detected)
    leaks = []
    for scope in _pdf_scopes(out_path):
        hh, _ = find_hits(scope, types)
        if hh:
            leaks.append("pdf surface (%s): %s"
                         % (hh[0]["type"], "content/link/annotation/attachment"))
            break
        if any(v in scope for v in uniq):
            leaks.append("pdf content/metadata/attachment")
            break
    raw = open(out_path, "rb").read()
    for v in uniq:
        if v.encode("utf-8") in raw:
            leaks.append("pdf raw bytes")
            break
    if leaks:
        raise VerificationLeak(sorted(set(leaks)))

    fin = _finalize(result)
    if unverified:
        fin["pdf_removed_and_verified"] = False
        fin["unverified_surfaces"] = unverified
    else:
        fin["pdf_removed_and_verified"] = True
    if removed_emb:
        fin["removed_embedded_files"] = removed_emb
    return fin, detected


# ---------- main ----------

def _output_name(f, input_root, is_dir):
    ext = os.path.splitext(f)[1].lower()
    if is_dir:
        rel = os.path.relpath(f, input_root)
        rel_no_ext, _ = os.path.splitext(rel)
        flat = re.sub(r'[\\/:*?"<>|]+', "__", rel_no_ext)
        return "%s_REDACTED%s" % (flat, ext)
    stem = os.path.splitext(os.path.basename(f))[0]
    return "%s_REDACTED%s" % (stem, ext)


def main():
    ap = argparse.ArgumentParser(description="敏感信息打码(DOCX/XLSX/PPTX/PDF)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--report")
    ap.add_argument("--types", help="逗号分隔: id_card,phone,bank_card,email")
    args = ap.parse_args()

    types = None
    if args.types:
        types = {t.strip() for t in args.types.split(",") if t.strip()}
        bad = types - set(ALL_TYPES)
        if bad:
            print("ERROR: unknown types: %s" % ",".join(sorted(bad)), file=sys.stderr)
            return 2

    is_dir = os.path.isdir(args.input)
    if not is_dir and not os.path.isfile(args.input):
        print("ERROR: input not found", file=sys.stderr)
        return 2
    outdir_abs = os.path.normcase(os.path.abspath(args.outdir))
    files = []
    if is_dir:
        for root, dirs, names in os.walk(args.input):
            dirs[:] = [d for d in dirs
                       if os.path.normcase(os.path.abspath(os.path.join(root, d))) != outdir_abs]
            for n in sorted(names):
                if n.lower().endswith((".docx", ".xlsx", ".pptx", ".pdf")) and not n.startswith("~$"):
                    p = os.path.join(root, n)
                    if os.path.normcase(os.path.abspath(p)).startswith(outdir_abs + os.sep):
                        continue
                    files.append(p)
    else:
        if not args.input.lower().endswith((".docx", ".xlsx", ".pptx", ".pdf")):
            print("ERROR: unsupported file type", file=sys.stderr)
            return 2
        files = [args.input]
    if not files:
        print("ERROR: no processable files", file=sys.stderr)
        return 2

    os.makedirs(args.outdir, exist_ok=True)
    results = []
    all_values = []
    used_outputs = {}
    for f in files:
        ext = os.path.splitext(f)[1].lower()
        out_name = _output_name(f, args.input, is_dir)
        out_path = os.path.join(args.outdir, out_name)
        key = os.path.normcase(os.path.abspath(out_path))
        if key in used_outputs:
            results.append({"file": f, "output": None, "failed": True,
                            "error": "output_name_collision: 另一输入已映射到相同输出名 " + out_name,
                            "redactions": [], "needs_review": []})
            continue
        used_outputs[key] = True
        try:
            if ext == ".docx":
                r, vals = process_ooxml(f, out_path, "docx", types)
            elif ext == ".pptx":
                r, vals = process_ooxml(f, out_path, "pptx", types)
            elif ext == ".xlsx":
                r, vals = process_xlsx(f, out_path, types)
            else:
                r, vals = process_pdf(f, out_path, types)
            all_values.extend(vals)
            r["file"] = f
            r["output"] = out_path
            r["failed"] = False
            results.append(r)
        except Exception as e:
            if os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            results.append({"file": f, "output": None, "failed": True,
                            "error": "%s: %s" % (type(e).__name__, e),
                            "redactions": [], "needs_review": []})

    manifest = {"files": results,
                "summary": {"files_processed": len([r for r in results if not r["failed"]]),
                            "files_failed": len([r for r in results if r["failed"]]),
                            "total_redactions": sum(
                                sum(x["count"] for x in r.get("redactions", []))
                                for r in results)}}
    report_path = args.report or os.path.join(args.outdir, "redact-manifest.json")
    raw = json.dumps(manifest, ensure_ascii=False, indent=2)
    for v in sorted(set(all_values), key=len, reverse=True):
        raw = raw.replace(v, mask_for(v))
    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write(raw)
    print(json.dumps(manifest["summary"], ensure_ascii=False))
    if manifest["summary"]["files_failed"]:
        print("ERROR: %d file(s) failed or blocked" % manifest["summary"]["files_failed"],
              file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
