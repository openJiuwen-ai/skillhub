"""sheet-consolidate-reconcile 黑盒测试：金标准为手工推导的匹配统计。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

from openpyxl import Workbook  # noqa: E402


def build_sources(ws):
    # 华东区: 编码/销售额(万元), P001-P004 + 合计行
    wb = Workbook()
    s1 = wb.active
    s1.title = "明细"
    s1.append(["序号", "编码", "销售额(万元)"])
    rows = [(1, "P001", 100), (2, "P002", 200), (3, "P003", 300), (4, "P004", 400)]
    for r in rows:
        s1.append(list(r))
    s1.append(["", "合计", "1000"])
    wb.save(str(ws / "east.xlsx"))

    # 华南区: 产品编码/销售额, P002-P004, P003 值不同 + P004 重复主键
    wb2 = Workbook()
    s2 = wb2.active
    s2.append(["产品编码", "销售额"])
    for r in [("P002", 200), ("P003", 330), ("P004", 400), ("P004", 410)]:
        s2.append(list(r))
    wb2.save(str(ws / "south.xlsx"))

    # 华北区: 产品编码/销售额, P001+P003+P004+P005
    wb3 = Workbook()
    s3 = wb3.active
    s3.append(["产品编码", "销售额"])
    for r in [("P001", 100), ("P003", 300), ("P004", 400), ("P005", 500)]:
        s3.append(list(r))
    wb3.save(str(ws / "north.xlsx"))


CFG = {
    "primary_key": "产品编码",
    "metrics": ["销售额"],
    "sources": [
        {"file": "east.xlsx", "sheet": "明细", "source_name": "华东区",
         "column_map": {"产品编码": "编码", "销售额": "销售额(万元)"}},
        {"file": "south.xlsx", "source_name": "华南区", "column_map": {}},
        {"file": "north.xlsx", "source_name": "华北区", "column_map": {}},
    ],
}


def test_reconcile(workspace):
    build_sources(workspace)
    (workspace / "cfg.json").write_text(json.dumps(CFG, ensure_ascii=False), encoding="utf-8")
    r = run_script("sheet-consolidate-reconcile", "consolidate.py",
                   ["--config", workspace / "cfg.json", "--output", workspace / "merged.xlsx",
                    "--report", workspace / "rep.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "rep.json").read_text(encoding="utf-8"))
    ms = rep["match_stats"]
    # 手工金标准:
    # 唯一主键 P001..P005 = 5
    assert ms["n_unique_pk"] == 5
    # 三源齐全: P003,P004 = 2
    assert ms["n_present_all"] == 2
    # 匹配率 = 2/5 = 0.4
    assert ms["match_rate"] == 0.4
    # 单源: P005(仅华北) = 1 (P001 在华东+华北, P002 在华东+华南)
    assert ms["n_single_source"] == 1
    # 差异主键: P003(300 vs 330), P004(400 vs 400,410 重复多值) = 2
    assert ms["n_mismatch_pk"] == 2
    # 重复主键: 华南 P004 x2
    assert len(rep["duplicates"]) == 1
    assert rep["duplicates"][0]["pk"] == "P004" and rep["duplicates"][0]["count"] == 2
    # 合计行剔除: 华东 合计
    assert len(rep["skipped_total_rows"]) == 1
    assert rep["skipped_total_rows"][0]["source"] == "华东区"
    # 差异明细含 P003 的各源值
    m_p3 = [m for m in rep["mismatches"] if m["pk"] == "P003"][0]
    assert m_p3["values"]["华东区"] == [300.0]
    assert m_p3["values"]["华南区"] == [330.0]
    # 华北缺失主键 = P002
    assert sorted(rep["source_missing"]["华北区"]) == ["P002"]

    # 合并表独立验证
    from openpyxl import load_workbook
    wb = load_workbook(str(workspace / "merged.xlsx"))
    ws = wb["合并汇总"]
    rows = list(ws.iter_rows(values_only=True))
    header = list(rows[0])
    assert header[0] == "产品编码"
    assert "销售额@华东区" in header and "销售额@华南区" in header and "销售额@华北区" in header
    data = {r[0]: r for r in rows[1:]}
    assert data["P001"][1] == 100 and data["P001"][3] == 100
    assert data["P005"][3] == 500
    # 源明细行数 = 表头1 + 华东4 + 华南4 + 华北4 = 13
    ws2 = wb["源明细"]
    detail_rows = list(ws2.iter_rows(values_only=True))
    assert len(detail_rows) == 1 + 4 + 4 + 4


def test_failed_source_continues(workspace):
    build_sources(workspace)
    cfg = dict(CFG)
    cfg["sources"] = CFG["sources"] + [
        {"file": "broken.xlsx", "source_name": "西域区", "column_map": {}}]
    (workspace / "cfg2.json").write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    r = run_script("sheet-consolidate-reconcile", "consolidate.py",
                   ["--config", workspace / "cfg2.json", "--output", workspace / "m2.xlsx",
                    "--report", workspace / "r2.json"])
    # broken.xlsx 不存在 -> 该源 failed, 其余继续
    assert r.returncode == 0, r.stderr
    rep = json.loads((workspace / "r2.json").read_text(encoding="utf-8"))
    assert "西域区" in rep["failed_sources"]
    assert rep["match_stats"]["n_unique_pk"] == 5


def test_all_sources_fail(workspace):
    (workspace / "cfg3.json").write_text(json.dumps({
        "primary_key": "X", "metrics": ["Y"],
        "sources": [{"file": "nope1.xlsx"}, {"file": "nope2.xlsx"}]},
        ensure_ascii=False), encoding="utf-8")
    r = run_script("sheet-consolidate-reconcile", "consolidate.py",
                   ["--config", workspace / "cfg3.json", "--output", workspace / "m3.xlsx",
                    "--report", workspace / "r3.json"])
    assert r.returncode == 2
