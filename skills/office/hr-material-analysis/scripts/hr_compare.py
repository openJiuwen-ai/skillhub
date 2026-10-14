#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hr-material-analysis: 薪资考勤比对 + 名单汇总 + HR简报。"""
import argparse
import json
import os
import re
import sys

from docx import Document
from openpyxl import Workbook, load_workbook

TOTAL_RE = re.compile(r"^(合计|总计)")
SENSITIVE_HEADERS = ("身份证", "银行卡", "手机", "电话")


def to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def find_cols(header):
    idx = {}
    for i, h in enumerate(header):
        h = str(h).strip() if h is not None else ""
        if not h:
            continue
        if "工号" in h and "工号" not in idx:
            idx["工号"] = i
        elif "姓名" in h and "姓名" not in idx:
            idx["姓名"] = i
        elif "部门" in h and "部门" not in idx:
            idx["部门"] = i
        elif "应发" in h and "应发" not in idx:
            idx["应发"] = i
        elif "实发" in h and "实发" not in idx:
            idx["实发"] = i
        elif "应出勤" in h and "应出勤" not in idx:
            idx["应出勤"] = i
        elif "实出勤" in h and "实出勤" not in idx:
            idx["实出勤"] = i
        else:
            for s in SENSITIVE_HEADERS:
                if s in h:
                    idx.setdefault(f"敏感_{h}", i)
    return idx


def read_sheet(path, sheet=None):
    wb = load_workbook(path, data_only=True)
    ws = wb[sheet] if sheet else wb.active
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    if not rows:
        return {}, [], []
    header = [str(c).strip() if c is not None else "" for c in rows[0]]
    idx = find_cols(header)
    data = []
    skipped_totals = []
    for rn, r in enumerate(rows[1:], start=2):
        if not r or all(c is None or str(c).strip() == "" for c in r):
            continue
        emp = str(r[idx["工号"]]).strip() if "工号" in idx and idx["工号"] < len(r) and r[idx["工号"]] is not None else ""
        name = str(r[idx["姓名"]]).strip() if "姓名" in idx and idx["姓名"] < len(r) and r[idx["姓名"]] is not None else ""
        if TOTAL_RE.match(emp) or TOTAL_RE.match(name):
            skipped_totals.append(rn)
            continue
        if not emp:
            continue
        rec = {"工号": emp, "姓名": name, "row": rn}
        for k in ("部门", "应发", "实发", "应出勤", "实出勤"):
            if k in idx and idx[k] < len(r):
                v = r[idx[k]]
                rec[k] = to_num(v) if k in ("应发", "实发", "应出勤", "实出勤") else (
                    str(v).strip() if v is not None else "")
        rec["sensitive"] = {h: r[i] for h, i in idx.items()
                            if h.startswith("敏感_") and i < len(r) and r[i] is not None}
        data.append(rec)
    return idx, data, skipped_totals


def mask_value(v):
    s = str(v)
    return "■" * min(len(s), 12)


def main():
    ap = argparse.ArgumentParser(description="HR材料比对分析")
    ap.add_argument("--payroll")
    ap.add_argument("--attendance")
    ap.add_argument("--roster")
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--no-mask", action="store_true")
    args = ap.parse_args()
    if not (args.payroll or args.attendance or args.roster):
        print("ERROR: need at least one of --payroll/--attendance/--roster", file=sys.stderr)
        return 2

    payroll = att = roster = []
    try:
        if args.payroll:
            _, payroll, _ = read_sheet(args.payroll)
        if args.attendance:
            _, att, _ = read_sheet(args.attendance)
        if args.roster:
            _, roster, _ = read_sheet(args.roster)
    except Exception as e:
        print(f"ERROR: cannot read input: {e}", file=sys.stderr)
        return 2

    all_ids = {}
    for rec in payroll + att + roster:
        all_ids.setdefault(rec["工号"], True)
    # 重复工号（同表内）
    dup = {}
    for label, rows in (("payroll", payroll), ("attendance", att), ("roster", roster)):
        seen = {}
        for r in rows:
            seen.setdefault(r["工号"], []).append(r["row"])
        for k, v in seen.items():
            if len(v) > 1:
                dup.setdefault(label, []).append({"工号": k, "rows": v})

    pay_by = {r["工号"]: r for r in payroll}
    att_by = {r["工号"]: r for r in att}
    ros_by = {r["工号"]: r for r in roster}

    compare_rows = []
    anomalies = 0
    total_absence = 0.0
    for emp in sorted(all_ids.keys()):
        p = pay_by.get(emp)
        a = att_by.get(emp)
        r = ros_by.get(emp)
        flags = []
        names = {x["姓名"] for x in (p, a, r) if x and x.get("姓名")}
        name_p = (p or {}).get("姓名", "")
        if len(names) > 1:
            flags.append("姓名不一致")
        if p and not a:
            flags.append("缺考勤记录")
        if not p and a:
            flags.append("缺薪资记录")
        absence = None
        if a and a.get("应出勤") is not None and a.get("实出勤") is not None:
            absence = round(a["应出勤"] - a["实出勤"], 1)
            if absence > 0:
                total_absence += absence
        if flags:
            anomalies += 1
        compare_rows.append({
            "工号": emp,
            "姓名(薪资)": (p or {}).get("姓名", ""),
            "姓名(考勤)": (a or {}).get("姓名", ""),
            "部门": (r or {}).get("部门", (p or {}).get("部门", "")),
            "应出勤": (a or {}).get("应出勤", ""),
            "实出勤": (a or {}).get("实出勤", ""),
            "缺勤天数": absence if absence is not None else "",
            "应发": (p or {}).get("应发", ""),
            "实发": (p or {}).get("实发", ""),
            "异常": "、".join(flags),
            "敏感字段": {k: mask_value(v) for k, v in
                        ((p or {}).get("sensitive", {}) or {}).items()},
        })

    # 部门汇总
    dept = {}
    for row in compare_rows:
        d = row["部门"] or "未分配"
        dept.setdefault(d, {"count": 0, "pay": 0.0})
        dept[d]["count"] += 1
        if isinstance(row["应发"], (int, float)):
            dept[d]["pay"] += row["应发"]
    os.makedirs(args.outdir, exist_ok=True)

    mask_on = not args.no_mask
    mask_manifest = {"masked": mask_on, "fields": list(SENSITIVE_HEADERS) if mask_on else []}

    wb = Workbook()
    ws = wb.active
    ws.title = "人员比对"
    headers = ["工号", "姓名(薪资)", "姓名(考勤)", "部门", "应出勤", "实出勤",
               "缺勤天数", "应发", "实发", "异常"]
    ws.append(headers)
    for row in compare_rows:
        ws.append([row[h] for h in headers])
    ws2 = wb.create_sheet("重复工号")
    ws2.append(["来源表", "工号", "出现行号"])
    for label, items in dup.items():
        for it in items:
            ws2.append([label, it["工号"], "、".join(str(x) for x in it["rows"])])
    wb.save(os.path.join(args.outdir, "compare-report.xlsx"))

    wb2 = Workbook()
    ws3 = wb2.active
    ws3.title = "部门汇总"
    ws3.append(["部门", "人数", "应发合计"])
    for d, v in sorted(dept.items()):
        ws3.append([d, v["count"], round(v["pay"], 2)])
    wb2.save(os.path.join(args.outdir, "roster-summary.xlsx"))

    n_total = len(compare_rows)
    doc = Document()
    doc.add_heading("人力材料分析简报（草稿）", level=1)
    doc.add_paragraph(f"本期人员共 {n_total} 人，涉及部门 {len(dept)} 个。")
    doc.add_paragraph(f"考勤比对合计缺勤 {total_absence:.1f} 天；异常人员 {anomalies} 人"
                      f"（姓名不一致/缺记录等，明细见 compare-report.xlsx）。")
    if dup:
        doc.add_paragraph(f"检测到重复工号 {sum(len(v) for v in dup.values())} 组，已单独列出不合并。")
    doc.add_paragraph("敏感字段（身份证/银行卡/手机号）在输出中默认脱敏，明细见 mask-manifest.json。")
    doc.save(os.path.join(args.outdir, "draft.docx"))

    with open(os.path.join(args.outdir, "mask-manifest.json"), "w", encoding="utf-8") as f:
        json.dump(mask_manifest, f, ensure_ascii=False, indent=2)
    print(json.dumps({"people": n_total, "anomalies": anomalies,
                      "total_absence": round(total_absence, 1),
                      "duplicate_groups": sum(len(v) for v in dup.values())},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
