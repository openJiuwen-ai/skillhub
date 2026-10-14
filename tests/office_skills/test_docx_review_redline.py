"""docx-review-redline 黑盒测试：批注线程 + 修订 + 定位 + 合并/异常路径。

独立金标准：期望文本/结构全部手工推导，不复用被测实现计算；
接受语义 = 去掉 w:del 内容、保留 w:ins 内容（与 Word“接受修订”一致）。
"""
import hashlib
import json
import os
import posixpath
import re
import sys
import zipfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402


def build_draft(path):
    doc = Document()
    doc.add_paragraph("第一段介绍背景。")
    # AAA OLD ZZZ: 修正 harvey 中间子串错序用例
    doc.add_paragraph("AAA OLD ZZZ")
    # 表格混排
    doc.add_paragraph("表格前的段落，包含 OLD 这个词。")
    table = doc.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "数值 OLD 备用"
    table.cell(1, 0).text = "备注"
    table.cell(1, 1).text = "正常内容"
    # 跨 run 文本
    p = doc.add_paragraph()
    for piece in ("跨run目标", "文本", "尾部"):
        p.add_run(piece)
    doc.save(path)


def build_annotated(path):
    """构造已有批注(id=5)与既有插入修订(w:ins id=101)的输入。"""
    doc = Document()
    p1 = doc.add_paragraph()
    r = p1._p.makeelement(qn("w:r"), {})
    t = r.makeelement(qn("w:t"), {})
    t.text = "OLD"
    r.append(t)
    ins = p1._p.makeelement(qn("w:ins"), {})
    ins.set(qn("w:id"), "101")
    ins.set(qn("w:author"), "bob")
    ins.set(qn("w:date"), "2026-01-01T00:00:00Z")
    ins.append(r)
    p1._p.append(ins)
    p2 = doc.add_paragraph("plain OLD text")
    for tag, extra in (("w:commentRangeStart", 0), ("w:commentRangeEnd", None)):
        el = p2._p.makeelement(qn(tag), {})
        el.set(qn("w:id"), "5")
        if extra == 0:
            p2._p.insert(0, el)
        else:
            p2._p.append(el)
    refr = p2._p.makeelement(qn("w:r"), {})
    ref = refr.makeelement(qn("w:commentReference"), {})
    ref.set(qn("w:id"), "5")
    refr.append(ref)
    p2._p.append(refr)
    doc.save(path)

    # 注入 comments part（模拟已有审校意见的文档）
    with zipfile.ZipFile(path) as zin:
        entries = [(n, zin.read(n)) for n in zin.namelist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zout:
        for n, data in entries:
            if n == "[Content_Types].xml":
                txt = data.decode("utf-8").replace(
                    "</Types>",
                    '<Override PartName="/word/comments.xml" ContentType='
                    '"application/vnd.openxmlformats-officedocument.'
                    'wordprocessingml.comments+xml"/></Types>')
                data = txt.encode("utf-8")
            elif n == "word/_rels/document.xml.rels":
                txt = data.decode("utf-8").replace(
                    "</Relationships>",
                    '<Relationship Id="rId90" Type="http://schemas.openxmlformats'
                    '.org/officeDocument/2006/relationships/comments" '
                    'Target="comments.xml"/></Relationships>')
                data = txt.encode("utf-8")
            zout.writestr(n, data)
        zout.writestr(
            "word/comments.xml",
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<w:comments xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main">'
            '<w:comment w:id="5" w:author="alice" '
            'w:date="2026-01-01T00:00:00Z" w:initials="al">'
            '<w:p><w:r><w:t>prior comment</w:t></w:r></w:p></w:comment>'
            '</w:comments>')


def xml_of(docx_path, part):
    with zipfile.ZipFile(str(docx_path)) as zf:
        return zf.read(part).decode("utf-8")


def names_of(docx_path):
    with zipfile.ZipFile(str(docx_path)) as zf:
        return zf.namelist()


def accepted_text(p):
    """独立实现“接受全部修订”后的段落文本：剔除 w:del 内 w:t，保留其余。"""
    out = []
    for node in p._p.iter(qn("w:t")):
        parent = node.getparent()
        if parent is not None and parent.getparent() is not None \
                and parent.getparent().tag == qn("w:del"):
            continue
        out.append(node.text or "")
    return "".join(out)


def assert_opc_ok(docx_path, expect_comments=True):
    """S01 包体关系与结构检查：无重复条目、XML 良构、关系目标存在、
    comments part 的 content type 已注册。"""
    names = names_of(docx_path)
    assert len(names) == len(set(names)), "duplicate zip entries"
    with zipfile.ZipFile(str(docx_path)) as zf:
        for n in names:
            if n.endswith((".xml", ".rels")):
                ET.fromstring(zf.read(n))  # 良构即可解析
        ct = zf.read("[Content_Types].xml").decode("utf-8")
        for rels in (n for n in names if n.endswith(".rels")):
            base = posixpath.dirname(posixpath.dirname(rels))
            for m in re.finditer(r'Target="([^"]+)"[^>]*', zf.read(rels).decode("utf-8")):
                tgt = m.group(1)
                if tgt.startswith(("http://", "https://", "external")):
                    continue
                resolved = tgt.lstrip("/") if tgt.startswith("/") else \
                    posixpath.normpath(posixpath.join(base, tgt))
                assert resolved in names, f"{rels} -> {tgt} missing"
    if expect_comments:
        assert 'PartName="/word/comments.xml"' in ct, ct
        assert "wordprocessingml.comments+xml" in ct
        assert 'PartName="/word/commentsExtended.xml"' in ct, ct
        assert "wordprocessingml.commentsExtended+xml" in ct


def comment_threads(docx_path):
    """返回 (document.xml 锚点id列表, comments.xml 意见id列表)。"""
    dx = xml_of(docx_path, "word/document.xml")
    cx = xml_of(docx_path, "word/comments.xml")
    crs = re.findall(r'<w:commentRangeStart\b[^>]*\bw:id="(\d+)"', dx)
    cre = re.findall(r'<w:commentRangeEnd\b[^>]*\bw:id="(\d+)"', dx)
    ref = re.findall(r'<w:commentReference\b[^>]*\bw:id="(\d+)"', dx)
    cid = re.findall(r'<w:comment\b[^>]*\bw:id="(\d+)"', cx)
    return crs, cre, ref, cid


def write_plan(workspace, plan):
    (workspace / "plan.json").write_text(
        json.dumps(plan, ensure_ascii=False), encoding="utf-8")


def run_review(workspace, draft="draft.docx", out="out_REVIEWED.docx",
               report="r.json"):
    args = ["--input", workspace / draft, "--plan", workspace / "plan.json",
            "--output", workspace / out]
    if report is not None:
        args += ["--report", workspace / report]
    return run_script("docx-review-redline", "apply_review.py", args)


# ---------------------------------------------------------------- 正常

def test_replace_with_comment(workspace):
    build_draft(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "replace", "find": "OLD", "replace": "NEW",
         "comment": "术语统一为NEW"},
        {"op": "replace", "find": "跨run目标文本", "replace": "跨run已改文本",
         "comment": "跨run替换"},
    ]})
    r = run_review(workspace)
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert report["applied"] == 2, report
    assert report["skipped"] == []

    out = workspace / "out_REVIEWED.docx"
    assert_opc_ok(out)
    doc_xml = xml_of(out, "word/document.xml")
    # 修订结构: w:del 含 OLD, w:ins 含 NEW
    assert re.search(r"<w:del [^>]*>.*?OLD.*?</w:del>", doc_xml, re.S)
    assert re.search(r"<w:ins [^>]*>.*?NEW.*?</w:ins>", doc_xml, re.S)
    # 批注锚点
    assert "commentRangeStart" in doc_xml and "commentRangeEnd" in doc_xml
    assert "commentReference" in doc_xml
    # comments.xml 存在且含意见文本
    comments_xml = xml_of(out, "word/comments.xml")
    assert "术语统一为NEW" in comments_xml
    assert "跨run已改" in comments_xml or "跨run替换" in comments_xml
    # commentsExtended 存在且 paraId 与 comments.xml 互相匹配
    assert "word/commentsExtended.xml" in names_of(out)
    ext_xml = xml_of(out, "word/commentsExtended.xml")
    c_pids = re.findall(r'w14:paraId="([0-9A-Fa-f]{8})"', comments_xml)
    e_pids = re.findall(r'w15:paraId="([0-9A-Fa-f]{8})"', ext_xml)
    assert c_pids and c_pids == e_pids
    # 批注线程 id 一致：锚点三件套与 comments.xml 一一对应
    crs, cre, ref, cid = comment_threads(out)
    assert crs == cre == ref
    assert sorted(crs) == sorted(cid)
    # 回读核对
    Document(str(out))


def test_insert_and_delete_ops(workspace):
    doc = Document()
    doc.add_paragraph("第一季度收入为一百万元。")
    doc.save(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "insert", "find": "收入为", "insert": "人民币",
         "position": "after", "comment": "补币种"},
        {"op": "delete", "find": "一百万", "comment": "金额待核实，先删"},
        {"op": "insert", "find": "。", "insert": "（初稿）",
         "position": "before", "comment": "标注初稿"},
    ]})
    r = run_review(workspace)
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert report["applied"] == 3, report

    out = workspace / "out_REVIEWED.docx"
    assert_opc_ok(out)
    doc_xml = xml_of(out, "word/document.xml")
    # 插入内容在 w:ins、删除内容在 w:delText
    assert re.search(r"<w:ins [^>]*>.*?人民币.*?</w:ins>", doc_xml, re.S)
    assert re.search(r"<w:ins [^>]*>.*?（初稿）.*?</w:ins>", doc_xml, re.S)
    assert re.search(r"<w:delText[^>]*>一百万</w:delText>", doc_xml)
    # 接受全部修订后的独立金标准
    got = accepted_text(Document(str(out)).paragraphs[0])
    assert got == "第一季度收入为人民币元（初稿）。", got
    comments_xml = xml_of(out, "word/comments.xml")
    assert "金额待核实" in comments_xml


def test_comment_only_and_skipped(workspace):
    build_draft(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "comment", "find": "正常内容", "comment": "请补充说明"},
        {"op": "replace", "find": "不存在的文本XX", "replace": "Y", "comment": "z"},
    ]})
    r = run_review(workspace, out="o2.docx", report="r2.json")
    assert r.returncode == 0, r.stderr
    report = json.loads((workspace / "r2.json").read_text(encoding="utf-8"))
    assert report["applied"] == 1
    assert len(report["skipped"]) == 1
    assert "out of range" in report["skipped"][0]["reason"] or \
        "not found" in report["skipped"][0]["reason"]
    out = workspace / "o2.docx"
    doc_xml = xml_of(out, "word/document.xml")
    assert "commentRangeStart" in doc_xml
    comments = xml_of(out, "word/comments.xml")
    assert "请补充说明" in comments
    # 无修订标记(comment-only不产生ins/del)
    assert "<w:del " not in doc_xml
    assert "<w:ins " not in doc_xml
    assert_opc_ok(out)


# ---------------------------------------------------------------- 复杂

def test_accept_semantics_and_order(workspace):
    """接受全部修订后的文本 = 原文去掉del内容替换为ins内容；验证段落顺序定位正确。"""
    build_draft(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "replace", "find": "OLD", "replace": "NEW", "comment": "c1",
         "scope": "all"},
    ]})
    r = run_review(workspace, out="o.docx", report=None)
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    # OLD 出现3处: AAA OLD ZZZ段 / 表格前段落 / 表格cell(0,1)
    assert report["applied"] == 3, report
    # 表格单元格定位被明确标注（不被段落定位误命中）
    locs = [x["location"] for x in report["results"] if x["status"] == "applied"]
    assert any(loc.startswith("table row 0 col 1") for loc in locs), locs

    doc = Document(str(workspace / "o.docx"))
    paras = doc.paragraphs
    assert accepted_text(paras[1]) == "AAA NEW ZZZ"
    assert "包含 NEW 这个词" in accepted_text(paras[2])
    # 表格cell文本(接受修订后)
    cell = doc.tables[0].cell(0, 1)
    cell_text = accepted_text(cell.paragraphs[0])
    assert "NEW 备用" in cell_text and "OLD" not in cell_text
    # 未匹配段落不受影响
    assert accepted_text(paras[0]) == "第一段介绍背景。"
    assert_opc_ok(workspace / "o.docx")


def test_occurrence_selection(workspace):
    doc = Document()
    doc.add_paragraph("first OLD one")
    doc.add_paragraph("second OLD two")
    doc.add_paragraph("third OLD three")
    doc.save(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "replace", "find": "OLD", "replace": "NEW",
         "comment": "只改第2处", "occurrence": 2},
    ]})
    r = run_review(workspace)
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert report["applied"] == 1, report
    assert report["results"][0]["location"] == "paragraph 1 (body)"

    texts = [accepted_text(p) for p in Document(str(workspace / "out_REVIEWED.docx")).paragraphs]
    assert texts == ["first OLD one", "second NEW two", "third OLD three"], texts


def test_sequential_ops_interact(workspace):
    """后一个操作要能命中前一个操作刚写入的文本。"""
    doc = Document()
    doc.add_paragraph("alpha beta gamma")
    doc.save(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "replace", "find": "beta", "replace": "delta", "comment": "r1"},
        {"op": "comment", "find": "delta", "comment": "r2 targets the new text"},
    ]})
    r = run_review(workspace)
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert report["applied"] == 2, report
    assert report["skipped"] == []
    comments_xml = xml_of(workspace / "out_REVIEWED.docx", "word/comments.xml")
    assert "r2 targets the new text" in comments_xml
    texts = [accepted_text(p) for p in
             Document(str(workspace / "out_REVIEWED.docx")).paragraphs]
    assert texts == ["alpha delta gamma"], texts


def test_existing_comments_and_revision_ids_merged(workspace):
    """输入已有批注/修订：合并而非覆盖，无重复 zip 条目，id 不冲突。"""
    build_annotated(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "replace", "find": "OLD", "replace": "NEW",
         "comment": "新意见", "occurrence": 2},
    ]})
    r = run_review(workspace)
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert report["applied"] == 1, report

    out = workspace / "out_REVIEWED.docx"
    names = names_of(out)
    assert names.count("word/comments.xml") == 1, names
    comments_xml = xml_of(out, "word/comments.xml")
    assert "prior comment" in comments_xml  # 既有意见保留
    assert "新意见" in comments_xml          # 新意见合入
    assert 'w:author="alice"' in comments_xml

    # id 唯一性：修订 id 互不重复；批注线程锚点一致且与 comments.xml 对应
    doc_xml = xml_of(out, "word/document.xml")
    rev_ids = re.findall(r'<w:(?:ins|del)\b[^>]*\bw:id="(\d+)"', doc_xml)
    assert len(rev_ids) == len(set(rev_ids)), rev_ids
    crs, cre, ref, cid = comment_threads(out)
    # 重叠批注的起止顺序可以不同，但每个线程的锚点三件套必须配对齐全
    assert sorted(crs) == sorted(cre) == sorted(ref)
    assert sorted(crs) == sorted(cid)
    assert set(cid) >= {"5"}          # 既有批注 id=5 保留
    assert "101" in rev_ids           # 既有修订 id 保留
    assert "5" not in set(rev_ids)    # 批注 id 与修订 id 不混用

    # 接受语义金标准：第2处（plain段落）被替换，第1处（既有ins内）不动
    paras = Document(str(out)).paragraphs
    assert accepted_text(paras[1]) == "plain NEW text"
    assert "OLD" in accepted_text(paras[0])
    assert_opc_ok(out)


def test_header_comment(workspace):
    doc = Document()
    doc.add_paragraph("正文段落。")
    doc.sections[0].header.paragraphs[0].text = "机密：内部资料勿外传"
    doc.save(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "comment", "find": "机密", "comment": "密级需确认"},
    ]})
    r = run_review(workspace)
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert report["applied"] == 1, report
    assert report["results"][0]["location"] == "section 0 header"
    out = workspace / "out_REVIEWED.docx"
    header_parts = [n for n in names_of(out) if re.match(r"word/header\d+\.xml", n)]
    assert header_parts
    found = any("commentRangeStart" in xml_of(out, n) for n in header_parts)
    assert found, header_parts
    assert "密级需确认" in xml_of(out, "word/comments.xml")
    assert_opc_ok(out)


# ---------------------------------------------------------------- 异常

def test_abnormal_corrupt_and_bad_plan(workspace):
    (workspace / "bad.docx").write_bytes(b"garbage")
    (workspace / "p.json").write_text('{"operations":[]}', encoding="utf-8")
    r = run_script("docx-review-redline", "apply_review.py",
                   ["--input", workspace / "bad.docx", "--plan", workspace / "p.json",
                    "--output", workspace / "o.docx"])
    assert r.returncode == 2

    # 非法 JSON 计划
    (workspace / "bad.json").write_text("{not json", encoding="utf-8")
    build_draft(str(workspace / "draft.docx"))
    r = run_script("docx-review-redline", "apply_review.py",
                   ["--input", workspace / "draft.docx", "--plan", workspace / "bad.json",
                    "--output", workspace / "o.docx"])
    assert r.returncode == 2

    # 计划根不是对象
    (workspace / "list.json").write_text("[1,2]", encoding="utf-8")
    r = run_script("docx-review-redline", "apply_review.py",
                   ["--input", workspace / "draft.docx", "--plan", workspace / "list.json",
                    "--output", workspace / "o.docx"])
    assert r.returncode == 2

    # 输入不存在
    r = run_script("docx-review-redline", "apply_review.py",
                   ["--input", workspace / "nope.docx", "--plan", workspace / "p.json",
                    "--output", workspace / "o.docx"])
    assert r.returncode == 2


def test_malformed_ops_reported_not_crash(workspace):
    build_draft(str(workspace / "draft.docx"))
    write_plan(workspace, {"operations": [
        {"op": "highlight", "find": "OLD"},          # 未知操作类型
        {"find": "OLD"},                             # 缺 op
        42,                                          # 非对象
        {"op": "comment", "find": "正常内容", "comment": "ok", "occurrence": 99},
        {"op": "replace", "find": "", "replace": "x"},  # 空 find
    ]})
    r = run_review(workspace, out="o3.docx", report="r3.json")
    assert r.returncode == 0, r.stderr + r.stdout
    report = json.loads((workspace / "r3.json").read_text(encoding="utf-8"))
    assert report["applied"] == 0
    reasons = [s["reason"] for s in report["skipped"]]
    assert "unknown op type" in reasons
    assert "operation is not an object" in reasons
    assert "empty find" in reasons
    assert any("out of range" in x for x in reasons)
    assert_opc_ok(workspace / "o3.docx", expect_comments=False)


def test_empty_plan(workspace):
    build_draft(str(workspace / "draft.docx"))
    (workspace / "p.json").write_text('{"operations":[]}', encoding="utf-8")
    r = run_script("docx-review-redline", "apply_review.py",
                   ["--input", workspace / "draft.docx", "--plan", workspace / "p.json",
                    "--output", workspace / "o.docx"])
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    assert report["operations_total"] == 0 and report["applied"] == 0
    # 原样字节复制
    h1 = hashlib.sha256((workspace / "draft.docx").read_bytes()).hexdigest()
    h2 = hashlib.sha256((workspace / "o.docx").read_bytes()).hexdigest()
    assert h1 == h2
