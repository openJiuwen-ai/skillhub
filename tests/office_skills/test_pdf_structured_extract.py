"""pdf-structured-extract 单项标准黑盒测试。

独立验证（normal / complex / abnormal 三类）：
- 真实 PDF 输入、真实子进程执行、断言产物文件与字段
- 预期值由本测试内独立算法推导（身份证校验位、Luhn），不复用被测代码
- 不 mock 被测实现
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import pymupdf  # noqa: E402

# 身份证校验位独立实现（与技能脚本不同代码路径）
W = [7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2]
CHECK = "10X98765432"


def make_id(prefix17):
    s = sum(int(prefix17[i]) * W[i] for i in range(17))
    return prefix17 + CHECK[s % 11]


VALID_ID = make_id("11010519880605121")   # 校验位正确
BAD_ID = "110105198806051211"             # 校验位故意错误
PHONE = "13812345678"
BANK = "4111111111111111"                 # 经典 Luhn 有效测试卡号
EMAIL = "lisi@example.com"
F15 = "110105880612345"                   # 15 位纯数字（长度歧义）


def independent_luhn(v):
    digits = [int(c) for c in v]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


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


# ---------- 正常 ----------

def test_normal_text_layer_table(workspace):
    build_normal_pdf(str(workspace / "normal.pdf"))
    r = run_script("pdf-structured-extract", "extract_pdf.py",
                   ["--input", workspace / "normal.pdf", "--outdir", workspace / "out"])
    assert r.returncode == 0, r.stderr + r.stdout
    out = workspace / "out"
    # 承诺的三件产物齐全
    assert (out / "extract.json").exists()
    assert (out / "extract.md").exists()
    assert (out / "verify-report.json").exists()

    rep = read_json(out / "extract.json")
    # 顶层结构化表格: 页码 + bbox + 结构化行列
    assert rep["tables"], "应检出至少一张表"
    tbl = rep["tables"][0]
    assert tbl["page"] == 1
    assert tbl["source"] == "find_tables"
    assert isinstance(tbl["bbox"], list) and len(tbl["bbox"]) == 4
    x0, y0, x1, y1 = tbl["bbox"]
    assert 0 <= x0 < x1
    assert 0 <= y0 < y1
    assert tbl["row_count"] == len(tbl["rows"]) >= 3
    assert tbl["col_count"] == 3
    flat = [str(c) for row in tbl["rows"] for c in row]
    assert "项目" in flat and "120.50" in flat
    # 表格不是纯文本流: rows 为二维列表
    assert all(isinstance(row, list) for row in tbl["rows"])
    # 每页 tables 保持行列兼容视图
    page_rows = rep["pages"][0]["tables"][0]
    assert any("项目" in str(c) for row in page_rows for c in row)

    vr = read_json(out / "verify-report.json")
    assert vr["summary"] == {"total": 0, "valid": 0, "invalid": 0, "needs_review": 0}
    # md 含 Markdown 管道表格
    assert "|" in (out / "extract.md").read_text(encoding="utf-8")


# ---------- 复杂 ----------

def test_complex_multipage_verification_and_sparse(workspace):
    build_complex_pdf(str(workspace / "complex.pdf"))
    out = workspace / "out"
    r = run_script("pdf-structured-extract", "extract_pdf.py",
                   ["--input", workspace / "complex.pdf", "--outdir", out])
    assert r.returncode == 0, r.stderr + r.stdout

    rep = read_json(out / "extract.json")
    assert len(rep["pages"]) == 3
    assert rep["sparse_pages"] == [3]                     # 第3页文字<20字符
    rendered = out / rep["pages"][2]["rendered_image"]
    assert rendered.exists() and rendered.stat().st_size > 1000
    assert rep["pages"][2]["needs_vision"] is True

    # 表格 page+bbox 落在页面范围内
    tbl = rep["tables"][0]
    assert tbl["page"] == 1
    pw, ph = 595, 842
    x0, y0, x1, y1 = tbl["bbox"]
    assert 0 <= x0 < x1 <= pw and 0 <= y0 < y1 <= ph

    vr = read_json(out / "verify-report.json")
    by_type = {}
    for e in vr["entries"]:
        by_type.setdefault(e["type"], []).append(e)

    # 第一轮正则候选数 == 条目数
    assert vr["round1"]["candidates"] == len(vr["entries"])

    # 有效: 正确证件 / 电话 / 有效卡号 / 邮箱
    assert any(e["pass2"] == "valid" for e in by_type["id_card"])
    assert by_type["phone"][0]["pass2"] == "valid"
    assert independent_luhn(BANK) is True
    assert by_type["bank_card"][0]["pass2"] == "valid"
    assert by_type["email"][0]["pass2"] == "valid"

    # 无效: 校验位错误的证件 → invalid 且进 mismatches
    bad = [e for e in by_type["id_card"] if e["pass2"] == "invalid"]
    assert len(bad) == 1
    assert bad[0] in vr["mismatches"]

    # 长度歧义: 15 位数字 → needs_review 且单列
    amb = [e for e in vr["entries"] if e["type"] == "ambiguous_15digit"]
    assert len(amb) == 1 and amb[0]["pass2"] == "needs_review"
    assert amb[0] in vr["needs_review"]
    # extract.json 的待核清单恰好是这条歧义项
    assert len(rep["uncertain_items"]) == 1
    assert rep["uncertain_items"][0]["pass2"] == "needs_review"
    assert rep["uncertain_items"][0]["type"] == "ambiguous_15digit"
    assert vr["summary"] == {"total": len(vr["entries"]), "valid": 4,
                             "invalid": 1, "needs_review": 1}

    # 关键数字输出脱敏: verify-report 与 key_numbers 只保留 masked，不含原始值
    secrets = (VALID_ID, BAD_ID, PHONE, BANK, EMAIL, F15)
    vr_raw = json.dumps(vr, ensure_ascii=False)
    kn_raw = json.dumps(rep["key_numbers"], ensure_ascii=False)
    for secret in secrets:
        assert secret not in vr_raw
        assert secret not in kn_raw
    for e in rep["key_numbers"]["candidates_and_verification"]:
        assert "value" not in e
        assert "***" in e["masked"]

    # 确定性: 两次运行 verify-report 逐字节一致
    r2 = run_script("pdf-structured-extract", "extract_pdf.py",
                    ["--input", workspace / "complex.pdf", "--outdir", workspace / "out2"])
    assert r2.returncode == 0
    assert (out / "verify-report.json").read_bytes() == \
        (workspace / "out2" / "verify-report.json").read_bytes()


# ---------- 异常 ----------

def test_abnormal_corrupt_and_encrypted(workspace):
    # 损坏输入: 非零退出且无成功产物
    bad = workspace / "corrupt.pdf"
    bad.write_bytes(b"not a pdf")
    r = run_script("pdf-structured-extract", "extract_pdf.py",
                   ["--input", bad, "--outdir", workspace / "badout"])
    assert r.returncode != 0, r.stdout
    assert not (workspace / "badout" / "extract.json").exists()

    # 加密 PDF: 显式报错退出，不尝试破解
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
