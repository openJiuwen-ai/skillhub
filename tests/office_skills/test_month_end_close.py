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
