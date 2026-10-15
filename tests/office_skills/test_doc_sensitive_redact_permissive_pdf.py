"""doc-sensitive-redact PDF 迁移后独立验收（宽松栈，无 PyMuPDF）。

与原黑盒测试互补：本文件只用 pypdf(BSD)/pdfplumber(MIT)/lxml/openpyxl
构造与回读，验证迁移后 PDF 正文与隐藏表面（内容流实际删除、Form XObject
递归、元数据/XMP/书签/批注/链接、表单 V 与外观 AP、附件 zip/Office 跨 run、
文档级附件移除、孤立对象无残留）。不 import 被测实现模块。
"""
import io
import json
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import pdfplumber  # noqa: E402
import pypdf  # noqa: E402
from pypdf.generic import (  # noqa: E402
    ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
    NumberObject, TextStringObject,
)

BODY_EMAIL = "literal@example.test"
XOBJ_EMAIL = "nested@example.test"
META_EMAIL = "meta@example.test"
XMP_EMAIL = "xmp@example.test"
TOC_EMAIL = "toc@example.test"
ANNOT_EMAIL = "annot@example.test"
LINK_EMAIL = "link@example.test"
ATT_EMAIL = "attachment@example.test"
SPLIT_HEAD = "split-across-runs@"
SPLIT_TAIL = "example.test"
SPLIT_EMAIL = SPLIT_HEAD + SPLIT_TAIL
EMB_EMAIL = "embedded@example.test"
FORM_EMAIL = "form@example.test"
PUBLIC = "PUBLIC_PREFIX PUBLIC_SUFFIX"
ATTACH_PUBLIC = "Public attachment text."


def _helv_font():
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    })


def _new_writer():
    w = pypdf.PdfWriter()
    page = w.add_blank_page(width=612, height=792)
    font = _helv_font()
    res = DictionaryObject({
        NameObject("/Font"): DictionaryObject({NameObject("/helv"): font})})
    page[NameObject("/Resources")] = res
    return w, page


def _set_content(w, page, data):
    stream = DecodedStreamObject()
    stream.set_data(data)
    page.replace_contents(stream)


def _annot_ref(w, page, annot):
    ref = w._add_object(annot)
    page[NameObject("/Annots")] = ArrayObject(
        list(page.get("/Annots") or []) + [ref])
    return ref


def _make_docx(split):
    body = ("<w:p><w:r><w:t>Public attachment text.</w:t></w:r></w:p>")
    if split:
        body += ("<w:p><w:r><w:t>%s</w:t></w:r><w:r><w:t>%s</w:t></w:r></w:p>"
                 % (SPLIT_HEAD, SPLIT_TAIL))
    else:
        body += ("<w:p><w:r><w:t>contact %s</w:t></w:r></w:p>" % ATT_EMAIL)
    doc = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/'
           'wordprocessingml/2006/main"><w:body>%s</w:body></w:document>' % body)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/'
                   '2006/content-types"/>')
        z.writestr("word/document.xml", doc)
    return buf.getvalue()


def _filespec(w, name, data):
    stream = DecodedStreamObject()
    stream.set_data(data)
    return DictionaryObject({
        NameObject("/Type"): NameObject("/Filespec"),
        NameObject("/F"): TextStringObject(name),
        NameObject("/UF"): TextStringObject(name),
        NameObject("/EF"): DictionaryObject(
            {NameObject("/F"): w._add_object(stream)}),
    })


def _load_redact():
    """按路径加载技能实现模块（仅供失败注入边界测试使用，不修改实现）。"""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)))), "skills", "office",
        "doc-sensitive-redact", "scripts", "redact.py")
    spec = importlib.util.spec_from_file_location("dsr_redact_impl", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _one_page_pdf(path, content):
    w, page = _new_writer()
    _set_content(w, page, content)
    w.write(str(path))


def _read_page_text(path):
    with pdfplumber.open(path) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def _outline_titles(reader):
    out = []
    root = reader.trailer["/Root"]
    ol = root.get("/Outlines")
    if ol is None:
        return out
    seen = set()
    stack = [ol]
    while stack:
        cur = stack.pop()
        try:
            cur = cur.get_object()
        except Exception:
            continue
        if id(cur) in seen:
            continue
        seen.add(id(cur))
        title = cur.get("/Title")
        if title is not None:
            out.append(str(title))
        for key in ("/First", "/Next"):
            if cur.get(key) is not None:
                stack.append(cur.get(key))
    return out


def _load_manifest(outdir):
    return json.loads((outdir / "redact-manifest.json").read_text(encoding="utf-8"))


def _stream_texts(reader):
    out = []
    for v in getattr(reader, "_objects", {}).values():
        try:
            if hasattr(v, "get_data"):
                out.append(v.get_data())
        except Exception:
            pass
    return out


def _assert_no_residue(path, values):
    raw = path.read_bytes()
    for v in values:
        assert v.encode("utf-8") not in raw, "raw residue: " + v
    reader = pypdf.PdfReader(str(path))
    for blob in _stream_texts(reader):
        for v in values:
            assert v.encode("utf-8") not in blob, "object/stream residue: " + v


# ---------- 正文：literal Tj + 元数据/XMP/书签/批注/链接 ----------

def test_redact_pdf_literal_body_and_hidden_surfaces(workspace):
    w, page = _new_writer()
    _set_content(w, page, ("BT /helv 12 Tf 72 720 Td "
                           "(%s %s) Tj ET" % (PUBLIC, BODY_EMAIL)).encode("latin-1"))
    w.add_metadata({"/Author": META_EMAIL, "/Title": "Report"})
    xmp = DecodedStreamObject()
    xmp.set_data(("<x:xmpmeta><dc:creator>%s</dc:creator></x:xmpmeta>"
                  % XMP_EMAIL).encode("utf-8"))
    w._root_object[NameObject("/Metadata")] = w._add_object(xmp)

    item = DictionaryObject({NameObject("/Title"):
                             TextStringObject("chapter by " + TOC_EMAIL)})
    item_ref = w._add_object(item)
    outlines = DictionaryObject({
        NameObject("/Type"): NameObject("/Outlines"),
        NameObject("/First"): item_ref,
        NameObject("/Last"): item_ref,
        NameObject("/Count"): NumberObject(1),
    })
    ol_ref = w._add_object(outlines)
    item[NameObject("/Parent")] = ol_ref
    item[NameObject("/Dest")] = ArrayObject(
        [page.indirect_reference, NameObject("/Fit")])
    w._root_object[NameObject("/Outlines")] = ol_ref

    _annot_ref(w, page, DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Text"),
        NameObject("/Rect"): ArrayObject([NumberObject(200), NumberObject(600),
                                          NumberObject(216), NumberObject(616)]),
        NameObject("/Contents"): TextStringObject("note " + ANNOT_EMAIL),
    }))
    _annot_ref(w, page, DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([NumberObject(72), NumberObject(400),
                                          NumberObject(260), NumberObject(420)]),
        NameObject("/A"): DictionaryObject({
            NameObject("/S"): NameObject("/URI"),
            NameObject("/URI"): TextStringObject("mailto:" + LINK_EMAIL),
        }),
    }))
    src = workspace / "literal_surfaces.pdf"
    w.write(str(src))

    r = run_script("doc-sensitive-redact", "redact.py",
                   ["--input", src, "--outdir", workspace / "out"])
    assert r.returncode == 0, r.stderr + r.stdout
    out = workspace / "out" / "literal_surfaces_REDACTED.pdf"
    text = _read_page_text(out)
    assert "PUBLIC_PREFIX" in text and "PUBLIC_SUFFIX" in text
    for v in (BODY_EMAIL, META_EMAIL, XMP_EMAIL, TOC_EMAIL, ANNOT_EMAIL, LINK_EMAIL):
        assert v not in text

    reader = pypdf.PdfReader(str(out))
    assert META_EMAIL not in (reader.metadata.get("/Author") or "")
    xmp_out = reader.trailer["/Root"]["/Metadata"].get_object().get_data().decode(
        "utf-8", "replace")
    assert XMP_EMAIL not in xmp_out
    toc_out = " ".join(_outline_titles(reader))
    assert TOC_EMAIL not in toc_out
    annots = reader.pages[0].get("/Annots")
    uris, contents = [], []
    for a in annots:
        o = a.get_object()
        if o.get("/Subtype") == "/Link":
            uris.append(str(o["/A"]["/URI"]))
        if o.get("/Contents"):
            contents.append(str(o["/Contents"]))
    assert all(LINK_EMAIL not in u for u in uris), uris
    assert all(ANNOT_EMAIL not in c for c in contents), contents
    _assert_no_residue(out, [BODY_EMAIL, META_EMAIL, XMP_EMAIL, TOC_EMAIL,
                             ANNOT_EMAIL, LINK_EMAIL])

    man = _load_manifest(workspace / "out")
    pf = man["files"][0]
    assert pf["failed"] is False
    assert pf["pdf_removed_and_verified"] is True
    types = {x["type"] for x in pf["redactions"]}
    assert "email" in types
    assert LINK_EMAIL not in json.dumps(man, ensure_ascii=False)


# ---------- 正文：嵌套 Form XObject ----------

def test_redact_pdf_nested_form_xobject_body(workspace):
    w, page = _new_writer()
    inner = DecodedStreamObject()
    inner.set_data(("BT /helv 12 Tf 72 720 Td (%s %s) Tj ET"
                    % (PUBLIC, XOBJ_EMAIL)).encode("latin-1"))
    inner_ref = w._add_object(inner)
    inner[NameObject("/Type")] = NameObject("/XObject")
    inner[NameObject("/Subtype")] = NameObject("/Form")
    inner[NameObject("/BBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(612), NumberObject(792)])
    inner[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/helv"): _helv_font()})})

    outer = DecodedStreamObject()
    outer.set_data(b"/fullpage Do")
    outer_ref = w._add_object(outer)
    outer[NameObject("/Type")] = NameObject("/XObject")
    outer[NameObject("/Subtype")] = NameObject("/Form")
    outer[NameObject("/BBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(612), NumberObject(792)])
    outer[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/fullpage"): inner_ref})})

    page["/Resources"][NameObject("/XObject")] = DictionaryObject(
        {NameObject("/fzFrm0"): outer_ref})
    _set_content(w, page, b"q /fzFrm0 Do Q")

    src = workspace / "xobj.pdf"
    w.write(str(src))
    r = run_script("doc-sensitive-redact", "redact.py",
                   ["--input", src, "--outdir", workspace / "out"])
    assert r.returncode == 0, r.stderr + r.stdout
    out = workspace / "out" / "xobj_REDACTED.pdf"
    text = _read_page_text(out)
    assert "PUBLIC_PREFIX" in text and "PUBLIC_SUFFIX" in text
    assert XOBJ_EMAIL not in text
    _assert_no_residue(out, [XOBJ_EMAIL])
    man = _load_manifest(workspace / "out")
    assert man["files"][0]["failed"] is False
    assert man["files"][0]["pdf_removed_and_verified"] is True


# ---------- 附件：zip 内 OOXML 跨 run 值 + 公共内容保留 ----------

def test_redact_pdf_attachment_split_runs(workspace):
    w, page = _new_writer()
    _set_content(w, page, ("BT /helv 12 Tf 72 720 Td (%s) Tj ET"
                           % PUBLIC).encode("latin-1"))
    fs = _filespec(w, "split.docx", _make_docx(split=True))
    _annot_ref(w, page, DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/FileAttachment"),
        NameObject("/Rect"): ArrayObject([NumberObject(72), NumberObject(200),
                                          NumberObject(108), NumberObject(236)]),
        NameObject("/Contents"): TextStringObject("split.docx"),
        NameObject("/FS"): w._add_object(fs),
    }))
    src = workspace / "att.pdf"
    w.write(str(src))
    r = run_script("doc-sensitive-redact", "redact.py",
                   ["--input", src, "--outdir", workspace / "out"])
    assert r.returncode == 0, r.stderr + r.stdout
    out = workspace / "out" / "att_REDACTED.pdf"
    reader = pypdf.PdfReader(str(out))
    blobs = []
    for a in reader.pages[0]["/Annots"]:
        o = a.get_object()
        if o.get("/Subtype") == "/FileAttachment":
            ef = o["/FS"].get_object()["/EF"].get_object()
            blobs.append(ef["/F"].get_object().get_data())
    assert len(blobs) == 1
    with zipfile.ZipFile(io.BytesIO(blobs[0])) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    assert ATTACH_PUBLIC in doc_xml
    assert SPLIT_EMAIL not in doc_xml
    assert SPLIT_HEAD not in doc_xml
    _assert_no_residue(out, [SPLIT_EMAIL, SPLIT_HEAD])
    man = _load_manifest(workspace / "out")
    pf = man["files"][0]
    assert pf["failed"] is False
    assert pf["pdf_removed_and_verified"] is True
    locs = [loc for x in pf["redactions"] if x["type"] == "email"
            for loc in x["locations"]]
    assert any("file_attachment" in loc for loc in locs), locs


# ---------- 文档级附件移除 ----------

def test_redact_pdf_embedded_file_removed(workspace):
    w, page = _new_writer()
    _set_content(w, page, ("BT /helv 12 Tf 72 720 Td (%s) Tj ET"
                           % PUBLIC).encode("latin-1"))
    ef = DictionaryObject({
        NameObject("/Names"): ArrayObject([
            TextStringObject("attach.txt"),
            w._add_object(_filespec(w, "attach.txt",
                                    ("see " + EMB_EMAIL).encode("utf-8"))),
        ])})
    w._root_object[NameObject("/Names")] = DictionaryObject(
        {NameObject("/EmbeddedFiles"): w._add_object(ef)})
    src = workspace / "emb.pdf"
    w.write(str(src))
    r = run_script("doc-sensitive-redact", "redact.py",
                   ["--input", src, "--outdir", workspace / "out"])
    assert r.returncode == 0, r.stderr + r.stdout
    out = workspace / "out" / "emb_REDACTED.pdf"
    _assert_no_residue(out, [EMB_EMAIL])
    reader = pypdf.PdfReader(str(out))
    root = reader.trailer["/Root"]
    names = root.get("/Names")
    if names is not None:
        efd = names.get_object().get("/EmbeddedFiles")
        if efd is not None:
            pairs = efd.get_object().get("/Names") or []
            assert all(str(pairs[i]) != "attach.txt" for i in range(0, len(pairs), 2))
    man = _load_manifest(workspace / "out")
    pf = man["files"][0]
    assert pf["failed"] is False
    assert pf.get("removed_embedded_files") == 1


# ---------- 表单：逻辑值 V 与外观 AP 均清洗，保留 needs_review ----------

def test_redact_pdf_widget_value_and_appearance(workspace):
    w, page = _new_writer()
    _set_content(w, page, ("BT /helv 12 Tf 72 720 Td (%s) Tj ET"
                           % PUBLIC).encode("latin-1"))
    ap = DecodedStreamObject()
    ap.set_data(("BT /helv 10 Tf 72 700 Td (form %s) Tj ET"
                 % FORM_EMAIL).encode("latin-1"))
    ap_ref = w._add_object(ap)
    ap[NameObject("/Type")] = NameObject("/XObject")
    ap[NameObject("/Subtype")] = NameObject("/Form")
    ap[NameObject("/BBox")] = ArrayObject(
        [NumberObject(0), NumberObject(0), NumberObject(300), NumberObject(24)])
    ap[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/helv"): _helv_font()})})
    widget = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Widget"),
        NameObject("/FT"): NameObject("/Tx"),
        NameObject("/T"): TextStringObject("contact"),
        NameObject("/Rect"): ArrayObject([NumberObject(72), NumberObject(680),
                                          NumberObject(300), NumberObject(700)]),
        NameObject("/V"): TextStringObject("form " + FORM_EMAIL),
        NameObject("/AP"): DictionaryObject({NameObject("/N"): ap_ref}),
    })
    wref = _annot_ref(w, page, widget)
    w._root_object[NameObject("/AcroForm")] = w._add_object(DictionaryObject({
        NameObject("/Fields"): ArrayObject([wref])}))
    src = workspace / "form.pdf"
    w.write(str(src))
    r = run_script("doc-sensitive-redact", "redact.py",
                   ["--input", src, "--outdir", workspace / "out"])
    assert r.returncode == 0, r.stderr + r.stdout
    out = workspace / "out" / "form_REDACTED.pdf"
    _assert_no_residue(out, [FORM_EMAIL])
    reader = pypdf.PdfReader(str(out))
    for a in reader.pages[0].get("/Annots") or []:
        o = a.get_object()
        if o.get("/Subtype") == "/Widget":
            assert FORM_EMAIL not in str(o.get("/V") or "")
            apd = o.get("/AP")
            if apd is not None:
                n = apd.get_object().get("/N")
                if hasattr(n, "get_object"):
                    n = n.get_object()
                if hasattr(n, "get_data"):
                    assert FORM_EMAIL.encode() not in n.get_data()
    man = _load_manifest(workspace / "out")
    pf = man["files"][0]
    assert pf["failed"] is False
    assert pf["pdf_removed_and_verified"] is False
    assert "form_fields" in pf.get("unverified_surfaces", {})
    review = {x["type"] for x in pf["needs_review"]}
    assert "form_fields_present_needs_manual_review" in review


# ---------- 边界：文本回读失败 fail closed / 输入抽取不可用不谎报完整核验 ----------

def test_pdf_output_text_readback_failure_fails_closed(workspace, monkeypatch):
    redact = _load_redact()
    src = workspace / "in.pdf"
    _one_page_pdf(src, ("BT /helv 12 Tf 72 720 Td (%s %s) Tj ET"
                        % (PUBLIC, BODY_EMAIL)).encode("latin-1"))
    before = src.read_bytes()

    import pdfplumber

    def boom(*a, **k):
        raise RuntimeError("injected pdfplumber failure")

    monkeypatch.setattr(pdfplumber, "open", boom)
    out = workspace / "out.pdf"
    with pytest.raises(RuntimeError):
        redact.process_pdf(str(src), str(out), None)
    # fail closed: 不产生被当作成功的产物; 原始输入不变
    assert src.read_bytes() == before


def test_pdf_input_text_extraction_unavailable_is_not_verified(workspace):
    redact = _load_redact()
    src = workspace / "in.pdf"
    _one_page_pdf(src, ("BT /helv 12 Tf 72 720 Td (%s %s) Tj ET"
                        % (PUBLIC, BODY_EMAIL)).encode("latin-1"))

    def boom(_p):
        raise RuntimeError("injected text extraction failure")

    orig = redact._pdf_page_texts
    redact._pdf_page_texts = boom
    try:
        out = workspace / "out.pdf"
        fin, _det = redact.process_pdf(str(src), str(out), None)
    finally:
        redact._pdf_page_texts = orig
    # 输入文本抽取不可用时不得声称完整核验
    assert fin["pdf_removed_and_verified"] is False
    assert "input_page_text_extraction" in fin.get("unverified_surfaces", {})
    review = {x["type"] for x in fin["needs_review"]}
    assert "text_extraction_unverified_needs_manual" in review
