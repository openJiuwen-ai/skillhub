#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""input-preflight-check: 清单三态核对 + 数据范围校验。"""
import argparse
import json
import os
import re
import sys


def sniff_content(path):
    """返回文件可提取文本（用于内容模式匹配）。"""
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in (".md", ".txt", ".csv"):
            with open(path, encoding="utf-8", errors="ignore") as f:
                return f.read(200000)
        if ext == ".docx":
            from docx import Document
            doc = Document(path)
            return "\n".join(p.text for p in doc.paragraphs)
        if ext == ".xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(path, data_only=True, read_only=True)
            parts = []
            for ws in wb.worksheets:
                for row in ws.iter_rows(max_row=50, values_only=True):
                    parts.append(" ".join(str(c) for c in row if c is not None))
            wb.close()
            return "\n".join(parts)
    except Exception:
        return ""
    return ""


def mode_checklist(args):
    try:
        with open(args.checklist, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"ERROR: bad checklist: {e}", file=sys.stderr)
        return 2
    files = []
    for root, _dirs, names in os.walk(args.dir):
        for n in sorted(names):
            files.append(os.path.join(root, n))
    contents = {}
    results = []
    reasons = []
    for item in cfg.get("items", []):
        pats = item.get("patterns", [])
        fuzzy = item.get("fuzzy", [])
        required = item.get("required", True)
        matched = []
        fuzzy_matched = []
        for f in files:
            fname = os.path.basename(f)
            if f not in contents:
                contents[f] = sniff_content(f)
            content = contents[f]
            if pats and all((p in fname) or (p in content) for p in pats):
                matched.append(os.path.relpath(f, args.dir))
            elif fuzzy and any(p in fname for p in fuzzy):
                fuzzy_matched.append(os.path.relpath(f, args.dir))
        if matched:
            state = "ready"
        elif fuzzy_matched:
            state = "needs_confirm"
            reasons.append(f"需确认: {item.get('name')}（文件名近似命中但内容模式未验证: "
                           f"{fuzzy_matched[:3]}）")
        else:
            state = "missing"
            if required:
                reasons.append(f"缺失: {item.get('name')}（无文件命中全部模式 {pats}）")
        results.append({"id": item.get("id"), "name": item.get("name"),
                        "state": state, "matched_files": matched[:5],
                        "fuzzy_files": fuzzy_matched[:5]})
    blocking = any(r["state"] == "missing" for r in results
                   if next((i.get("required", True) for i in cfg.get("items", [])
                            if i.get("id") == r["id"]), True))
    confirm = any(r["state"] == "needs_confirm" for r in results)
    verdict = "block" if blocking else ("confirm_needed" if confirm else "pass")
    report = {"mode": "checklist", "verdict": verdict, "items": results,
              "reasons": reasons,
              "note": "verdict=block时不产出分析结论"}
    dump(args.report, report)
    return 0


def mode_data(args):
    from openpyxl import load_workbook
    try:
        with open(args.requirements, encoding="utf-8") as f:
            req = json.load(f)
    except Exception as e:
        print(f"ERROR: bad requirements: {e}", file=sys.stderr)
        return 2
    try:
        wb = load_workbook(args.data, data_only=True)
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        wb.close()
    except Exception as e:
        print(f"ERROR: cannot read data: {e}", file=sys.stderr)
        return 2
    if not rows:
        dump(args.report, {"mode": "data", "verdict": "block", "reasons": ["空表"]})
        return 0
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    data_rows = [r for r in rows[1:] if any(c is not None and str(c).strip() for c in r)]

    checks = []
    reasons = []
    # 列检查
    missing_cols = [c for c in req.get("columns", []) if c not in header]
    checks.append({"check": "required_columns", "ok": not missing_cols,
                   "missing": missing_cols})
    if missing_cols:
        reasons.append(f"必需列缺失: {missing_cols}")
    # 单位
    for col, unit in (req.get("units") or {}).items():
        if col not in header:
            continue
        idx = header.index(col)
        vals = [r[idx] for r in data_rows[:20]
                if idx < len(r) and isinstance(r[idx], (int, float))]
        header_has_unit = unit in header[idx]
        checks.append({"check": "unit", "column": col, "expected_unit": unit,
                       "header": header[idx], "numeric_values": len(vals),
                       "ok": header_has_unit or not vals,
                       "note": None if header_has_unit else
                       "表头未注明单位且列为数值，需确认口径"})
        if not header_has_unit and vals:
            reasons.append(f"单位待确认: {col} 列要求单位'{unit}'但表头'{header[idx]}'未注明")
    # 日期范围
    dc = req.get("date_column")
    dr = req.get("date_range")
    date_check = None
    if dc and dr and dc in header:
        idx = header.index(dc)
        out_of_range = []
        parsed = 0
        for ri, r in enumerate(data_rows, start=2):
            v = r[idx] if idx < len(r) else None
            if v is None:
                continue
            s = str(v)[:10].replace("/", "-").replace(".", "-")
            m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})", s)
            if m:
                parsed += 1
                iso = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
                if not (dr[0] <= iso <= dr[1]):
                    out_of_range.append({"row": ri, "value": iso})
        date_check = {"check": "date_range", "column": dc,
                      "expected": dr, "parsed_rows": parsed,
                      "out_of_range": out_of_range[:20],
                      "ok": not out_of_range}
        checks.append(date_check)
        if out_of_range:
            reasons.append(f"日期超出范围: {len(out_of_range)} 行不在 {dr} 内")
    # 行数
    min_rows = req.get("min_rows", 1)
    checks.append({"check": "min_rows", "ok": len(data_rows) >= min_rows,
                   "rows": len(data_rows), "min": min_rows})
    if len(data_rows) < min_rows:
        reasons.append(f"有效行数不足: {len(data_rows)} < {min_rows}")
    blocking = (missing_cols or len(data_rows) < min_rows
                or (date_check and not date_check["ok"]
                    and req.get("date_range_block", True)))
    verdict = "block" if blocking else ("confirm_needed" if reasons else "pass")
    report = {"mode": "data", "verdict": verdict, "checks": checks,
              "reasons": reasons, "rows": len(data_rows),
              "note": "verdict=block时不产出分析结论"}
    dump(args.report, report)
    return 0


def dump(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print(json.dumps({"verdict": obj["verdict"],
                      "reasons": obj.get("reasons", [])[:5]}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="材料齐全性与数据范围校验")
    sub = ap.add_subparsers(dest="mode", required=True)
    p1 = sub.add_parser("checklist")
    p1.add_argument("--checklist", required=True)
    p1.add_argument("--dir", required=True)
    p1.add_argument("--report", required=True)
    p1.set_defaults(func=mode_checklist)
    p2 = sub.add_parser("data")
    p2.add_argument("--data", required=True)
    p2.add_argument("--requirements", required=True)
    p2.add_argument("--report", required=True)
    p2.set_defaults(func=mode_data)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
