"""chinese-official-doc 测试：MIT复用脚本可运行 + GB/T 9704 排版与机检 + 确定性。"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script, skill_dir  # noqa: E402

from docx import Document  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402

DRAFT = """标题：关于开展2026年度内部审计工作的通知
主送：各部门、各分子公司：

一、审计范围
2026年度内部审计覆盖全部一级部门，重点关注资金使用与采购流程。

二、工作要求
各部门须在收到通知后五个工作日内完成自查，并将自查报告报送审计部。

落款：九文科技（集团）有限公司审计部
日期：2026年10月14日
"""


def test_layout_and_check(workspace):
    (workspace / "draft.txt").write_text(DRAFT, encoding="utf-8")
    r = run_script("chinese-official-doc", "gbt9704_layout.py",
                   ["--draft", workspace / "draft.txt", "--output", workspace / "gongwen.docx",
                    "--report", workspace / "layout.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "layout.json").read_text(encoding="utf-8"))
    assert rep["draft_elements"]["title"] is True
    assert rep["draft_elements"]["recipient"] is True
    assert rep["draft_elements"]["body"] >= 4
    assert rep["elements_complete"] is True, rep["missing"]
    assert rep["missing"] == []

    # 独立断言（不依赖脚本check逻辑）
    doc = Document(str(workspace / "gongwen.docx"))
    sec = doc.sections[0]
    assert abs(sec.top_margin.mm - 37) < 1 and abs(sec.left_margin.mm - 28) < 1
    paras = [p for p in doc.paragraphs if p.text.strip()]
    title = paras[0]
    assert title.text.startswith("关于开展")
    assert title.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.CENTER
    assert title.runs[0].font.size.pt == 22
    recipient = paras[1]
    assert recipient.text.startswith("各部门")
    assert (recipient.paragraph_format.first_line_indent or 0) == 0
    body_first = next(p for p in paras if p.text.startswith("一、"))
    assert body_first.runs[0].font.size.pt == 16
    body_indented = next(p for p in paras if p.text.startswith("2026年度"))
    assert body_indented.paragraph_format.first_line_indent.pt >= 30  # 2字符 x 16pt
    right = [p for p in paras if p.paragraph_format.alignment == WD_ALIGN_PARAGRAPH.RIGHT]
    assert any("九文科技" in p.text for p in right)
    assert any("2026年10月14日" in p.text for p in right)

    # check 模式独立可用
    r2 = run_script("chinese-official-doc", "gbt9704_layout.py",
                    ["--check", workspace / "gongwen.docx", "--report", workspace / "chk.json"])
    assert r2.returncode == 0
    chk2 = json.loads((workspace / "chk.json").read_text(encoding="utf-8"))
    assert chk2["elements_complete"] is True, chk2["missing"]


def test_layout_determinism(workspace):
    """同一输入两次排版，输出 docx 主 XML 一致（确定性）。"""
    import zipfile
    (workspace / "draft.txt").write_text(DRAFT, encoding="utf-8")
    run_script("chinese-official-doc", "gbt9704_layout.py",
               ["--draft", workspace / "draft.txt", "--output", workspace / "a.docx"])
    run_script("chinese-official-doc", "gbt9704_layout.py",
               ["--draft", workspace / "draft.txt", "--output", workspace / "b.docx"])
    with zipfile.ZipFile(str(workspace / "a.docx")) as za, \
            zipfile.ZipFile(str(workspace / "b.docx")) as zb:
        assert za.read("word/document.xml") == zb.read("word/document.xml")


def test_upstream_prose_lint_runs_and_deterministic(workspace):
    """MIT 复用的 prose_lint.py 可运行且两次命中一致；只报告不改写。"""
    text = ("需要指出的是，本方案将全面提升管理水平。值得注意的是，相关情况如下："
            "我们将持续发力，赋能业务，实现质的飞跃。")
    (workspace / "s.txt").write_text(text, encoding="utf-8")
    sdir = skill_dir("chinese-official-doc")
    env = dict(os.environ, PYTHONUTF8="1")
    outs = []
    for i in (1, 2):
        p = subprocess.run([sys.executable, os.path.join(sdir, "scripts", "prose_lint.py"),
                            "--json", str(workspace / "s.txt")],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        assert p.returncode in (0, 1), p.stderr
        data = json.loads(p.stdout)
        findings = data if isinstance(data, list) else data.get("findings", [])
        outs.append(sorted((f["label"], f["line"]) for f in findings))
    assert outs[0] == outs[1]
    labels = {l for l, _ in outs[0]}
    assert len(labels) >= 1  # 命中套话/口语类规则


def test_upstream_scripts_help(workspace):
    """上游4个脚本均可在本环境运行（--help 退出0）。"""
    sdir = skill_dir("chinese-official-doc")
    env = dict(os.environ, PYTHONUTF8="1")
    for s in ("draft_length.py", "edit_scope.py", "material_diff.py", "prose_lint.py"):
        p = subprocess.run([sys.executable, os.path.join(sdir, "scripts", s), "--help"],
                           capture_output=True, text=True, encoding="utf-8", env=env)
        assert p.returncode == 0, (s, p.stderr)


def test_abnormal(workspace):
    (workspace / "bad.txt").write_text("", encoding="utf-8")
    r = run_script("chinese-official-doc", "gbt9704_layout.py",
                   ["--draft", workspace / "bad.txt", "--output", workspace / "x.docx"])
    assert r.returncode == 2
    r2 = run_script("chinese-official-doc", "gbt9704_layout.py",
                    ["--draft", workspace / "noexist.txt", "--output", workspace / "y.docx"])
    assert r2.returncode == 2
