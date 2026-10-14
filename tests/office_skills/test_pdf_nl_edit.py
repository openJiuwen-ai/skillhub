"""pdf-nl-edit 单项标准黑盒测试。

独立验证（normal / complex / abnormal 三类）：
- 真实 PDF 输入、真实子进程执行（不 import 被测实现）、断言落盘产物
- 关键回归：同页多引用命中必须“全部替换”，绝不“删多处只插一处”
- 预期值由本测试内独立算法/回读推导，不 mock 被测实现
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import pymupdf  # noqa: E402


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def page_texts(path):
    doc = pymupdf.open(str(path))
    texts = [p.get_text("text") for p in doc]
    doc.close()
    return texts


def no_temp_left(directory):
    return not any(n.startswith(".pdfedit-") for n in os.listdir(directory))


def build_normal_pdf(path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "季度报告封面", fontsize=16, fontname="china-s")
    p1.insert_text((72, 110), "旧标题", fontsize=12, fontname="china-s")
    p1.insert_text((72, 140), "这是正文，不应被改动。", fontsize=10, fontname="china-s")
    p2 = doc.new_page()
    p2.insert_text((72, 72), "第一章 概述", fontsize=14, fontname="china-s")
    p3 = doc.new_page()
    p3.insert_text((72, 72), "附录 A", fontsize=14, fontname="china-s")
    doc.save(str(path))
    doc.close()


def build_multi_pdf(path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((60, 60), "OLD_LABEL", fontsize=11)
    p1.insert_text((60, 120), "OLD_LABEL", fontsize=11)
    p1.insert_text((300, 120), "KEEP_THIS", fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((60, 60), "SECOND_PAGE_UNCHANGED", fontsize=11)
    doc.save(str(path))
    doc.close()


# ---------- 正常 ----------

def test_normal_single_replace(workspace):
    src = workspace / "normal.pdf"
    out = workspace / "out.pdf"
    build_normal_pdf(src)
    src_hash = sha256(src)

    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "旧标题",
                    "--replace", "新标题", "--output", out, "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert out.exists()

    rep = read_json(workspace / "r.json")
    assert rep["occurrences_found"] == 1
    assert rep["occurrences_replaced"] == 1
    assert len(rep["edits"]) == 1
    assert rep["edits"][0]["before"] == "旧标题" and rep["edits"][0]["after"] == "新标题"
    assert rep["other_pages_unchanged"] is True
    assert rep["target_page_contains_new_text"] is True
    assert rep["target_page_contains_old_text"] is False
    assert rep["mode"] == "incremental" and rep["irreversible"] is False

    before = page_texts(src)
    after = page_texts(out)
    assert "新标题" in after[0] and "旧标题" not in after[0]
    assert "这是正文，不应被改动。" in after[0]
    assert after[1] == before[1]
    assert after[2] == before[2]
    # 原文件字节未被修改
    assert sha256(src) == src_hash
    assert no_temp_left(workspace)


# ---------- 复杂 ----------

def test_complex_multi_occurrence_and_cjk(workspace):
    src = workspace / "multi.pdf"
    out = workspace / "out.pdf"
    build_multi_pdf(src)

    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW_LABEL", "--output", out, "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout

    # 核心回归：两处都删、两处都插；绝不删 2 插 1
    texts = page_texts(out)
    assert texts[0].count("NEW_LABEL") == 2, texts[0]
    assert texts[0].count("OLD_LABEL") == 0, texts[0]
    assert "KEEP_THIS" in texts[0]
    assert texts[1] == "SECOND_PAGE_UNCHANGED\n"

    rep = read_json(workspace / "r.json")
    assert rep["occurrences_found"] == 2
    assert rep["occurrences_replaced"] == 2
    assert len(rep["edits"]) == 2
    assert all(e["before"] == "OLD_LABEL" and e["after"] == "NEW_LABEL" for e in rep["edits"])
    assert rep["other_pages_unchanged"] is True

    # CJK 多引用替换
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 72), "占位符甲", fontsize=12, fontname="china-s")
    p.insert_text((72, 100), "占位符甲", fontsize=12, fontname="china-s")
    doc.save(str(workspace / "cjk.pdf"))
    doc.close()
    cj = workspace / "cjk_out.pdf"
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", workspace / "cjk.pdf", "--page", "1", "--find", "占位符甲",
                     "--replace", "已完成", "--output", cj, "--report", workspace / "cjk.json"])
    assert r2.returncode == 0, r2.stderr + r2.stdout
    ct = page_texts(cj)[0]
    assert ct.count("已完成") == 2 and "占位符甲" not in ct, ct
    assert read_json(workspace / "cjk.json")["occurrences_replaced"] == 2

    # --purge：整文件重写，标记为不可逆模式，结果仍正确
    pg = workspace / "purged.pdf"
    r3 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                     "--replace", "NEW_LABEL", "--output", pg,
                     "--report", workspace / "p.json", "--purge"])
    assert r3.returncode == 0, r3.stderr + r3.stdout
    prep = read_json(workspace / "p.json")
    assert prep["mode"] == "purge" and prep["irreversible"] is True
    pt = page_texts(pg)[0]
    assert pt.count("NEW_LABEL") == 2 and "OLD_LABEL" not in pt


# ---------- 异常 ----------

def test_abnormal_not_found_and_page_range(workspace):
    src = workspace / "multi.pdf"
    build_multi_pdf(src)
    # 目标不存在: 退出 1，不产出输出，且保留既有输出文件
    existing = workspace / "existing.pdf"
    existing.write_bytes(b"SENTINEL-PREVIOUS-OUTPUT")
    sentinel = sha256(existing)
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "NO_SUCH_TEXT",
                    "--replace", "X", "--output", existing, "--report", workspace / "nf.json"])
    assert r.returncode == 1, r.stderr + r.stdout
    assert sha256(existing) == sentinel
    assert read_json(workspace / "nf.json")["status"] == "not_found"
    assert no_temp_left(workspace)

    # 页码越界: 退出 2
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", src, "--page", "9", "--find", "OLD_LABEL",
                     "--replace", "X", "--output", workspace / "o.pdf"])
    assert r2.returncode == 2


def test_abnormal_same_path_corrupt_encrypted(workspace):
    src = workspace / "multi.pdf"
    build_multi_pdf(src)
    h = sha256(src)
    # 同输入输出路径: 拒绝并保持源文件不变
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW_LABEL", "--output", src])
    assert r.returncode == 2, r.stdout
    assert sha256(src) == h

    # 损坏输入
    bad = workspace / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", bad, "--page", "1", "--find", "a",
                     "--replace", "b", "--output", workspace / "o2.pdf"])
    assert r2.returncode == 2

    # 加密输入: 显式报错，不尝试破解
    enc = workspace / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="o", user_pw="u")
    doc.close()
    r3 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", enc, "--page", "1", "--find", "secret",
                     "--replace", "x", "--output", workspace / "o3.pdf"])
    assert r3.returncode == 2
    assert "encrypted" in (r3.stderr + r3.stdout).lower()


def test_abnormal_overflow_and_overlap_no_damage(workspace):
    # 超长替换越页: 明确失败，不写坏文件，也不动既有输出
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((450, 60), "OLD", fontsize=11)
    doc.save(str(workspace / "wide.pdf"))
    doc.close()
    existing = workspace / "existing.pdf"
    existing.write_bytes(b"KEEP-ME")
    sentinel = sha256(existing)
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", workspace / "wide.pdf", "--page", "1", "--find", "OLD",
                    "--replace", "X" * 200, "--output", existing, "--report", workspace / "of.json"])
    assert r.returncode == 2, r.stdout
    assert sha256(existing) == sentinel
    assert "overflow" in (r.stderr + r.stdout).lower()
    assert no_temp_left(workspace)

    # 替换文本会覆盖同一行邻近内容: 明确失败，不覆盖
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((60, 60), "OLD", fontsize=11)
    p.insert_text((90, 60), "NEIGHBOR", fontsize=11)
    doc.save(str(workspace / "near.pdf"))
    doc.close()
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", workspace / "near.pdf", "--page", "1", "--find", "OLD",
                     "--replace", "VERYLONGREPLACEMENTTEXT", "--output",
                     workspace / "near_out.pdf"])
    assert r2.returncode == 2, r2.stdout
    assert "overlap" in (r2.stderr + r2.stdout).lower()
    assert not (workspace / "near_out.pdf").exists()
    assert no_temp_left(workspace)
