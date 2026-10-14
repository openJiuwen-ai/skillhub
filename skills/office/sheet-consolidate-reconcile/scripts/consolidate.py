#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sheet-consolidate-reconcile: 多源表格合并、清洗与对账。

用法:
  python consolidate.py --config cfg.json --output merged.xlsx --report r.json

退出码: 0=成功(可含差异) 2=配置/输入错误(全部源失败)
"""
import argparse
import csv
import json
import os
import re
import sys

from openpyxl import Workbook, load_workbook

TOTAL_ROW_RE = re.compile(r"^(合计|总计|小计|累计)\s*$")


def _num_or_str(v):
    """整数浮点写成 int，保持表格可读且可直接比较。"""
    if isinstance(v, float) and v == int(v):
        return int(v)
    return v


def to_num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("，", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def read_source(spec, pk, metrics, base_dir):
    """读取一个源 -> (rows, issues)。rows: [{pk, metrics{m: raw}, row_num}]"""
    path = spec["file"]
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    issues = []
    cmap = spec.get("column_map", {}) or {}
    rows = []
    if path.lower().endswith(".csv"):
        with open(path, encoding="utf-8-sig", newline="") as f:
            table = list(csv.reader(f))
        header_idx = None
        for i, r in enumerate(table[:20]):
            vals = [c.strip() for c in r]
            if cmap and all(any(cm in vals for cm in cmap.keys() if cm)):
                pass
            # 简化: 找包含主键目标列的行
            target_pk = cmap.get(pk, pk)
            if target_pk in vals:
                header_idx = i
                header = vals
                break
        if header_idx is None:
            return [], [f"header not found (need column {cmap.get(pk, pk)!r})"]
        col_idx = {name: j for j, name in enumerate(header)}
        for rn, r in enumerate(table[header_idx + 1:], header_idx + 2):
            if not r or all(not c.strip() for c in r):
                continue
            rows.append((rn, r, col_idx))
    else:
        wb = load_workbook(path, data_only=True, read_only=True)
        ws = wb[spec.get("sheet")] if spec.get("sheet") else wb.active
        header_idx = None
        header = None
        for i, r in enumerate(ws.iter_rows(min_row=1, max_row=20, values_only=True)):
            vals = [str(c).strip() if c is not None else "" for c in r]
            target_pk = cmap.get(pk, pk)
            if target_pk in vals:
                header_idx = i
                header = vals
                break
        if header_idx is None:
            wb.close()
            return [], [f"header not found (need column {cmap.get(pk, pk)!r})"]
        col_idx = {name: j for j, name in enumerate(header) if name}
        for rn, r in enumerate(ws.iter_rows(min_row=header_idx + 2, values_only=True), header_idx + 2):
            if r is None or all(c is None or str(c).strip() == "" for c in r):
                continue
            rows.append((rn, r, col_idx))
        wb.close()

    out = []
    for rn, r, col_idx in rows:
        def cell(name):
            std = cmap.get(name, name)
            j = col_idx.get(std)
            if j is None or j >= len(r):
                return None
            return r[j]
        pk_val = cell(pk)
        pk_val = str(pk_val).strip() if pk_val is not None else ""
        if TOTAL_ROW_RE.match(pk_val):
            issues.append(("total_row_skipped", rn, pk_val))
            continue
        if not pk_val:
            continue
        rec = {"pk": pk_val, "row_num": rn, "metrics": {}}
        for m in metrics:
            v = cell(m)
            rec["metrics"][m] = {"raw": v, "num": to_num(v)}
        out.append(rec)
    return out, issues


def main():
    ap = argparse.ArgumentParser(description="多源表格合并对账")
    ap.add_argument("--config", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()
    try:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print(f"ERROR: bad config: {e}", file=sys.stderr)
        return 2
    pk = cfg.get("primary_key")
    metrics = cfg.get("metrics", [])
    sources = cfg.get("sources", [])
    base_dir = os.path.dirname(os.path.abspath(args.config))
    if not pk or not metrics or not sources:
        print("ERROR: config needs primary_key/metrics/sources", file=sys.stderr)
        return 2

    per_source = {}
    failed = {}
    skipped_total = []
    for spec in sources:
        name = spec.get("source_name") or spec["file"]
        try:
            rows, issues = read_source(spec, pk, metrics, base_dir)
        except Exception as e:
            rows, issues = [], [f"read error: {e}"]
        hard = [i for i in issues if isinstance(i, str)]
        if hard:
            failed[name] = hard
            per_source[name] = []
        else:
            per_source[name] = rows
            for kind, rn, val in issues:
                skipped_total.append({"source": name, "row": rn, "pk_value": val})

    if all(not v for v in per_source.values()):
        print(f"ERROR: all sources failed: {failed}", file=sys.stderr)
        return 2

    names = [s.get("source_name") or s["file"] for s in sources]
    all_pks = []
    for n in names:
        for rec in per_source[n]:
            if rec["pk"] not in all_pks:
                all_pks.append(rec["pk"])
    all_pks.sort()

    # 重复主键（同源）
    duplicates = []
    for n in names:
        seen = {}
        for rec in per_source[n]:
            seen.setdefault(rec["pk"], []).append(rec)
        for p, recs in seen.items():
            if len(recs) > 1:
                duplicates.append({"source": n, "pk": p, "count": len(recs),
                                   "rows": [r["row_num"] for r in recs]})

    # 逐主键逐指标对账
    mismatches = []
    pending = []
    for p in all_pks:
        for m in metrics:
            vals = {}
            for n in names:
                nums = [r["metrics"][m]["num"] for r in per_source[n] if r["pk"] == p]
                nums = [x for x in nums if x is not None]
                raws = [r["metrics"][m]["raw"] for r in per_source[n] if r["pk"] == p]
                if raws and not nums:
                    pending.append({"pk": p, "metric": m, "source": n, "raw": [str(x) for x in raws]})
                if nums:
                    vals[n] = nums
            if not vals:
                continue
            distinct = set()
            for n, nums in vals.items():
                for x in nums:
                    distinct.add(round(x, 6))
            if len(distinct) > 1 or any(len(v) > 1 for v in vals.values()):
                mismatches.append({"pk": p, "metric": m,
                                   "values": {n: v for n, v in vals.items()}})

    n_present_all = 0
    for p in all_pks:
        if all(any(r["pk"] == p for r in per_source[n]) for n in names if per_source[n]):
            n_present_all += 1
    active = [n for n in names if per_source[n]]
    source_missing = {n: [p for p in all_pks
                          if not any(r["pk"] == p for r in per_source[n])]
                      for n in active}

    n_unique = len(all_pks)
    n_mismatch_pk = len({mm["pk"] for mm in mismatches})
    match_rate = round(n_present_all / n_unique, 4) if n_unique else 0.0

    # 写 merged.xlsx
    wb = Workbook()
    ws = wb.active
    ws.title = "合并汇总"
    header = [pk] + [f"{m}@{n}" for m in metrics for n in names] + ["数据一致", "缺失来源"]
    ws.append(header)
    for p in all_pks:
        row = [p]
        all_consistent = True
        missing = []
        for m in metrics:
            vals = []
            for n in names:
                nums = [r["metrics"][m]["num"] for r in per_source[n] if r["pk"] == p]
                if nums:
                    v = nums[0]
                    vals.append(int(v) if v == int(v) else v)
                    if len(nums) > 1:
                        all_consistent = False
                else:
                    vals.append("")
            # 一致性判断
            nums_flat = []
            for n in names:
                for r in per_source[n]:
                    if r["pk"] == p and r["metrics"][m]["num"] is not None:
                        nums_flat.append(r["metrics"][m]["num"])
            if len(set(round(x, 6) for x in nums_flat)) > 1:
                all_consistent = False
            row.extend(vals)
        for n in names:
            if per_source[n] and not any(r["pk"] == p for r in per_source[n]):
                missing.append(n)
        row.append("是" if all_consistent else "否")
        row.append("、".join(missing))
        ws.append(row)
    # 源明细
    ws2 = wb.create_sheet("源明细")
    ws2.append(["来源", pk, "行号"] + metrics)
    for n in names:
        for rec in per_source[n]:
            ws2.append([n, rec["pk"], rec["row_num"]] +
                       [rec["metrics"][m]["raw"] for m in metrics])
    # 重复主键
    ws3 = wb.create_sheet("重复主键")
    ws3.append(["来源", pk, "出现次数", "行号"])
    for d in duplicates:
        ws3.append([d["source"], d["pk"], d["count"], "、".join(str(x) for x in d["rows"])])
    try:
        wb.save(args.output)
    except Exception as e:
        print(f"ERROR: cannot save xlsx: {e}", file=sys.stderr)
        return 2

    report = {
        "output": args.output,
        "match_stats": {
            "n_sources": len(active),
            "n_unique_pk": n_unique,
            "n_present_all": n_present_all,
            "n_single_source": sum(1 for p in all_pks
                                   if sum(1 for n in active
                                          if any(r["pk"] == p for r in per_source[n])) == 1),
            "n_mismatch_pk": n_mismatch_pk,
            "match_rate": match_rate,
        },
        "mismatches": mismatches,
        "duplicates": duplicates,
        "source_missing": {k: v for k, v in source_missing.items() if v},
        "skipped_total_rows": skipped_total,
        "pending_values": pending,
        "failed_sources": failed,
    }
    with open(args.report, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"match_stats": report["match_stats"],
                      "mismatch_count": len(mismatches),
                      "duplicate_groups": len(duplicates)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
