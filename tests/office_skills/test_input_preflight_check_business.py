"""input-preflight-check 业务完整性判定黑盒回归（2026-10-15）。

独立新文件，不修改既有 14 项测试。触发背景：真实 Jiuwen 宿主调用中
M2 名称"近三年审计报告"、patterns 仅 ["审计报告", "2023"]、mats 只有 2023 一份
审计报告，关键词层给 ready/pass，宿主据此称"齐全"。本组测试断言：
- 关键词层 verdict/state 作为兼容层仍保留原行为；
- 新增业务完整性层 business_verdict/coverage_state/coverage_warnings 必须
  对有跨年度要求但证据不足者判 needs_confirmation/block，绝不放行；
- 显式 required_years 能区分缺年与齐备并保留来源证据；
- 不猜测"近N年"的基准年份；
- 不以"任意年份出现在任意文件"覆盖；无年度要求的普通清单无回归。

测试为黑盒：子进程执行 CLI，断言落盘报告与 stdout，独立推导预期值。
"""
import json
import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

SKILL = "input-preflight-check"
SCRIPT = "preflight.py"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run(*args):
    return run_script(SKILL, SCRIPT, list(args))


def write_json(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")


def item(rep, iid):
    return [i for i in rep["items"] if i["id"] == iid][0]


@pytest.mark.parametrize("flag", ["false", "true", 1])
def test_merged_years_requires_literal_boolean_true(workspace, flag):
    """Text 'false' and truthy malformed values must not enable a coverage exception."""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2021_2022_2023.md").write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [{"id": "M1", "name": "近三年审计报告", "required": True,
                      "patterns": ["审计报告"], "required_years": [2021, 2022, 2023],
                      "allow_merged_years": flag}]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    assert read_json(workspace / "r.json")["business_verdict"] == "block"


def test_host_case_near_three_years_only_2023_not_released(workspace):
    """真实宿主冻结样例：近三年 + 仅 2023 一份 —— 关键词 pass 但业务层不得放行。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "营业执照副本.md").write_text(
        "统一社会信用代码 91330100MA27X0000X。", encoding="utf-8")
    (mat / "审计报告_2023.md").write_text("审计意见：标准无保留意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "营业执照", "required": True,
         "patterns": ["营业执照", "统一社会信用代码"], "fuzzy": ["执照"]},
        {"id": "M2", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告", "2023"]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    # 关键词层向后兼容：仍为 pass/ready
    assert rep["verdict"] == "pass"
    assert item(rep, "M2")["state"] == "ready"
    # 业务层：不得放行
    assert rep["business_verdict"] == "block"
    m2 = item(rep, "M2")
    assert m2["coverage_state"] == "needs_confirmation"
    assert rep["coverage_warnings"], rep["coverage_warnings"]
    # CLI stdout 同样暴露业务判定（调用方无需只读报告）
    out = json.loads(r.stdout)
    assert out["business_verdict"] == "block"
    # 确定性
    r2 = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
             "--report", workspace / "r2.json")
    assert r2.returncode == 0
    assert read_json(workspace / "r2.json") == rep


def test_explicit_required_years_missing_one_blocks(workspace):
    """显式 required_years 三缺一：coverage_state=incomplete，业务层 block。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2022.md").write_text("审计意见。", encoding="utf-8")
    (mat / "审计报告_2023.md").write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告"], "required_years": [2021, 2022, 2023]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["verdict"] == "pass"  # 关键词层：模式全部命中
    assert rep["business_verdict"] == "block"
    m1 = item(rep, "M1")
    assert m1["coverage_state"] == "incomplete"
    cov = m1["coverage"]
    assert cov["missing_years"] == ["2021"]
    assert set(cov["covered_years"]) == {"2022", "2023"}
    assert "审计报告_2022.md" in cov["evidence"]["2022"]
    assert "审计报告_2023.md" in cov["evidence"]["2023"]
    assert rep["coverage_warnings"]


def test_explicit_required_years_complete_passes(workspace):
    """显式 required_years 三年齐全：coverage_state=complete，业务层 pass。"""
    mat = workspace / "mats"
    mat.mkdir()
    for y in (2021, 2022, 2023):
        (mat / ("审计报告_%d.md" % y)).write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告"], "required_years": [2021, 2022, 2023]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["verdict"] == "pass"
    assert rep["business_verdict"] == "pass"
    assert rep["coverage_warnings"] == []
    m1 = item(rep, "M1")
    assert m1["coverage_state"] == "complete"
    cov = m1["coverage"]
    assert cov["missing_years"] == []
    assert set(cov["covered_years"]) == {"2021", "2022", "2023"}
    assert cov["evidence"]["2021"] == ["审计报告_2021.md"]


def test_plain_checklist_without_year_unchanged(workspace):
    """不涉及年度的普通清单：业务层无约束，旧行为保持。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "营业执照副本.md").write_text("统一社会信用代码 91xxx。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "营业执照", "required": True,
         "patterns": ["营业执照"]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["verdict"] == "pass"
    assert rep["business_verdict"] == "pass"
    assert rep["coverage_warnings"] == []
    assert rep["items"][0]["state"] == "ready"
    assert rep["items"][0]["coverage_state"] is None


def test_unknown_basis_not_guessed(workspace):
    """近三年未声明具体年份：不推断基准年份，业务层需人工核验，不得 pass。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2023.md").write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告"]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    m1 = item(rep, "M1")
    assert m1["coverage_state"] == "needs_confirmation"
    # 不猜基准年份：未声明 required_years 时不得编造年份清单
    assert not m1["coverage"].get("required_years")
    assert rep["business_verdict"] != "pass"
    assert any(("未声明" in w) or ("人工" in w) for w in rep["coverage_warnings"])


def test_year_in_non_evidence_file_does_not_cover(workspace):
    """不以"任意年份在任意文件出现"覆盖：仅同一清单项已命中的可读证据文件计入。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2023.md").write_text("审计意见。", encoding="utf-8")
    # 该文件名含 2021/2022，但文件名与内容都不含模式"审计报告"，不是该项证据
    (mat / "说明_2021_2022.md").write_text("历史年份备查说明，仅用于归档。",
                                       encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告"], "required_years": [2021, 2022, 2023]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["business_verdict"] == "block"
    cov = item(rep, "M1")["coverage"]
    assert set(cov["missing_years"]) == {"2021", "2022"}
    assert "审计报告_2023.md" in cov["evidence"]["2023"]
    assert cov["evidence"].get("2021") in (None, [])
    assert cov["evidence"].get("2022") in (None, [])


def test_partially_invalid_required_years_not_released(workspace):
    """required_years 含无法识别年份：即便有效年份已命中，也不得业务放行。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2023.md").write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "审计报告", "required": True,
         "patterns": ["审计报告"], "required_years": [2023, "bad"]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["business_verdict"] == "block"
    m1 = item(rep, "M1")
    assert m1["coverage_state"] == "needs_confirmation"
    assert any("无法识别" in w for w in rep["coverage_warnings"])


def test_temporal_count_mismatch_not_released(workspace):
    """名称'近三年'但 required_years 仅列 1 个年份 → 数量不一致，不得 complete/pass。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2023.md").write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告"], "required_years": [2023]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["business_verdict"] == "block"
    assert item(rep, "M1")["coverage_state"] == "needs_confirmation"
    assert any("数量不一致" in w for w in rep["coverage_warnings"])


def test_duplicate_required_years_not_released(workspace):
    """required_years 重复年份（[2023,2023,2023]）不得被当作三年齐备。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2023.md").write_text("审计意见。", encoding="utf-8")
    cfg = {"items": [
        {"id": "M1", "name": "近三年审计报告", "required": True,
         "patterns": ["审计报告"], "required_years": [2023, 2023, 2023]},
    ]}
    write_json(workspace / "c.json", cfg)
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["business_verdict"] == "block"
    assert item(rep, "M1")["coverage_state"] == "needs_confirmation"
    assert any("重复" in w for w in rep["coverage_warnings"])


def test_multi_year_filename_not_silent_pass(workspace):
    """单个文件名并列三个年份≠三份证据；默认待确认，显式允许合并才可通过。"""
    mat = workspace / "mats"
    mat.mkdir()
    (mat / "审计报告_2021_2022_2023.md").write_text("审计意见。", encoding="utf-8")
    base_item = {"id": "M1", "name": "近三年审计报告", "required": True,
                 "patterns": ["审计报告"], "required_years": [2021, 2022, 2023]}
    write_json(workspace / "c.json", {"items": [base_item]})
    r = run("checklist", "--checklist", workspace / "c.json", "--dir", mat,
            "--report", workspace / "r.json")
    assert r.returncode == 0
    rep = read_json(workspace / "r.json")
    assert rep["business_verdict"] == "block"
    m1 = item(rep, "M1")
    assert m1["coverage_state"] == "needs_confirmation"
    assert set(m1["coverage"]["merged_years_pending"]) == {"2021", "2022", "2023"}
    assert any("合并" in w or "allow_merged_years" in w
               for w in rep["coverage_warnings"])
    # 显式允许合并报告：配置明确时才可通过（仍需有对应年度证据文件）
    allowed = dict(base_item, allow_merged_years=True)
    write_json(workspace / "c2.json", {"items": [allowed]})
    r2 = run("checklist", "--checklist", workspace / "c2.json", "--dir", mat,
             "--report", workspace / "r2.json")
    assert r2.returncode == 0
    rep2 = read_json(workspace / "r2.json")
    assert rep2["business_verdict"] == "pass"
    assert item(rep2, "M1")["coverage_state"] == "complete"
