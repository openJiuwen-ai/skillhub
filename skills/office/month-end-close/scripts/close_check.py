#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""month-end-close: 试算平衡表勾稽检查。

用法:
  python close_check.py --trial tb.xlsx --checklist checks.json --output close-check.xlsx --report r.json

退出码: 0=完成 2=输入错误
"""
import argparse
import json
import os
import re
import sys

from openpyxl import Workbook, load_workbook

TOTAL_RE = re.compile(r"^(合计|总计)\s*$")


def to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    try:
        return float(s)
    except ValueError:
        return None


def load_trial(path):
    """读取试算表 -> (columns, rows, total_rows, pending)。rows: {code, name, amounts{col: num}}"""
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    table = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    header_idx = None
    header = None
    for i, r in enumerate(table[:15]):
        texts = [str(c).strip() if c is not None else "" for c in r]
        if any("科目" in t for t in texts) and any("借" in t or "贷" in t for t in texts):
            header_idx = i
            header = texts
            break
    if header_idx is None:
        raise ValueError("未找到含科目与借贷的表头行")
    code_col = None
    for i, t in enumerate(header):
        if ("科目" in t and "编码" in t) or t == "科目编码" or t == "科目":
            code_col = i
            break
    if code_col is None:
        code_col = 0
    name_col = None
    for i, t in enumerate(header):
        if "科目" in t and "名称" in t:
            name_col = i
            break
    amount_cols = {}
    for i, t in enumerate(header):
        if not t:
            continue
        has_j = "借" in t
        has_d = "贷" in t
        if not (has_j or has_d):
            continue
        if "期初" in t:
            amount_cols["期初余额借" if has_j else "期初余额贷"] = i
        elif "本期" in t or "发生" in t:
            amount_cols["本期发生额借" if has_j else "本期发生额贷"] = i
        elif "期末" in t:
            amount_cols["期末余额借" if has_j else "期末余额贷"] = i
        else:
            # 无期间词的借贷列: 当作期末
            amount_cols["期末余额借" if has_j else "期末余额贷"] = i
    rows = []
    total_rows = []
    pending = []
    for rn, r in enumerate(table[header_idx + 1:], header_idx + 2):
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        code = str(r[code_col]).strip() if code_col < len(r) and r[code_col] is not None else ""
        name = str(r[name_col]).strip() if name_col is not None and name_col < len(r) and r[name_col] is not None else ""
        if TOTAL_RE.match(code) or TOTAL_RE.match(name):
            total_rows.append({"row": rn, "values": {k: to_num(r[i]) if i < len(r) else None
                                                     for k, i in amount_cols.items()}})
            continue
        if not code:
            pending.append({"row": rn, "reason": "缺少科目编码", "name": name})
            continue
        amounts = {}
        bad = False
        for k, i in amount_cols.items():
            v = to_num(r[i]) if i < len(r) else None
            amounts[k] = v
            if v is None and r[i] not in (None, "", "—", "-"):
                bad = True
        if bad:
            pending.append({"row": rn, "reason": "金额无法解析", "code": code, "name": name})
        rows.append({"code": code, "name": name, "amounts": amounts, "row": rn})
    return amount_cols, rows, total_rows, pending


def col_sum(rows, col):
    return sum(r["amounts"].get(col) or 0.0 for r in rows)


def prefix_sum(rows, col, prefix):
    return sum(r["amounts"].get(col) or 0.0 for r in rows
               if r["code"].startswith(prefix))


def eval_check(expr, rows):
    """安全求值: 支持 col('X'), sump('X','p'), + - () == 数字。返回 (left, right, equal)。"""
    def repl_col(m):
        return repr(col_sum(rows, m.group(1)))

    def repl_sump(m):
        return repr(prefix_sum(rows, m.group(1), m.group(2)))

    e = re.sub(r"col\(\s*'([^']+)'\s*\)", repl_col, expr)
    e = re.sub(r"sump\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", repl_sump, e)
    if not re.fullmatch(r"[\d\.eE\+\-\*\(\) ]+(==[\d\.eE\+\-\*\(\) ]+)?", e.replace("==", "==")):
        raise ValueError(f"unsupported expression: {expr}")
    if "==" not in e:
        raise ValueError(f"expression must contain ==: {expr}")
    left_s, right_s = e.split("==", 1)
    left = eval(left_s, {"__builtins__": {}}, {})
    right = eval(right_s, {"__builtins__": {}}, {})
    return left, right, abs(left - right) < 0.005


def main():
    ap = argparse.ArgumentParser(description="月末关账勾稽检查")
    ap.add_argument("--trial", required=True)
    ap.add_argument("--checklist")
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    try:
        amount_cols, rows, total_rows, pending = load_trial(args.trial)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    default_checks = {"checks": [
        {"id": "TB-OPEN", "name": "期初借贷平衡", "expr": "col('期初余额借') == col('期初余额贷')"},
        {"id": "TB-PERIOD", "name": "本期发生额借贷平衡", "expr": "col('本期发生额借') == col('本期发生额贷')"},
        {"id": "TB-CLOSE", "name": "期末借贷平衡", "expr": "col('期末余额借') == col('期末余额贷')"},
        {"id": "EQ-1", "name": "资产=负债+权益(期末)",
         "expr": "sump('期末余额借','1') == sump('期末余额贷','2') + sump('期末余额贷','4')"},
    ]}
    if args.checklist:
        try:
            with open(args.checklist, encoding="utf-8") as f:
                checks = json.load(f)
        except Exception as e:
            print(f"ERROR: bad checklist: {e}", file=sys.stderr)
            return 2
    else:
        checks = default_checks

    results = []
    for c in checks.get("checks", []):
        expr = c["expr"]
        try:
            left, right, ok = eval_check(expr, rows)
            results.append({"id": c.get("id", "?"), "name": c.get("name", expr),
                            "expr": expr, "left": round(left, 2), "right": round(right, 2),
                            "difference": round(left - right, 2), "pass": ok})
        except Exception as e:
            results.append({"id": c.get("id", "?"), "name": c.get("name", expr),
                            "expr": expr, "error": str(e), "pass": False})

    # 合计行核对
    total_row_check = {"exists": bool(total_rows), "mismatches": []}
    if total_rows:
        tr = total_rows[0]["values"]
        for col, stated in tr.items():
            if stated is None:
                continue
            computed = col_sum(rows, col)
            if abs(computed - stated) > 0.005:
                total_row_check["mismatches"].append(
                    {"column": col, "stated": stated, "recomputed": round(computed, 2)})

    verdict = "balanced" if all(r.get("pass") for r in results) else "differences_found"

    wb = Workbook()
    ws = wb.active
    ws.title = "勾稽检查"
    ws.append(["编号", "名称", "表达式", "左值", "右值", "差异", "结论"])
    for r in results:
        ws.append([r["id"], r["name"], r["expr"], r.get("left"), r.get("right"),
                   r.get("difference"), "通过" if r.get("pass") else "差异"])
    ws2 = wb.create_sheet("未决项")
    ws2.append(["行号", "原因", "科目编码", "科目名称"])
    for p in pending:
        ws2.append([p.get("row"), p.get("reason"), p.get("code", ""), p.get("name", "")])
    try:
        import os
        out_dir = os.path.dirname(os.path.abspath(args.output))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        wb.save(args.output)
    except Exception as e:
        print(f"ERROR: cannot save: {e}", file=sys.stderr)
        return 2

    report = {
        "trial": args.trial,
        "output": args.output,
        "columns_detected": list(amount_cols.keys()),
        "n_accounts": len(rows),
        "checks": results,
        "total_row_check": total_row_check,
        "pending_items": pending,
        "verdict": verdict,
        "note": "只输出建议与差异清单，不执行过账或锁定账期",
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": verdict, "checks": len(results),
                      "failed": sum(1 for r in results if not r.get("pass"))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
