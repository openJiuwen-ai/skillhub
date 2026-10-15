#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""month-end-close: 试算平衡表勾稽检查。

用法:
  python close_check.py --trial tb.xlsx --checklist checks.json --output close-check.xlsx --report r.json

退出码: 0=完成(含发现差异与未决项) 2=输入错误(文件不存在/损坏/清单非法)
"""
import argparse
import json
import math
import os
import re
import sys

from openpyxl import Workbook, load_workbook

TOTAL_RE = re.compile(r"^(合计|总计)")
EXPR_SAFE_RE = re.compile(r"^[\d\.eE\+\-\*\(\) ]+(==[\d\.eE\+\-\*\(\) ]+)?$")


class MissingColumnError(Exception):
    """勾稽表达式引用了试算表中不存在的金额列。"""

    def __init__(self, col):
        self.col = col
        super().__init__(col)


def parse_amount(v):
    """金额解析 -> (float|None, problem|None)。空白占位视为0，返回(None, None)。"""
    if v is None:
        return None, None
    if isinstance(v, bool):
        return None, "金额无法解析"
    if isinstance(v, (int, float)):
        f = float(v)
        return (None, "金额非有限数") if not math.isfinite(f) else (f, None)
    s = str(v).strip().replace(",", "").replace("，", "")
    if s in ("", "—", "-", "－", "/"):
        return None, None
    try:
        f = float(s)
    except ValueError:
        return None, "金额无法解析"
    return (None, "金额非有限数") if not math.isfinite(f) else (f, None)


def norm_code(v):
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def load_trial(path):
    """读取试算表 -> dict(amount_cols, rows, total_rows, pending, dirty_cols, sump_missing)。

    rows: {code, name, amounts{col: float|None}, problems{col: str}, row(1-based)}
    公式无缓存值(data_only 读回 None 且原始单元格是公式)单列为问题，不当作0。
    """
    wb_v = load_workbook(path, data_only=True)
    ws = wb_v.active
    table = [list(r) for r in ws.iter_rows(values_only=True)]
    wb_v.close()
    wb_f = load_workbook(path, data_only=False)
    ws_f = wb_f.active
    formulas = [list(r) for r in ws_f.iter_rows(values_only=True)]
    wb_f.close()

    def cell_value(rn, i, raw):
        f = formulas[rn][i] if rn < len(formulas) and i < len(formulas[rn]) else None
        if isinstance(f, str) and f.startswith("="):
            if raw is None:
                return None, "公式无缓存值"
            return parse_amount(raw)
        return parse_amount(raw)

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
        if ("科目" in t and "编码" in t) or t in ("科目编码", "科目"):
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
        if not t or ("借" not in t and "贷" not in t):
            continue
        has_j = "借" in t
        if "期初" in t:
            key = "期初余额借" if has_j else "期初余额贷"
        elif "本期" in t or "发生" in t:
            key = "本期发生额借" if has_j else "本期发生额贷"
        else:  # 含"期末"或无期间词的借贷列均按期末
            key = "期末余额借" if has_j else "期末余额贷"
        amount_cols.setdefault(key, i)

    rows = []
    total_rows = []
    pending = []
    dirty_cols = set()
    sump_missing = {}
    for rn in range(header_idx + 1, len(table)):
        r = table[rn]
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        code = norm_code(r[code_col]) if code_col < len(r) else ""
        name = str(r[name_col]).strip() if name_col is not None and name_col < len(r) and r[name_col] is not None else ""
        if TOTAL_RE.match(code) or TOTAL_RE.match(name):
            values = {}
            for k, i in amount_cols.items():
                raw = r[i] if i < len(r) else None
                v, p = cell_value(rn, i, raw)
                values[k] = v
                if p:
                    pending.append({"row": rn + 1, "reason": "合计行" + p, "column": k})
            total_rows.append({"row": rn + 1, "values": values})
            continue
        amounts = {}
        problems = {}
        for k, i in amount_cols.items():
            raw = r[i] if i < len(r) else None
            v, p = cell_value(rn, i, raw)
            amounts[k] = v
            if p:
                problems[k] = p
        if not code:
            pending.append({"row": rn + 1, "reason": "缺少科目编码", "name": name})
            for k, v in amounts.items():
                if v is not None and v != 0:
                    sump_missing.setdefault(k, []).append(rn + 1)
        for k, p in problems.items():
            pending.append({"row": rn + 1, "reason": p, "column": k, "code": code, "name": name})
            dirty_cols.add(k)
        rows.append({"code": code, "name": name, "amounts": amounts, "row": rn + 1})
    return {"amount_cols": amount_cols, "rows": rows, "total_rows": total_rows,
            "pending": pending, "dirty_cols": dirty_cols, "sump_missing": sump_missing}


def col_sum(rows, col):
    return sum(r["amounts"].get(col) or 0.0 for r in rows)


def prefix_sum(rows, col, prefix):
    return sum(r["amounts"].get(col) or 0.0 for r in rows
               if r["code"].startswith(prefix))


def review_structure(rows):
    """科目编码重复与父子科目并存(合并层级重复计入风险)。"""
    by_code = {}
    for r in rows:
        by_code.setdefault(r["code"], []).append(r["row"])
    duplicates = [{"code": c, "rows": v} for c, v in by_code.items() if c and len(v) > 1]
    codes = sorted(c for c in by_code if c)
    parent_child = []
    for idx, a in enumerate(codes):
        for b in codes[idx + 1:]:
            if b.startswith(a):
                parent_child.append({"parent": a, "child": b})
    return duplicates, parent_child

def eval_check(expr, rows, amount_cols):
    """安全求值: col('X')/sump('X','p') 与 + - * () ==。返回 (left, right, ok, refs)。

    引用列不存在时抛 MissingColumnError(该检查不得以 0==0 通过)。
    左右各按分位(2位小数)取整后比较，避免浮点累加误差(0.1+0.2 vs 0.3)。
    """
    refs = []

    def repl_col(m):
        col = m.group(1)
        if col not in amount_cols:
            raise MissingColumnError(col)
        refs.append((col, "col"))
        return repr(col_sum(rows, col))

    def repl_sump(m):
        col, prefix = m.group(1), m.group(2)
        if col not in amount_cols:
            raise MissingColumnError(col)
        refs.append((col, "sump"))
        return repr(prefix_sum(rows, col, prefix))

    e = re.sub(r"col\(\s*'([^']+)'\s*\)", repl_col, expr)
    e = re.sub(r"sump\(\s*'([^']+)'\s*,\s*'([^']+)'\s*\)", repl_sump, e)
    if not EXPR_SAFE_RE.fullmatch(e):
        raise ValueError(f"unsupported expression: {expr}")
    if "==" not in e:
        raise ValueError(f"expression must contain ==: {expr}")
    left_s, right_s = e.split("==", 1)
    left = round(eval(left_s, {"__builtins__": {}}, {}), 2)
    right = round(eval(right_s, {"__builtins__": {}}, {}), 2)
    return left, right, abs(left - right) < 1e-9, refs


def run_checks(checks, data):
    rows = data["rows"]
    results = []
    for c in checks.get("checks", []):
        expr = c["expr"]
        try:
            left, right, ok, refs = eval_check(expr, rows, data["amount_cols"])
        except MissingColumnError as e:
            results.append({"id": c.get("id", "?"), "name": c.get("name", expr), "expr": expr,
                            "error": f"缺少列: {e.col}（试算表未检测到该列，无法勾稽）", "pass": False})
            continue
        except Exception as e:
            results.append({"id": c.get("id", "?"), "name": c.get("name", expr),
                            "expr": expr, "error": str(e), "pass": False})
            continue
        entry = {"id": c.get("id", "?"), "name": c.get("name", expr), "expr": expr,
                 "left": left, "right": right, "difference": round(left - right, 2), "pass": ok}
        bad = sorted({col for col, kind in refs
                      if col in data["dirty_cols"] or (kind == "sump" and col in data["sump_missing"])})
        if bad:
            entry["pass"] = False
            entry["unreliable_columns"] = bad
            entry["unreliable_reason"] = "引用列存在无法解析/非有限/无缓存金额或缺编码非零行，结果不可靠"
        results.append(entry)
    return results


def main():
    ap = argparse.ArgumentParser(description="月末关账勾稽检查")
    ap.add_argument("--trial", required=True)
    ap.add_argument("--checklist")
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    try:
        data = load_trial(args.trial)
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

    results = run_checks(checks, data)

    total_row_check = {"exists": bool(data["total_rows"]), "mismatches": []}
    for tr in data["total_rows"]:
        for col, stated in tr["values"].items():
            if stated is None:
                continue
            computed = round(col_sum(data["rows"], col), 2)
            if abs(computed - round(stated, 2)) >= 0.01:
                total_row_check["mismatches"].append(
                    {"row": tr["row"], "column": col, "stated": stated, "recomputed": computed})

    duplicates, parent_child = review_structure(data["rows"])
    pending = data["pending"]

    reasons = []
    failed = [r["id"] for r in results if not r.get("pass")]
    if failed:
        reasons.append("存在未通过的勾稽检查: " + ", ".join(failed))
    if total_row_check["mismatches"]:
        reasons.append("合计行与明细复算不一致（可能重复计入或漏记）")
    if pending:
        reasons.append(f"未决项 {len(pending)} 条（金额无法解析/缺少科目编码/公式无缓存值/金额非有限数）")
    if duplicates:
        reasons.append("科目编码重复: " + ", ".join(d["code"] for d in duplicates))
    if parent_child:
        reasons.append("父子科目并存可能重复计入: "
                       + "; ".join(f"{p['parent']}<{p['child']}" for p in parent_child))
    verdict = "differences_found" if reasons else "balanced"

    wb = Workbook()
    ws = wb.active
    ws.title = "勾稽检查"
    ws.append(["编号", "名称", "表达式", "左值", "右值", "差异", "结论", "备注"])
    for r in results:
        note = r.get("error") or r.get("unreliable_reason") or ""
        ws.append([r["id"], r["name"], r["expr"], r.get("left"), r.get("right"),
                   r.get("difference"), "通过" if r.get("pass") else "差异", note])
    ws2 = wb.create_sheet("未决项")
    ws2.append(["行号", "原因", "科目编码", "科目名称", "列"])
    for p in pending:
        ws2.append([p.get("row"), p.get("reason"), p.get("code", ""),
                    p.get("name", ""), p.get("column", "")])
    ws3 = wb.create_sheet("结构检查")
    ws3.append(["类型", "内容"])
    for d in duplicates:
        ws3.append(["科目编码重复", f"{d['code']} 出现于行 {d['rows']}"])
    for p in parent_child:
        ws3.append(["父子科目并存", f"{p['parent']} 与 {p['child']}"])
    if not duplicates and not parent_child:
        ws3.append(["-", "无"])
    try:
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
        "columns_detected": list(data["amount_cols"].keys()),
        "n_accounts": len(data["rows"]),
        "checks": results,
        "total_row_check": total_row_check,
        "structure_review": {"duplicate_codes": duplicates, "parent_child": parent_child},
        "pending_items": pending,
        "verdict": verdict,
        "verdict_reasons": reasons,
        "note": "只输出建议与差异清单，不执行过账或锁定账期",
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": verdict, "checks": len(results),
                      "failed": sum(1 for r in results if not r.get("pass")),
                      "pending": len(pending)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

