"""pdf-structured-extract 运行时依赖迁移回归（PyMuPDF/fitz -> 宽松许可栈）。

独立验证迁移后（pdfplumber/pdfminer.six + pypdfium2/PDFium + pypdf）：
- 功能契约保持：页码定位、表格 page+bbox+rows、稀疏页真实 PNG + needs_vision、
  关键数字两轮校验与脱敏、不可读/加密显式失败、determinism
- 发布 runtime 源码无 pymupdf/fitz import（静态断言）
- 用原 PyMuPDF 环境作独立 oracle 交叉核对实际输出语义，不复用被测代码、不读脚本 success 字段

PyMuPDF 只用于本测试生成 fixture 与独立 oracle；不在被测 runtime 中出现。
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import pymupdf  # noqa: E402  (test-time fixture/oracle only)

SCRIPT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "skills", "office", "pdf-structured-extract", "scripts", "extract_pdf.py"))

W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK = "10X98765432"


def make_id(prefix17):
    s = sum(int(prefix17[i]) * W[i] for i in range(17))
    return prefix17 + CHECK[s % 11]


VALID_ID = make_id("11010519880605121")
BAD_ID = "110105198806051211"
PHONE = "13812345678"
BANK = "4111111111111111"
EMAIL = "lisi@example.com"
F15 = "110105880612345"


def draw_table(page, x0, y0, x1, y1, rows):
    cols = max(len(r) for r in rows)
    nrows = len(rows)
    for i in range(nrows + 1):
        y = y0 + i * (y1 - y0) / nrows
        page.draw_line(pymupdf.Point(x0, y), pymupdf.Point(x1, y))
    for j in range(cols + 1):
        x = x0 + j * (x1 - x0) / cols
        page.draw_line(pymupdf.Point(x, y0), pymupdf.Point(x, y1))
    for i, row in enumerate(rows):
        for j, val in enumerate(row):
            page.insert_text(
                (x0 + j * (x1 - x0) / cols + 4, y0 + i * (y1 - y0) / nrows + 14),
                val, fontsize=10, fontname="china-s")


def build_normal_pdf(path):
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 60), "办公用品登记", fontsize=14, fontname="china-s")
    draw_table(p, 72, 100, 460, 240,
               [["项目", "数量", "金额"],
                ["A", "10", "120.50"],
                ["B", "20", "340.00"]])
    doc.save(path)
    doc.close()


def build_complex_pdf(path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 60), "三季度台账", fontsize=14, fontname="china-s")
    draw_table(p1, 72, 90, 480, 230,
               [["订单号", "客户", "金额"],
                ["A001", "张三", "1200"],
                ["A002", "李四", "3400"]])
    p1.insert_text((72, 260), f"客户经理身份证 {VALID_ID}", fontsize=10, fontname="china-s")
    p1.insert_text((72, 278), f"存疑证号 {BAD_ID}", fontsize=10, fontname="china-s")
    p1.insert_text((72, 296), f"电话 {PHONE} 邮箱 {EMAIL}", fontsize=10, fontname="china-s")
    p1.insert_text((72, 314), f"卡号 {BANK} 旧编号 {F15}", fontsize=10, fontname="china-s")
    p2 = doc.new_page()
    p2.insert_text((72, 100), "正文页：本季度完成三项重点任务，各项运营指标均达成预期目标。",
                   fontsize=11, fontname="china-s")
    p3 = doc.new_page()
    p3.insert_text((72, 100), "签字：", fontsize=10, fontname="china-s")
    doc.save(path)
    doc.close()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_runtime_source_has_no_pymupdf_or_fitz_import():
    src = open(SCRIPT_PATH, encoding="utf-8").read()
    # 只针对真正的 import 语句，避免注释/文档字符串里的产品名误报
    assert not re.search(r"(?m)^\s*(?:import|from)\s+(?:pymupdf|fitz)\b", src, re.I), \
        "runtime 源码不得 import pymupdf/fitz"
    # 同时不得出现直接属性访问 fitz.
    assert "fitz." not in src


def test_normal_contract_unchanged_with_permissive_stack(workspace):
    build_normal_pdf(str(workspace / "normal.pdf"))
    out = workspace / "out"
    r = run_script("pdf-structured-extract", "extract_pdf.py",
                   ["--input", workspace / "normal.pdf", "--outdir", out])
    assert r.returncode == 0, r.stderr + r.stdout
    assert (out / "extract.json").exists()
    assert (out / "extract.md").exists()
    assert (out / "verify-report.json").exists()

    rep = read_json(out / "extract.json")
    # 三层产物字段完整，engine 明确为宽松栈（不再是 pymupdf）
    assert "pymupdf" not in rep["engine"].lower()
    assert "pdfplumber" in rep["engine"].lower()
    tbl = rep["tables"][0]
    assert tbl["page"] == 1 and tbl["source"] == "find_tables"
    x0, y0, x1, y1 = tbl["bbox"]
    assert 0 <= x0 < x1 and 0 <= y0 < y1
    assert tbl["row_count"] == 3 and tbl["col_count"] == 3
    flat = [str(c) for row in tbl["rows"] for c in row]
    assert "项目" in flat and "120.50" in flat
    assert "|" in (out / "extract.md").read_text(encoding="utf-8")
    assert read_json(out / "verify-report.json")["summary"] == \
        {"total": 0, "valid": 0, "invalid": 0, "needs_review": 0}


def test_oracle_crosscheck_complex(workspace):
    pdf_path = str(workspace / "complex.pdf")
    build_complex_pdf(pdf_path)

    # 独立 oracle：原 PyMuPDF 环境读取真实文件
    oracle = pymupdf.open(pdf_path)
    oracle_pages = [oracle[i].get_text("text") for i in range(oracle.page_count)]
    oracle.close()
    assert len(oracle_pages) == 3
    oracle_sparse = [i + 1 for i, t in enumerate(oracle_pages) if len(t.strip()) < 20]
    assert oracle_sparse == [3]

    out = workspace / "out"
    r = run_script("pdf-structured-extract", "extract_pdf.py",
                   ["--input", workspace / "complex.pdf", "--outdir", out])
    assert r.returncode == 0, r.stderr + r.stdout
    rep = read_json(out / "extract.json")

    # 页数与 oracle 一致；稀疏页与 oracle 判定一致，且确有真实 PNG（不仅是 success 标志）
    assert len(rep["pages"]) == len(oracle_pages)
    assert rep["sparse_pages"] == oracle_sparse
    rendered = out / rep["pages"][2]["rendered_image"]
    assert rendered.exists() and rendered.stat().st_size > 1000
    assert rendered.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert rep["pages"][2]["needs_vision"] is True

    # 表格内容必须真实存在于 oracle 正文（防伪造/防凭空输出）
    page1_text = oracle_pages[0]
    for row in rep["tables"][0]["rows"]:
        for cell in row:
            if cell:
                assert str(cell) in page1_text, f"cell {cell!r} not in oracle page text"

    # 关键数字两轮校验语义
    vr = read_json(out / "verify-report.json")
    by_type = {}
    for e in vr["entries"]:
        by_type.setdefault(e["type"], []).append(e)
    assert vr["round1"]["candidates"] == len(vr["entries"])
    assert any(e["pass2"] == "valid" for e in by_type["id_card"])
    assert any(e["pass2"] == "invalid" for e in by_type["id_card"])
    assert by_type["phone"][0]["pass2"] == "valid"
    assert by_type["bank_card"][0]["pass2"] == "valid"
    assert by_type["email"][0]["pass2"] == "valid"
    amb = by_type["ambiguous_15digit"]
    assert len(amb) == 1 and amb[0]["pass2"] == "needs_review"
    ambr = [e for e in by_type["id_card"] if e["pass2"] == "invalid"]
    assert ambr[0] in vr["mismatches"] and amb[0] in vr["needs_review"]
    assert vr["summary"] == {"total": len(vr["entries"]), "valid": 4,
                             "invalid": 1, "needs_review": 1}

    # 脱敏：原始敏感值不得出现在任何产物
    secrets = (VALID_ID, BAD_ID, PHONE, BANK, EMAIL, F15)
    blob = json.dumps(vr, ensure_ascii=False) + json.dumps(rep["key_numbers"], ensure_ascii=False)
    for s in secrets:
        assert s not in blob
    for e in rep["key_numbers"]["candidates_and_verification"]:
        assert "value" not in e and "***" in e["masked"]

    # 确定性
    r2 = run_script("pdf-structured-extract", "extract_pdf.py",
                    ["--input", workspace / "complex.pdf", "--outdir", workspace / "out2"])
    assert r2.returncode == 0
    assert (out / "verify-report.json").read_bytes() == \
        (workspace / "out2" / "verify-report.json").read_bytes()


def test_failures_corrupt_and_encrypted(workspace):
    bad = workspace / "corrupt.pdf"
    bad.write_bytes(b"not a pdf")
    r = run_script("pdf-structured-extract", "extract_pdf.py",
                   ["--input", bad, "--outdir", workspace / "badout"])
    assert r.returncode != 0
    assert not (workspace / "badout" / "extract.json").exists()

    enc = workspace / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="user")
    doc.close()
    r2 = run_script("pdf-structured-extract", "extract_pdf.py",
                    ["--input", enc, "--outdir", workspace / "encout"])
    assert r2.returncode == 2, r2.stdout
    assert "encrypted" in (r2.stderr + r2.stdout).lower()
    assert not (workspace / "encout" / "extract.json").exists()
