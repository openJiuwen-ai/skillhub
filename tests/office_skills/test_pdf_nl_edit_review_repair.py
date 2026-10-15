"""pdf-nl-edit 主控复核修复回归（独立于迁移测试，追加覆盖真实缺陷）。

覆盖主控复核发现的真实缺陷与新增审查要求（黑盒子进程 + PyMuPDF oracle；
PyMuPDF 仅用于生成 fixture 与独立读取，不在被测 runtime 中出现）：

1. 字面量字符串：`_scan_literal` 曾把外层 '(' 也当成内容，普通 literal Tj
   文档局部替换后非目标正文多出左括号 —— 现须精确解码、正文不损坏。
2. 同一文本算子内多处命中：曾按算子计数导致 hits 与定位器不一致而退出 2 ——
   现须计数与替换每一处（与 `occurrences_found` 一致）。
3. 字距补偿须用该字体真实字宽：对 Courier / Times / Helvetica-Bold 等标准
   14 号字体，保留文本位置不得因误用 Helvetica 字宽而漂移。
4. 同一 TJ 数组多个字符串元素与数字字距项须保留；目标跨元素不得假成功。
5. 未知定界符 / 内联图像必须推进或显式报错，绝不无限循环。
6. 输出校验核查未匹配正文（`target_page_body_preserved`），坏结果不得退出 0。
"""
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import pytest  # noqa: E402
import pymupdf  # noqa: E402  (test-time fixture/oracle only)


def _find_ttf():
    """找一个真实 TTF 作为嵌入字体 fixture（无则跳过）。"""
    cands = []
    for root in (r"C:\Windows\Fonts", "/usr/share/fonts", "/Library/Fonts",
                 "/System/Library/Fonts"):
        cands += glob.glob(os.path.join(root, "**", "*.ttf"), recursive=True)
    prefer = [c for c in cands
              if any(k in os.path.basename(c).lower()
                     for k in ("arial", "times", "cour", "dejavu", "liberation"))]
    return (prefer or cands or [None])[0]


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _texts(path):
    doc = pymupdf.open(str(path))
    texts = [p.get_text("text") for p in doc]
    doc.close()
    return texts


def _position_of(path, needle, page=0):
    """返回 needle 首个字符在该页的 x0（按读取顺序的字符框）。"""
    doc = pymupdf.open(str(path))
    chars = []
    for blk in doc[page].get_text("rawdict")["blocks"]:
        for line in blk.get("lines", []):
            for span in line.get("spans", []):
                chars.extend(span.get("chars", []))
    doc.close()
    text = "".join(c["c"] for c in chars)
    i = text.find(needle)
    assert i >= 0, f"{needle!r} not found in {text!r}"
    return chars[i]["bbox"][0]


def _build_with_content(workspace, name, content):
    """构造仅含指定内容流的单页 PDF（先建 seed 以带出 /helv 字体资源）。"""
    src = workspace / (name + ".pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 770), "seed", fontsize=12)
    xref = page.get_contents()[0]
    doc.update_stream(xref, content)
    doc.save(str(src))
    doc.close()
    return src


# ---------------------------------------------------------------------------
# 缺陷 1：字面量字符串外层括号
# ---------------------------------------------------------------------------

def test_literal_string_does_not_leak_opening_paren(workspace):
    src = workspace / "literal.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 770), "PREFIXOLD_LABELSUFFIX", fontsize=12)
    xref = page.get_contents()[0]
    stream = doc.xref_stream(xref)
    stream = stream.replace(
        b"<" + b"PREFIXOLD_LABELSUFFIX".hex().encode() + b">",
        b"(PREFIXOLD_LABELSUFFIX)")
    doc.update_stream(xref, stream)
    doc.save(str(src))
    doc.close()

    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    text = _texts(out)[0]
    assert "OLD_LABEL" not in text
    assert "PREFIX" in text and "SUFFIX" in text and "NEW" in text
    # 关键：非目标正文不得多出左定界括号（缺陷 1）
    assert "(" not in text, repr(text)
    rep = _read_json(workspace / "r.json")
    assert rep["target_page_body_preserved"] is True
    assert rep["target_page_contains_old_text"] is False


def test_literal_string_with_escaped_paren(workspace):
    src = _build_with_content(workspace, "escaped", (
        b"q\nBT\n1 0 0 1 72 770 Tm\n/helv 12 Tf\n"
        b"(PRE\\(X) Tj\nET\nQ\n"))
    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "PRE(X",
                    "--replace", "PREZ", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    assert _texts(out)[0].strip() == "PREZ"


# ---------------------------------------------------------------------------
# 缺陷 2：同一算子内多处命中全部替换
# ---------------------------------------------------------------------------

def test_multiple_hits_in_one_operator_all_replaced(workspace):
    src = _build_with_content(workspace, "repeat", (
        b"q\nBT\n1 0 0 1 72 770 Tm\n/helv 12 Tf\n"
        b"(OLD_LABEL OLD_LABEL) Tj\nET\nQ\n"))
    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    text = _texts(out)[0]
    assert text.count("NEW") == 2, repr(text)
    assert "OLD_LABEL" not in text
    rep = _read_json(workspace / "r.json")
    assert rep["occurrences_found"] == 2
    assert rep["occurrences_replaced"] == 2
    assert len(rep["edits"]) == 2


def test_body_preserved_not_confused_by_preexisting_replacement_text(workspace):
    # 页面**原本就有**独立的 NEW：把 OLD_LABEL 改成 NEW 必须成功，
    # 且不得把原有 NEW 当作“正文丢失”而误报（退出 1）。
    src = _build_with_content(workspace, "preexisting", (
        b"q\nBT\n1 0 0 1 72 770 Tm\n/helv 12 Tf\n[(OLD_LABEL)] TJ\nET\nQ\n"
        b"q\nBT\n1 0 0 1 72 740 Tm\n/helv 12 Tf\n[(NEW)] TJ\nET\nQ\n"))
    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    text = _texts(out)[0]
    assert text.count("NEW") == 2, repr(text)
    assert "OLD_LABEL" not in text
    rep = _read_json(workspace / "r.json")
    assert rep["target_page_body_preserved"] is True
    assert rep["occurrences_replaced"] == 1


# ---------------------------------------------------------------------------
# 不同字体：字距补偿须用真实字宽，保留文本位置不漂移
# ---------------------------------------------------------------------------

def test_kerning_uses_real_font_widths(workspace):
    cases = [("cour", "courier"), ("tiro", "times"), ("hebo", "helv-bold")]
    for fontname, label in cases:
        src = workspace / (label + ".pdf")
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 770), "PREFIXOLD_LABELSUFFIX",
                                   fontsize=12, fontname=fontname)
        doc.save(str(src))
        doc.close()
        before_x = _position_of(src, "SUFFIX")

        out = workspace / (label + "-out.pdf")
        r = run_script("pdf-nl-edit", "pdf_edit.py",
                       ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                        "--replace", "NEW", "--output", out,
                        "--report", workspace / (label + ".json")])
        assert r.returncode == 0, (label, r.stderr + r.stdout)
        assert "OLD_LABEL" not in _texts(out)[0]
        after_x = _position_of(out, "SUFFIX")
        # 若误用 Helvetica 宽度，Courier 会漂移 ~3.9pt、Times ~0.6pt
        assert abs(after_x - before_x) < 0.5, \
            f"{label}: SUFFIX shifted {after_x - before_x:.3f}pt"


def test_embedded_truetype_cid_uses_real_widths(workspace):
    # 嵌入 TrueType 生成 CID 字体（带真实 /W 与 ToUnicode）：保留文本按原字形
    # 码重排、字距按真实字宽补偿，位置不漂移（不因多对一 ToUnicode 失配而假失败）。
    ttf = _find_ttf()
    if not ttf:
        pytest.skip("no system TrueType font available for embedded-CID fixture")
    src = workspace / "embed.pdf"
    try:
        doc = pymupdf.open()
        doc.new_page().insert_text((72, 770), "PREFIXOLD_LABELSUFFIX",
                                   fontsize=12, fontname="Emb", fontfile=ttf)
        doc.save(str(src))
        doc.close()
    except Exception as e:  # 某些字体不适合内嵌
        pytest.skip(f"cannot embed {ttf}: {e}")
    before_x = _position_of(src, "SUFFIX")
    out = workspace / "embed-out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "embed.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    text = _texts(out)[0]
    assert "OLD_LABEL" not in text
    assert "PREFIX" in text and "SUFFIX" in text and "NEW" in text
    assert abs(_position_of(out, "SUFFIX") - before_x) < 0.5


# ---------------------------------------------------------------------------
# TJ 数组：保留多字符串元素与数字字距项；跨元素不假成功
# ---------------------------------------------------------------------------

def test_tj_array_preserves_strings_and_numeric_kern(workspace):
    src = _build_with_content(workspace, "array", (
        b"q\nBT\n1 0 0 1 72 770 Tm\n/helv 12 Tf\n"
        b"[(ALPHA) -50 (OLD_LABEL) 60 (OMEGA)] TJ\nET\nQ\n"))
    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    text = _texts(out)[0]
    assert "ALPHA" in text and "OMEGA" in text and "NEW" in text
    assert "OLD_LABEL" not in text

    # 数字字距项必须保留在内容流中
    import pypdf
    data = pypdf.PdfReader(str(out)).pages[0].get_contents().get_data()
    assert b"-50" in data and b"60" in data, data


def test_tj_cross_element_target_fails_without_fake_success(workspace):
    src = _build_with_content(workspace, "cross", (
        b"q\nBT\n1 0 0 1 72 770 Tm\n/helv 12 Tf\n"
        b"[(OLD_)(LABEL)] TJ\nET\nQ\n"))
    out = workspace / "out.pdf"
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode != 0, r.stdout
    assert not out.exists(), "跨元素目标不得产出假成功文件"
    assert not any(n.startswith(".pdfedit-") for n in os.listdir(workspace))


# ---------------------------------------------------------------------------
# 未知定界符 / 内联图像：必须推进，绝不无限循环
# ---------------------------------------------------------------------------

def test_inline_image_and_stray_delimiters_do_not_hang(workspace):
    src = _build_with_content(workspace, "inline", (
        b"q\n>\n)\nBT\n1 0 0 1 72 770 Tm\n/helv 12 Tf\n"
        b"[(OLD_LABEL)] TJ\nET\nQ\n"
        b"q\nBI /W 2 /H 2 /BPC 8 /CS /G ID "
        b"\x00\xff\x01 >) %( \x02 EI\nQ\n"))
    out = workspace / "out.pdf"
    # run_script 有超时（120s）：若 tokenize 不推进而挂起，这里会 TimeoutExpired 失败
    r = run_script("pdf-nl-edit", "pdf_edit.py",
                   ["--input", src, "--page", "1", "--find", "OLD_LABEL",
                    "--replace", "NEW", "--output", out,
                    "--report", workspace / "r.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    text = _texts(out)[0]
    assert "NEW" in text and "OLD_LABEL" not in text
