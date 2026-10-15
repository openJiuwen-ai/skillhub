"""pdf-nl-edit 运行时依赖迁移回归（PyMuPDF/fitz -> 宽松许可栈）。

独立验证迁移后（pdfplumber/pdfminer.six 定位 + pypdf 结构读写）：
- 功能契约保持：指定页全部命中逐处替换、其余页文本不变、源文件字节不变
- 真正增量：默认输出以原文件字节为前缀、旧对象版本仍在（旧字形码仍在文件里）
- --purge 真正整文件重写：清除未引用对象（旧字形码消失）
- 新隐患：页面缺目标字体时按需新增资源；命中与其它文本共用算子时精确切除 + 字距补偿；
  跨算子命中显式失败不破坏
- 失败行为：损坏/加密显式非零退出、不产出文件
- 发布 runtime 源码无 pymupdf/fitz import（静态断言）
- 用原 PyMuPDF 环境作独立 oracle 交叉核对实际输出语义，不复用被测代码、不读脚本 success 字段

PyMuPDF 只用于本测试生成 fixture 与独立 oracle；不在被测 runtime 中出现。
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import pymupdf  # noqa: E402  (test-time fixture/oracle only)

SCRIPT_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..",
    "skills", "office", "pdf-nl-edit", "scripts", "pdf_edit.py"))

OLD_HEX = b"4f4c445f4c4142454c"   # "OLD_LABEL" 在内容流中的字形码（十六进制）


def _read(path):
    with open(path, "rb") as f:
        return f.read()


def _texts(path):
    doc = pymupdf.open(str(path))
    texts = [p.get_text("text") for p in doc]
    doc.close()
    return texts


def build_multi(path):
    doc = pymupdf.open()
    p1 = doc.new_page()
    p1.insert_text((60, 60), "OLD_LABEL", fontsize=11)
    p1.insert_text((60, 120), "OLD_LABEL", fontsize=11)
    p1.insert_text((300, 120), "KEEP_THIS", fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((60, 60), "SECOND_PAGE_UNCHANGED", fontsize=11)
    doc.save(str(path))
    doc.close()


def test_runtime_source_has_no_pymupdf_or_fitz_import():
    src = open(SCRIPT_PATH, encoding="utf-8").read()
    assert not re.search(r"(?m)^\s*(?:import|from)\s+(?:pymupdf|fitz)\b", src, re.I), \
        "runtime 源码不得 import pymupdf/fitz"
    assert "fitz." not in src


def test_incremental_is_genuine_and_purge_rewrites(workspace):
    src = workspace / "multi.pdf"
    build_multi(src)
    original = _read(src)

    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW_LABEL", "--output", out, "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    data = _read(out)
    # 真正增量：输出以原文件字节为前缀，旧对象版本仍留在文件中
    assert data[:len(original)] == original, "默认必须为 append-only 真正增量"
    assert OLD_HEX in data, "增量应保留旧内容流对象版本（旧字形码仍在文件里）"

    # oracle 交叉核对
    at = _texts(out)
    assert at[0].count("NEW_LABEL") == 2 and "OLD_LABEL" not in at[0]
    assert "KEEP_THIS" in at[0]
    assert at[1] == "SECOND_PAGE_UNCHANGED\n"

    # --purge：整文件重写，清除旧未引用对象
    pg = workspace / "purged.pdf"
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                     "--replace", "NEW_LABEL", "--output", pg,
                     "--report", workspace / "p.json", "--purge"])
    assert r2.returncode == 0, r2.stderr + r2.stdout
    pdata = _read(pg)
    assert not pdata.startswith(original), "purge 必须整文件重写（非 append）"
    assert OLD_HEX not in pdata, "purge 应清除旧内容流对象版本"
    pt = _texts(pg)
    assert pt[0].count("NEW_LABEL") == 2 and "OLD_LABEL" not in pt[0]
    assert pt[1] == "SECOND_PAGE_UNCHANGED\n"


def test_font_resource_added_when_page_lacks_target_font(workspace):
    # 只有西文 Helvetica 的页面，替换为 CJK：应新增 CJK 字体资源并成功
    src = workspace / "latin.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "OLD_LABEL", fontsize=12)
    doc.save(str(src))
    doc.close()

    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "中文标签", "--output", out, "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    t = _texts(out)[0]
    assert "中文标签" in t and "OLD_LABEL" not in t
    # 输出仍是真正增量
    assert _read(out)[:len(_read(src))] == _read(src)


def test_partial_match_removed_locally_without_losing_neighbours(workspace):
    # 命中与其它文本共用同一文本算子：只切除命中字形，保留前后文本
    src = workspace / "shared.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "PREFIXOLD_LABELSUFFIX", fontsize=12)
    doc.save(str(src))
    doc.close()

    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out, "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    t = _texts(out)[0]
    assert "PREFIX" in t and "SUFFIX" in t and "NEW" in t and "OLD_LABEL" not in t
    # 前后文本未被整段删除
    assert "PREFIXSUFFIX" not in t

    # CJK 共用算子部分命中
    csrc = workspace / "cjk-shared.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "标题：旧内容在此", fontsize=12, fontname="china-s")
    doc.save(str(csrc))
    doc.close()
    cout = workspace / "cjk-out.pdf"
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", csrc, "--page", "1", "--find", "旧内容",
                     "--replace", "新内容", "--output", cout, "--report", workspace / "c.json"])
    assert r2.returncode == 0, r2.stderr + r2.stdout
    ct = _texts(cout)[0]
    assert "标题：" in ct and "新内容" in ct and "旧内容" not in ct


def test_find_split_across_text_operators_fails_explicitly(workspace):
    # 目标文本跨两个文本算子：本版本不猜改，显式失败且不产出文件
    src = workspace / "split.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    width_ab = pymupdf.get_text_length("AB", fontname="helv", fontsize=12)
    p.insert_text((72, 72), "AB", fontsize=12)
    p.insert_text((72 + width_ab, 72), "CD", fontsize=12)
    doc.save(str(src))
    doc.close()

    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "ABCD",
                    "--replace", "XY", "--output", out, "--report", workspace / "r.json"])
    assert r.returncode == 2, r.stdout
    assert not out.exists()
    assert not any(n.startswith(".pdfedit-") for n in os.listdir(workspace))


def test_failures_corrupt_and_encrypted(workspace):
    bad = workspace / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    out = workspace / "o.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", bad, "--page", "1", "--find", "a",
                    "--replace", "b", "--output", out])
    assert r.returncode == 2
    assert not out.exists()

    enc = workspace / "enc.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "secret")
    doc.save(str(enc), encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="user")
    doc.close()
    r2 = run_script("pdf-nl-edit", "pdf_edit.py",
                    ["--input", enc, "--page", "1", "--find", "secret",
                     "--replace", "x", "--output", workspace / "o2.pdf"])
    assert r2.returncode == 2, r2.stdout
    assert "encrypted" in (r2.stderr + r2.stdout).lower()
    assert not (workspace / "o2.pdf").exists()
