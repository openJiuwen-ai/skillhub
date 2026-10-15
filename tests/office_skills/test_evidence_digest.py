"""evidence-digest 单项标准黑盒测试。

独立验证（normal / complex / abnormal 三类）：
- 真实 md/docx/xlsx/csv/图片输入、真实子进程执行（不 import 被测实现）、断言落盘产物
- 关键回归（benchmarks/evidence-failure 冻结缺陷）：
  1) 损坏 docx 的读取失败错误串绝不能成为证据（E-xxxx），不能计入
     事实台账/覆盖/数字统计，也不能因错误信息含文件名让相关问题"有证据"；
  2) 公式缺缓存、未知文件类型、空材料必须单列或警告，不得静默计入/跳过；
  3) 关键词命中只是检索候选（candidates_need_review），不是已证实答案；
  4) 冲突判定保留期间/主体/单位，不同期间/主体的数值不判为同口径冲突。
- 预期值由本测试内独立回读推导，不 mock 被测实现。
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

import docx  # noqa: E402
from openpyxl import Workbook  # noqa: E402

SKILL = "evidence-digest"
SCRIPT = "evidence_ledger.py"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(*args):
    return run_script(SKILL, SCRIPT, list(args))


def build_normal_materials(mat):
    """md(3行) + docx(2段) + xlsx(2数据行) + csv(1行) + png(1张)。"""
    (mat / "报告.md").write_text(
        "评价结论：2026年Q3销售额为1200万元。\n"
        "客户满意度为92%。\n"
        "访谈提到交付及时。\n", encoding="utf-8")
    d = docx.Document()
    d.add_paragraph("访谈记录：受访人认为服务响应良好。")
    d.add_paragraph("补充数据：Q3销售额为1150万元。")
    d.save(str(mat / "访谈.docx"))
    wb = Workbook()
    ws = wb.active
    ws.title = "指标"
    ws.append(["指标", "数值"])
    ws.append(["客户满意度", "93%"])
    ws.append(["投诉量", "5件"])
    wb.save(str(mat / "指标.xlsx"))
    (mat / "明细.csv").write_text("部门,销售额(万元)\n华东,300\n", encoding="utf-8")
    (mat / "截图.png").write_bytes(b"\x89PNG\r\n\x1a\nfakepng")


# ---------- 正常 ----------

def test_normal_multisource_digest(workspace):
    mat = workspace / "materials"
    mat.mkdir()
    build_normal_materials(mat)
    (workspace / "q.md").write_text(
        "- Q3销售额是多少？\n- 客户满意度如何？\n- 员工流失率是多少？\n",
        encoding="utf-8")
    out = workspace / "out"
    r = run("--materials", mat, "--questions", workspace / "q.md",
            "--outdir", out)
    assert r.returncode == 0, r.stderr + r.stdout
    for name in ("evidence-ledger.json", "evidence-ledger.md",
                 "conflicts.json", "open-questions.json"):
        assert (out / name).is_file(), name
    rep = read_json(out / "evidence-ledger.json")
    # 独立计数: md 3 行 + docx 2 段 + xlsx 6 个非空单元格 + csv 2 行 = 13
    assert rep["stats"]["evidence_items"] == 13
    assert rep["stats"]["material_files"] == 5
    # 独立计数含数字条目: 1200/92/1150/93%/5件/300 = 6
    assert rep["stats"]["with_numbers"] == 6
    # 每条可回溯: id/来源/定位/类型/置信
    for e in rep["ledger"]:
        assert e["id"] and e["source_file"] and e["location"]
        assert e["type"] in ("md", "docx", "xlsx", "csv")
        assert e["confidence"] == "sourced"
    by_src = {}
    for e in rep["ledger"]:
        by_src.setdefault(e["source_file"], []).append(e)
    md1 = by_src["报告.md"][0]
    assert md1["location"] == "行1" and "1200万元" in md1["text"]
    xlsx_hits = [e for e in by_src["指标.xlsx"]
                 if e["location"] == "指标!B2"]
    assert len(xlsx_hits) == 1 and xlsx_hits[0]["text"] == "客户满意度: 93%"
    csv_rows = {e["location"]: e for e in by_src["明细.csv"]}
    assert "华东" in csv_rows["行2"]["text"] and "300" in csv_rows["行2"]["text"]
    assert "部门" in csv_rows["行1"]["text"]
    # 图片 needs_vision，不在台账
    assert len(rep["image_sources_needing_vision"]) == 1
    assert rep["image_sources_needing_vision"][0]["file"] == "截图.png"
    assert all(e["source_file"] != "截图.png" for e in rep["ledger"])
    assert rep["stats"]["unreadable_files"] == 0
    assert rep["stats"]["unsupported_files"] == 0
    assert rep["stats"]["formula_cells_without_cache"] == 0
    # 同口径冲突: 销售额(1200 vs 1150万元)、满意度(92% vs 93%)
    metrics = " ".join(c["metric"] for c in rep["conflicts"])
    assert "销售额" in metrics and "客户满意度" in metrics
    conf = read_json(out / "conflicts.json")
    sale = next(c for c in conf["conflicts"] if "销售额" in c["metric"])
    vals = sorted(h["value"] for h in sale["entries"])
    assert vals == [1150.0, 1200.0]
    assert sale["caliber"]["unit"] == "万元"
    for h in sale["entries"]:
        assert set(h) >= {"value", "id", "file", "location",
                          "period", "subject", "unit"}
    # 问题覆盖: 候选语义
    cov = {c["question"]: c for c in rep["coverage"]}
    assert cov["Q3销售额是多少？"]["status"] == "candidates_need_review"
    assert cov["Q3销售额是多少？"]["evidence_ids"]
    oq = read_json(out / "open-questions.json")["open_questions"]
    assert [q["question"] for q in oq] == ["员工流失率是多少？"]
    assert oq[0]["status"] == "no_evidence"
    # md 台账含来源定位与冲突节
    md_txt = (out / "evidence-ledger.md").read_text(encoding="utf-8")
    assert "报告.md@行1" in md_txt and "冲突指标" in md_txt


def test_normal_conflict_same_caliber_and_determinism(workspace):
    mat = workspace / "materials"
    mat.mkdir()
    (mat / "a.md").write_text(
        "2026年3月客户满意度为90%。\n全年客户满意度为95%。\n", encoding="utf-8")
    (mat / "b.md").write_text("客户满意度为91%。\n", encoding="utf-8")
    (workspace / "q.md").write_text("满意度如何\n", encoding="utf-8")
    out1, out2 = workspace / "out1", workspace / "out2"
    r1 = run("--materials", mat, "--questions", workspace / "q.md",
             "--outdir", out1)
    r2 = run("--materials", mat, "--questions", workspace / "q.md",
             "--outdir", out2)
    assert r1.returncode == 0 and r2.returncode == 0
    rep1 = read_json(out1 / "evidence-ledger.json")
    rep2 = read_json(out2 / "evidence-ledger.json")
    # 确定性: 两次运行台账与冲突一致
    assert rep1["ledger"] == rep2["ledger"]
    assert rep1["conflicts"] == rep2["conflicts"]
    # "90%"(2026年3月) 与 "91%"(未声明期间) 期间兼容 -> 同口径冲突
    sat = [c for c in rep1["conflicts"] if "满意度" in c["metric"]]
    assert len(sat) == 1
    vals = sorted(h["value"] for h in sat[0]["entries"])
    assert vals == [90.0, 91.0]
    # "95%" 声明"全年"，与 2026年3月 显式不同分量？全年无年份分量，
    # 月份/季度分量缺失 -> 与 2026年3月 的月份分量不冲突时单列待核
    # （95% 与 90/91 不同值，但期间分量不同: 3月 vs 未声明）
    cross = read_json(out1 / "conflicts.json")["cross_caliber_review"]
    assert any("满意度" in c["metric"] for c in cross)
    # 冲突条目保留期间与单位
    assert sat[0]["caliber"]["unit"] == "%"
    assert sat[0]["caliber"]["period"] is not None


# ---------- 复杂 ----------

def test_complex_period_and_subject_caliber(workspace):
    """不同期间/不同主体不判同口径冲突，单列待核并保留期间/主体/单位。"""
    mat = workspace / "materials"
    mat.mkdir()
    (mat / "a.md").write_text(
        "2023年营业收入为500万元。\n2024年营业收入为800万元。\n", encoding="utf-8")
    (mat / "b.md").write_text(
        "甲方报价如下：总价为50万元。\n乙方报价如下：总价为80万元。\n",
        encoding="utf-8")
    (mat / "c.md").write_text("工期为360天。\n工期为400天。\n", encoding="utf-8")
    (workspace / "q.md").write_text("营业收入\n", encoding="utf-8")
    out = workspace / "out"
    r = run("--materials", mat, "--questions", workspace / "q.md",
            "--outdir", out)
    assert r.returncode == 0, r.stderr + r.stdout
    rep = read_json(out / "evidence-ledger.json")
    conf = read_json(out / "conflicts.json")
    # 同口径冲突只有 工期（无期间/主体声明，360 vs 400 天）
    assert len(rep["conflicts"]) == 1
    only = rep["conflicts"][0]
    assert only["metric"] == "工期（天）"
    assert sorted(h["value"] for h in only["entries"]) == [360.0, 400.0]
    assert only["caliber"]["unit"] == "天"
    # 不同年份期间: 不判冲突，单列待核且保留期间
    rev = [c for c in conf["cross_caliber_review"] if "营业收入" in c["metric"]]
    assert len(rev) == 1
    periods = {h["period"] for h in rev[0]["entries"]}
    assert periods == {"2023年", "2024年"}
    assert {h["value"] for h in rev[0]["entries"]} == {500.0, 800.0}
    assert all(h["unit"] == "万元" for h in rev[0]["entries"])
    # 不同主体: 不判冲突，单列待核且保留主体
    sub = [c for c in conf["cross_caliber_review"] if "总价" in c["metric"]]
    assert len(sub) == 1
    subjects = {h["subject"] for h in sub[0]["entries"]}
    assert subjects == {"甲方", "乙方"}
    assert {h["value"] for h in sub[0]["entries"]} == {50.0, 80.0}
    # 冲突指标节不含营业收入/总价
    assert not any("营业收入" in c["metric"] or "总价" in c["metric"]
                   for c in rep["conflicts"])


def test_complex_formula_without_cache(workspace):
    """xlsx 公式无缓存值: 单列 formula_cells_without_cache，不编造数值。"""
    mat = workspace / "materials"
    mat.mkdir()
    wb = Workbook()
    ws = wb.active
    ws.title = "计算"
    ws["A1"] = "合计"
    ws["B1"] = 10
    ws["B2"] = "=B1*2"  # openpyxl 保存的公式无缓存值
    wb.save(str(mat / "明细.xlsx"))
    (workspace / "q.md").write_text("合计是多少\n", encoding="utf-8")
    out = workspace / "out"
    r = run("--materials", mat, "--questions", workspace / "q.md",
            "--outdir", out)
    assert r.returncode == 0, r.stderr + r.stdout
    rep = read_json(out / "evidence-ledger.json")
    # 公式单元格单列，带公式与坐标
    assert rep["stats"]["formula_cells_without_cache"] == 1
    fc = rep["formula_cells_without_cache"][0]
    assert (fc["file"], fc["sheet"], fc["cell"], fc["formula"]) == \
        ("明细.xlsx", "计算", "B2", "=B1*2")
    # 台账只有 A1/B1 两个真实值，未编造公式结果 20
    assert rep["stats"]["evidence_items"] == 2
    texts = {e["text"] for e in rep["ledger"]}
    assert texts == {"合计", "合计: 10"}
    assert all("20" != e["text"] for e in rep["ledger"])
    # 警告提示公式缺缓存
    assert any("公式" in w for w in rep["warnings"])
    # 覆盖候选不含公式单元格（其不在台账）
    cov = rep["coverage"][0]
    hit_ids = cov["evidence_ids"]
    locs = [e["location"] for e in rep["ledger"] if e["id"] in hit_ids]
    assert all("B2" not in loc for loc in locs)


def test_complex_unknown_types_single_listed(workspace):
    """未知类型文件单列不读取，不静默跳过，也不参与问题匹配。"""
    mat = workspace / "materials"
    mat.mkdir()
    (mat / "旧版报告.doc").write_bytes(b"old binary word")
    (mat / "数据.zip").write_bytes(b"PK\x03\x04")
    (mat / "说明.pdf").write_bytes(b"%PDF-1.4 fake")
    (mat / "有效.md").write_text("实际内容：工期为30天。\n", encoding="utf-8")
    (workspace / "q.md").write_text("工期\n压缩包数据\n", encoding="utf-8")
    out = workspace / "out"
    r = run("--materials", mat, "--questions", workspace / "q.md",
            "--outdir", out)
    assert r.returncode == 0, r.stderr + r.stdout
    rep = read_json(out / "evidence-ledger.json")
    assert rep["stats"]["unsupported_files"] == 3
    exts = {u["ext"] for u in rep["unsupported_files"]}
    assert exts == {".doc", ".zip", ".pdf"}
    assert all(u["reason"] for u in rep["unsupported_files"])
    # 台账只含 md 内容
    assert rep["stats"]["evidence_items"] == 1
    assert rep["ledger"][0]["source_file"] == "有效.md"
    # 未知文件不参与匹配: 压缩包数据问题无证据
    oq = read_json(out / "open-questions.json")["open_questions"]
    assert [q["question"] for q in oq] == ["压缩包数据"]
    # md 报告含未知类型节
    md_txt = (out / "evidence-ledger.md").read_text(encoding="utf-8")
    assert "未知类型" in md_txt and "数据.zip" in md_txt


# ---------- 异常 ----------

def test_abnormal_corrupt_docx_not_evidence(workspace):
    """冻结缺陷回归: 损坏文件的错误串绝不能成为证据或满足相关问题。"""
    mat = workspace / "materials"
    mat.mkdir()
    (mat / "合同.docx").write_bytes(b"broken document")
    (mat / "坏表.xlsx").write_bytes(b"not an xlsx")
    (mat / "正常.md").write_text("补充说明：验收金额为88万元。\n", encoding="utf-8")
    (workspace / "q.txt").write_text("合同\n验收金额是多少\n", encoding="utf-8")
    out = workspace / "out"
    r = run("--materials", mat, "--questions", workspace / "q.txt",
            "--outdir", out)
    assert r.returncode == 0, r.stderr + r.stdout
    rep = read_json(out / "evidence-ledger.json")
    # 读取失败单列（2 个损坏文件），绝不进入台账/统计
    assert rep["stats"]["unreadable_files"] == 2
    unreadable_names = {u["file"] for u in rep["unreadable_files"]}
    assert unreadable_names == {"合同.docx", "坏表.xlsx"}
    assert all(u["error"] for u in rep["unreadable_files"])
    # 台稿只有正常 md 的 1 条；错误串不是证据
    assert rep["stats"]["evidence_items"] == 1
    assert rep["stats"]["with_numbers"] == 1
    assert rep["ledger"][0]["source_file"] == "正常.md"
    assert all("读取失败" not in e["text"] for e in rep["ledger"])
    assert all("Package not found" not in e["text"] for e in rep["ledger"])
    # 问题"合同"不能因错误信息含文件名而"有证据"
    oq = read_json(out / "open-questions.json")["open_questions"]
    assert [q["question"] for q in oq] == ["合同"]
    assert oq[0]["status"] == "no_evidence"
    # 部分失败不影响可用文件: 验收金额仍有候选
    cov = rep["coverage"][0]
    assert cov["question"] == "验收金额是多少"
    assert cov["status"] == "candidates_need_review"
    # 无冲突（单值）
    assert rep["conflicts"] == []
    # md 台账无第二条证据（无 E-0002）
    md_txt = (out / "evidence-ledger.md").read_text(encoding="utf-8")
    assert "E-0002" not in md_txt
    assert "读取失败（未计入台账）" in md_txt


def test_abnormal_empty_materials(workspace):
    """空材料目录: 明确警告 + 全部问题无证据，不伪造成功汇总。"""
    mat = workspace / "materials"
    mat.mkdir()
    (workspace / "q.md").write_text("销售额\n满意度\n", encoding="utf-8")
    out = workspace / "out"
    r = run("--materials", mat, "--questions", workspace / "q.md",
            "--outdir", out)
    assert r.returncode == 0, r.stderr + r.stdout
    rep = read_json(out / "evidence-ledger.json")
    assert rep["stats"]["material_files"] == 0
    assert rep["stats"]["evidence_items"] == 0
    assert rep["ledger"] == []
    assert rep["conflicts"] == []
    assert any("材料目录为空" in w for w in rep["warnings"])
    oq = read_json(out / "open-questions.json")["open_questions"]
    assert len(oq) == 2
    assert all(q["status"] == "no_evidence" for q in oq)
    assert rep["stats"]["questions_no_evidence"] == 2
    md_txt = (out / "evidence-ledger.md").read_text(encoding="utf-8")
    assert "提示" in md_txt


def test_abnormal_missing_inputs(workspace):
    """材料目录不存在 / 问题文件不可读: 退出码 2。"""
    mat = workspace / "materials"
    mat.mkdir()
    (mat / "a.md").write_text("内容：工期为30天。\n", encoding="utf-8")
    (workspace / "q.md").write_text("工期\n", encoding="utf-8")
    r1 = run("--materials", workspace / "nope", "--questions",
             workspace / "q.md", "--outdir", workspace / "o1")
    assert r1.returncode == 2
    r2 = run("--materials", mat, "--questions", workspace / "nope.md",
             "--outdir", workspace / "o2")
    assert r2.returncode == 2
    r3 = run("--materials", mat, "--questions", workspace / "q.md",
             "--outdir", workspace / "o3")
    assert r3.returncode == 0
