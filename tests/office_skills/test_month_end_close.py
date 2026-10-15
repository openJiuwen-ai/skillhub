"""month-end-close 黑盒测试：勾稽金标准手工推导。"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from helpers import run_script  # noqa: E402

from openpyxl import Workbook, load_workbook  # noqa: E402


def build_tb(path):
    """试算表（一处贷方少记 8000）。

    金标准（手工推导）:
      期初: 借10000+50000=60000 / 贷30000+30000=60000 → 平
      本期: 借5000+20000+8000=33000 / 贷3000+10000+12000=25000 → 差8000
      期末: 借12000+60000=72000 / 贷34000+30000=64000 → 差8000
      EQ: 1xxx期末借72000 vs 2xxx+3xxx期末贷34000+30000=64000 → 差8000
      合计行与明细复算一致（无重复计入）
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "库存现金", 10000, None, 5000, 3000, 12000, None])
    ws.append(["1002", "银行存款", 50000, None, 20000, 10000, 60000, None])
    ws.append(["2202", "应付账款", None, 30000, 8000, 12000, None, 34000])
    ws.append(["4001", "实收资本", None, 30000, None, None, None, 30000])
    ws.append(["合计", "", 60000, 60000, 33000, 25000, 72000, 64000])
    wb.save(path)


def test_close_check_golden(workspace):
    build_tb(str(workspace / "tb.xlsx"))
    r = run_script("month-end-close", "close_check.py",
                   ["--trial", workspace / "tb.xlsx",
                    "--output", workspace / "close-check.xlsx",
                    "--report", workspace / "rep.json"])
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "rep.json").read_text(encoding="utf-8"))
    checks = {c["id"]: c for c in rep["checks"]}
    assert rep["n_accounts"] == 4
    assert checks["TB-OPEN"]["pass"] is True
    assert checks["TB-OPEN"]["left"] == 60000 and checks["TB-OPEN"]["right"] == 60000
    assert checks["TB-PERIOD"]["pass"] is False
    assert checks["TB-PERIOD"]["left"] == 33000
    assert checks["TB-PERIOD"]["right"] == 25000
    assert checks["TB-PERIOD"]["difference"] == 8000
    assert checks["TB-CLOSE"]["pass"] is False
    assert checks["TB-CLOSE"]["left"] == 72000 and checks["TB-CLOSE"]["right"] == 64000
    assert checks["TB-CLOSE"]["difference"] == 8000
    assert checks["EQ-1"]["pass"] is False
    assert checks["EQ-1"]["left"] == 72000
    assert checks["EQ-1"]["right"] == 64000  # 负债34000 + 权益(4xxx)30000
    assert checks["EQ-1"]["difference"] == 8000
    assert rep["total_row_check"]["exists"] is True
    assert rep["total_row_check"]["mismatches"] == []
    assert rep["verdict"] == "differences_found"
    assert "不执行过账" in rep["note"]
    wb = load_workbook(str(workspace / "close-check.xlsx"))
    ws = wb["勾稽检查"]
    rows = list(ws.iter_rows(values_only=True))
    assert len(rows) == 5  # 表头+4项


def test_balanced_book(workspace):
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "库存现金", 1000, None, 500, 300, 1200, None])
    ws.append(["2001", "短期借款", None, 800, 100, 0, None, 700])
    ws.append(["4001", "未分配利润", None, 200, 0, 300, None, 500])
    wb.save(str(workspace / "tb2.xlsx"))
    r = run_script("month-end-close", "close_check.py",
                   ["--trial", workspace / "tb2.xlsx",
                    "--output", workspace / "c2.xlsx", "--report", workspace / "r2.json"])
    assert r.returncode == 0
    rep = json.loads((workspace / "r2.json").read_text(encoding="utf-8"))
    assert rep["verdict"] == "balanced"
    assert all(c["pass"] for c in rep["checks"])


def test_pending_and_abnormal(workspace):
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "库存现金", "N/A", None, 500, 300, 200, None])
    ws.append(["2001", "应付款", None, 200, 500, 300, None, 0])
    wb.save(str(workspace / "tb3.xlsx"))
    r = run_script("month-end-close", "close_check.py",
                   ["--trial", workspace / "tb3.xlsx",
                    "--output", workspace / "c3.xlsx", "--report", workspace / "r3.json"])
    assert r.returncode == 0
    rep = json.loads((workspace / "r3.json").read_text(encoding="utf-8"))
    assert len(rep["pending_items"]) == 1
    assert "无法解析" in rep["pending_items"][0]["reason"]
    # 损坏文件
    (workspace / "bad.xlsx").write_bytes(b"junk")
    r2 = run_script("month-end-close", "close_check.py",
                    ["--trial", workspace / "bad.xlsx",
                     "--output", workspace / "cx.xlsx", "--report", workspace / "rx.json"])
    assert r2.returncode == 2


def _run_close(workspace, tb_name, out_name, rep_name, checklist=None):
    args = ["--trial", workspace / tb_name,
            "--output", workspace / out_name, "--report", workspace / rep_name]
    if checklist:
        args = ["--trial", workspace / tb_name, "--checklist", workspace / checklist,
                "--output", workspace / out_name, "--report", workspace / rep_name]
    return run_script("month-end-close", "close_check.py", args)


def test_missing_period_columns_cannot_pass(workspace):
    """冻结缺陷1(benchmarks/close-complex): 仅有期末列时，期初/本期勾稽不得以0==0通过。

    手工推导: 期末借 100+0=100, 期末贷 0+100=100 → TB-CLOSE 平；
    期初/本期列不存在 → TB-OPEN/TB-PERIOD 必须以"缺少列"失败，verdict 不得 balanced。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期末余额借", "期末余额贷"])
    ws.append(["1001", "虚构现金", 100, 0])
    ws.append(["2001", "虚构负债", 0, 100])
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["TB-OPEN"]["pass"] is False
    assert "缺少列" in checks["TB-OPEN"]["error"]
    assert checks["TB-PERIOD"]["pass"] is False
    assert "缺少列" in checks["TB-PERIOD"]["error"]
    assert checks["TB-CLOSE"]["left"] == 100 and checks["TB-CLOSE"]["right"] == 100
    assert checks["TB-CLOSE"]["pass"] is True
    assert rep["verdict"] == "differences_found"
    assert any("TB-OPEN" in x and "TB-PERIOD" in x for x in rep["verdict_reasons"])


def test_wrong_total_row_blocks_balanced(workspace):
    """冻结缺陷2: 合计行声明500与明细复算100不符 → verdict 不得 balanced。

    手工推导: 期初/本期全空(0==0平)，期末借 100+0=100 == 期末贷 0+100=100，
    四项勾稽全平；但合计行期末写 500/500，与复算各差 400。
    （探针原样输入=仅期末列，另由 probe 复测与缺列用例覆盖。）
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "虚构现金", None, None, None, None, 100, None])
    ws.append(["2001", "虚构负债", None, None, None, None, None, 100])
    ws.append(["合计", "", None, None, None, None, 500, 500])
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert all(c["pass"] for c in rep["checks"])  # 勾稽本身全平
    assert rep["total_row_check"]["exists"] is True
    ms = rep["total_row_check"]["mismatches"]
    assert len(ms) == 2
    assert all(m["stated"] == 500 and m["recomputed"] == 100 for m in ms)
    assert rep["verdict"] == "differences_found"
    assert any("合计行" in x for x in rep["verdict_reasons"])


def test_invalid_amount_blocks_balanced(workspace):
    """冻结缺陷3: 金额 NOT_A_NUMBER 不得静默按0并放行 balanced。

    手工推导: 期末借列含不可解析值 → 该行进未决项；TB-CLOSE 引用该列，
    数值上 0==0 也不得判通过（unreliable），EQ-1 同理；verdict 不得 balanced。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期末余额借", "期末余额贷"])
    ws.append(["1001", "虚构现金", "NOT_A_NUMBER", 0])
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert len(rep["pending_items"]) == 1
    p = rep["pending_items"][0]
    assert p["row"] == 2 and p["code"] == "1001"
    assert "无法解析" in p["reason"]
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["TB-CLOSE"]["pass"] is False
    assert "期末余额借" in checks["TB-CLOSE"]["unreliable_columns"]
    assert checks["EQ-1"]["pass"] is False
    assert rep["verdict"] == "differences_found"
    assert any("未决项" in x for x in rep["verdict_reasons"])


def test_uncached_formula_and_nonfinite_amounts(workspace):
    """公式无缓存值与非有限数(1e400→inf)单列未决项并阻断 balanced，不当作0。

    手工推导: 1001(期初借100/期末借105) 与 2001(期初贷100/期末贷105) 本身全平；
    追加 1002 行: 期初借为无缓存公式(读回空)、本期借为"1e400"(inf)。
    → 未决项2条{公式无缓存值, 金额非有限数}；TB-OPEN/TB-PERIOD 引用脏列判不可靠；
    TB-CLOSE/EQ-1 只引用干净期末列 → 105==105 通过；verdict=differences_found。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "库存现金", 100, None, 10, 5, 105, None])
    ws.append(["2001", "应付款", None, 100, 5, 10, None, 105])
    ws.append(["1002", "问题科目", None, None, None, None, None, None])
    ws["C4"] = "=C2*2"       # 期初余额借: 公式且无缓存值
    ws["E4"] = "1e400"       # 本期发生额借: 解析为 inf，非有限数
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    reasons = {p["reason"] for p in rep["pending_items"]}
    assert reasons == {"公式无缓存值", "金额非有限数"}
    assert all(p["row"] == 4 for p in rep["pending_items"])
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["TB-OPEN"]["pass"] is False
    assert "期初余额借" in checks["TB-OPEN"]["unreliable_columns"]
    assert checks["TB-PERIOD"]["pass"] is False
    assert "本期发生额借" in checks["TB-PERIOD"]["unreliable_columns"]
    assert checks["TB-CLOSE"]["pass"] is True
    assert checks["TB-CLOSE"]["left"] == 105 and checks["TB-CLOSE"]["right"] == 105
    assert checks["EQ-1"]["pass"] is True
    assert rep["verdict"] == "differences_found"


def test_duplicate_and_parent_child_structure(workspace):
    """科目编码重复与父子科目并存 → structure_review 显式列出并阻断 balanced。

    手工推导: 明细 100(期末借50) + 1001(30) + 1001(20,重复) + 100201(10) +
    100202(5) = 期末借合计115 == 2001期末贷115，勾稽数值全平；
    但 1001 出现2次；100 是 1001/100201/100202 的前缀(父子并存，合并层级重复计入风险)。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["100", "资产汇总", None, None, None, None, 50, None])
    ws.append(["1001", "库存现金", None, None, None, None, 30, None])
    ws.append(["1001", "库存现金(重复)", None, None, None, None, 20, None])
    ws.append(["100201", "人民币", None, None, None, None, 10, None])
    ws.append(["100202", "美元", None, None, None, None, 5, None])
    ws.append(["2001", "应付款", None, None, None, None, None, 115])
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert all(c["pass"] for c in rep["checks"])
    sr = rep["structure_review"]
    assert sr["duplicate_codes"] == [{"code": "1001", "rows": [3, 4]}]
    pairs = {(p["parent"], p["child"]) for p in sr["parent_child"]}
    assert pairs == {("100", "1001"), ("100", "100201"), ("100", "100202")}
    assert rep["verdict"] == "differences_found"
    assert any("重复" in x for x in rep["verdict_reasons"])
    assert any("父子" in x for x in rep["verdict_reasons"])
    # xlsx 结构检查 sheet
    wbx = load_workbook(str(workspace / "c.xlsx"))
    assert wbx.sheetnames == ["勾稽检查", "未决项", "结构检查"]
    rows3 = list(wbx["结构检查"].iter_rows(values_only=True))
    assert len(rows3) == 5  # 表头 + 1重复 + 3父子


def test_missing_code_row_not_silently_dropped(workspace):
    """缺科目编码的行不再被静默丢弃: 计入col合计、显式进未决项、sump类勾稽不可靠。

    手工推导: 1001期末借50 + 无编码行期末借50 = 期末借合计100 == 2001期末贷100
    → TB-CLOSE(数值上)通过；但 EQ-1 前缀分类无法纳入无编码行:
    sump('期末余额借','1')=50 vs sump('期末余额贷','2')=100 → 差-50且不可靠。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "库存现金", None, None, None, None, 50, None])
    ws.append(["", "未编码科目", None, None, None, None, 50, None])
    ws.append(["2001", "应付款", None, None, None, None, None, 100])
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    assert len(rep["pending_items"]) == 1
    p = rep["pending_items"][0]
    assert p["row"] == 3 and p["reason"] == "缺少科目编码" and p["name"] == "未编码科目"
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["TB-CLOSE"]["pass"] is True
    assert checks["TB-CLOSE"]["left"] == 100  # 无编码行金额仍计入col合计
    assert checks["EQ-1"]["pass"] is False
    assert checks["EQ-1"]["difference"] == -50
    assert "期末余额借" in checks["EQ-1"]["unreliable_columns"]
    assert rep["verdict"] == "differences_found"


def test_float_precision_cent_tolerance(workspace):
    """浮点精度: 0.1+0.2 类累加误差不得误报差异；0.01 真实差异必须报出。

    手工推导A: 期初借 0.1+0.2=0.30000000000000004 vs 期初贷 0.3（浮点不相等，
    分位取整后相等）→ balanced；期末/本期同理全平。
    手工推导B: 期初借 100.01 vs 期初贷 100.00 → 差 0.01 必须判差异。
    """
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "A", 0.1, None, None, None, 0.1, None])
    ws.append(["1002", "B", 0.2, None, None, None, 0.2, None])
    ws.append(["2001", "C", None, 0.3, None, None, None, 0.3])
    wb.save(str(workspace / "tb.xlsx"))
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json")
    assert r.returncode == 0, r.stderr + r.stdout
    rep = json.loads((workspace / "r.json").read_text(encoding="utf-8"))
    checks = {c["id"]: c for c in rep["checks"]}
    assert checks["TB-OPEN"]["pass"] is True
    assert checks["TB-OPEN"]["left"] == 0.3 and checks["TB-OPEN"]["right"] == 0.3
    assert checks["EQ-1"]["pass"] is True
    assert rep["verdict"] == "balanced"

    wb2 = Workbook()
    ws2 = wb2.active
    ws2.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
                "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws2.append(["1001", "A", 100.01, None, None, None, 100.01, None])
    ws2.append(["2001", "C", None, 100.00, None, None, None, 100.00])
    wb2.save(str(workspace / "tb2.xlsx"))
    r2 = _run_close(workspace, "tb2.xlsx", "c2.xlsx", "r2.json")
    assert r2.returncode == 0
    rep2 = json.loads((workspace / "r2.json").read_text(encoding="utf-8"))
    checks2 = {c["id"]: c for c in rep2["checks"]}
    assert checks2["TB-OPEN"]["pass"] is False
    assert checks2["TB-OPEN"]["difference"] == 0.01
    assert rep2["verdict"] == "differences_found"


def test_bad_checklist_and_missing_trial(workspace):
    """异常: 清单文件非法/试算表不存在 → exit 2；合法自定义清单可用。"""
    wb = Workbook()
    ws = wb.active
    ws.append(["科目编码", "科目名称", "期初余额借", "期初余额贷",
               "本期发生额借", "本期发生额贷", "期末余额借", "期末余额贷"])
    ws.append(["1001", "库存现金", 10, None, None, None, 10, None])
    ws.append(["2001", "应付款", None, 10, None, None, None, 10])
    wb.save(str(workspace / "tb.xlsx"))
    (workspace / "bad.json").write_text("{not json", encoding="utf-8")
    r = _run_close(workspace, "tb.xlsx", "c.xlsx", "r.json", checklist="bad.json")
    assert r.returncode == 2
    assert not (workspace / "r.json").exists()
    # 试算表不存在
    r2 = _run_close(workspace, "nope.xlsx", "c2.xlsx", "r2.json")
    assert r2.returncode == 2
    # 合法自定义清单: 单条期初平衡
    (workspace / "ok.json").write_text(
        json.dumps({"checks": [{"id": "X1", "name": "期初平",
                                "expr": "col('期初余额借') == col('期初余额贷')"}]},
                   ensure_ascii=False), encoding="utf-8")
    r3 = _run_close(workspace, "tb.xlsx", "c3.xlsx", "r3.json", checklist="ok.json")
    assert r3.returncode == 0, r3.stderr + r3.stdout
    rep3 = json.loads((workspace / "r3.json").read_text(encoding="utf-8"))
    assert len(rep3["checks"]) == 1
    assert rep3["checks"][0]["id"] == "X1" and rep3["checks"][0]["pass"] is True
    assert rep3["verdict"] == "balanced"
