"""docx-version-diff 黑盒测试：正常（改字/单元格）、复杂（分节/页边距/多类并存/结构变化/变更报告docx）、
异常（相同文件/损坏/非docx zip/文件不存在）场景。"""
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

from docx import Document  # noqa: E402
from docx.shared import Inches  # noqa: E402


def build_v1(path):
    doc = Document()
    doc.add_paragraph("第一段：背景说明")
    doc.add_paragraph("第二段：预算为一百万元")
    doc.add_paragraph("第三段：实施安排")
    t = doc.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "项目"
    t.cell(0, 1).text = "金额"
    t.cell(1, 0).text = "咨询费"
    t.cell(1, 1).text = "300000"
    doc.sections[0].header.paragraphs[0].text = "V1 页眉"
    doc.save(path)


def test_modified_one_word(workspace):
    build_v1(str(workspace / "v1.docx"))
    doc = Document(str(workspace / "v1.docx"))
    doc.paragraphs[1].text = "第二段：预算为二百万元"  # 只改一处
    doc.save(str(workspace / "v2.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v2.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 0, r.stderr
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    assert rep["summary"]["paragraph_modified"] == 1
    assert rep["summary"]["paragraph_added"] == 0
    assert rep["summary"]["paragraph_removed"] == 0
    ch = rep["changes"][0]
    assert ch["type"] == "paragraph" and ch["change"] == "modified"
    assert "一百万元" in ch["before"] and "二百万元" in ch["after"]


def test_table_cell_change(workspace):
    build_v1(str(workspace / "v1.docx"))
    doc = Document(str(workspace / "v1.docx"))
    doc.tables[0].cell(1, 1).text = "350000"
    doc.save(str(workspace / "v2.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v2.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 0
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    assert rep["summary"]["table_cell_modified"] == 1
    ch = [c for c in rep["changes"] if c["type"] == "table_cell"][0]
    assert ch["location"] == "table 0 row 1 col 1"
    assert ch["before"] == "300000" and ch["after"] == "350000"


def test_new_section_and_header(workspace):
    build_v1(str(workspace / "v1.docx"))
    doc = Document(str(workspace / "v1.docx"))
    doc.add_section()
    doc.sections[0].header.paragraphs[0].text = "V2 页眉"
    doc.save(str(workspace / "v2.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v2.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 0
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    assert rep["summary"]["section_changed"] >= 1  # 节数变化
    assert rep["summary"]["header_footer_changed"] >= 1  # 页眉变化
    assert any(c["type"] == "section" for c in rep["changes"])
    assert any(c["type"] == "header" and "V2" in (c["after"] or "") for c in rep["changes"])


def test_identical_and_corrupt(workspace):
    build_v1(str(workspace / "v1.docx"))
    import shutil
    shutil.copy(str(workspace / "v1.docx"), str(workspace / "v1copy.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v1copy.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 0
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    assert rep["identical"] is True and rep["changes"] == []
    # 损坏输入
    (workspace / "bad.docx").write_bytes(b"junk")
    r2 = run_script("docx-version-diff", "docx_diff.py",
                    ["--old", workspace / "bad.docx", "--new", workspace / "v1.docx",
                     "--report", workspace / "d2.json"])
    assert r2.returncode == 2


def test_margin_change_detected(workspace):
    """四边页边距变化都要能检出（右/下边距也不能漏），且只计入 section_changed。"""
    doc = Document()
    doc.add_paragraph("正文")
    doc.save(str(workspace / "v1.docx"))
    d2 = Document(str(workspace / "v1.docx"))
    d2.sections[0].right_margin = Inches(2.5)
    d2.sections[0].bottom_margin = Inches(3.0)
    d2.save(str(workspace / "v2.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v2.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 0, r.stderr
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    assert rep["summary"]["section_changed"] == 1
    assert rep["summary"]["header_footer_changed"] == 0  # 页面设置不得计入页眉页脚
    ch = [c for c in rep["changes"] if c["type"] == "section"][0]
    assert ch["change"] == "modified" and ch["location"] == "section 0 page setup"
    assert ch["before"]["right_margin"] != ch["after"]["right_margin"]
    assert ch["before"]["bottom_margin"] != ch["after"]["bottom_margin"]
    assert ch["before"]["left_margin"] == ch["after"]["left_margin"]  # 未动的边距不变


def test_multi_change_and_changes_doc(workspace):
    """复杂场景：段落改/增/删 + 单元格改 + 新增表格 + 页脚改同时发生，
    且 --changes-doc 产出可打开的 docx 变更报告。"""
    d1 = Document()
    for t in ["p1", "p2", "p3", "p4"]:
        d1.add_paragraph(t)
    t = d1.add_table(rows=2, cols=2)
    t.cell(1, 1).text = "300000"
    d1.save(str(workspace / "v1.docx"))
    d2 = Document(str(workspace / "v1.docx"))
    d2.paragraphs[1].text = "p2-修改后"                       # 修改
    p3 = d2.paragraphs[2]._element                            # 删除 p3
    p3.getparent().remove(p3)
    d2.add_paragraph("pX-新增段")                             # 新增
    d2.tables[0].cell(1, 1).text = "350000"                   # 单元格改
    d2.add_table(rows=1, cols=1)                              # 新增表格
    d2.sections[0].footer.paragraphs[0].text = "页脚V2"       # 页脚改
    d2.save(str(workspace / "v2.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v2.docx",
                    "--report", workspace / "d.json",
                    "--changes-doc", workspace / "changes.docx"])
    assert r.returncode == 0, r.stderr
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    s = rep["summary"]
    assert s["paragraph_modified"] == 1
    assert s["paragraph_added"] == 1
    assert s["paragraph_removed"] == 1
    assert s["table_cell_modified"] == 1
    assert s["header_footer_changed"] == 1
    locs = {(c["type"], c["change"], c["location"]) for c in rep["changes"]}
    assert ("paragraph", "modified", "body paragraph 1") in locs
    assert ("paragraph", "removed", "old paragraph 2") in locs
    assert ("paragraph", "added", "body paragraph 3") in locs
    assert ("table_cell", "modified", "table 0 row 1 col 1") in locs
    assert ("table", "added", "table 1") in locs
    assert any(c["type"] == "footer" and "页脚V2" in (c["after"] or "")
               for c in rep["changes"])
    cell = [c for c in rep["changes"] if c["type"] == "table_cell"][0]
    assert cell["before"] == "300000" and cell["after"] == "350000"
    # 变更报告 docx 可独立打开且内容正确
    cdoc = Document(str(workspace / "changes.docx"))
    texts = [p.text for p in cdoc.paragraphs]
    assert any("文档版本变更清单" in t for t in texts)
    assert any("300000" in t for t in texts) and any("350000" in t for t in texts)
    assert any("p2-修改后" in t for t in texts) and any("pX-新增段" in t for t in texts)


def test_table_structural_rowcount_change(workspace):
    """表格行数变化记 structural，不做单元格比对。"""
    d1 = Document()
    t = d1.add_table(rows=2, cols=2)
    t.cell(0, 0).text = "a"
    d1.save(str(workspace / "v1.docx"))
    d2 = Document(str(workspace / "v1.docx"))
    row = d2.tables[0].add_row()
    row.cells[0].text = "new"
    d2.save(str(workspace / "v2.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "v1.docx", "--new", workspace / "v2.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 0, r.stderr
    rep = json.loads((workspace / "d.json").read_text(encoding="utf-8"))
    assert rep["summary"]["table_cell_modified"] == 0  # 结构变化不做单元格级比对
    st = [c for c in rep["changes"] if c["type"] == "table" and c["change"] == "structural"]
    assert len(st) == 1 and st[0]["location"] == "table 0"
    assert st[0]["before"] == "2x2" and st[0]["after"] == "3x2"


def test_non_docx_zip_and_missing_file(workspace):
    """异常：合法 zip 但非 docx → 退出 2；文件不存在 → 退出 2。"""
    fake = workspace / "fake.docx"
    with zipfile.ZipFile(str(fake), "w") as z:
        z.writestr("hello.txt", "not a docx")
    doc = Document()
    doc.add_paragraph("ok")
    doc.save(str(workspace / "good.docx"))
    r = run_script("docx-version-diff", "docx_diff.py",
                   ["--old", workspace / "fake.docx", "--new", workspace / "good.docx",
                    "--report", workspace / "d.json"])
    assert r.returncode == 2
    r2 = run_script("docx-version-diff", "docx_diff.py",
                    ["--old", workspace / "missing.docx", "--new", workspace / "good.docx",
                     "--report", workspace / "d2.json"])
    assert r2.returncode == 2
