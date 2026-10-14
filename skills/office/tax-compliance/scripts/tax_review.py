#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tax-compliance: 申报材料归集 + 适用税种规则分析（可溯源）。"""
import argparse
import csv
import json
import os
import sys

from openpyxl import Workbook, load_workbook

RULES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "references", "tax-rules-cn.json")
DISCLAIMER = ("本分析基于材料归集与公开法规条目高层摘录，不构成专业税务意见；"
              "适用以主管税务机关解释及现行有效法规文本为准。本工具不代为申报、不汇算、不过账。")


def classify_file(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        try:
            from docx import Document
            doc = Document(path)
            text = "\n".join(p.text for p in doc.paragraphs)
            if "合同" in text[:500]:
                return "合同", {"paragraphs": len(doc.paragraphs)}
            return "其他文档", {"paragraphs": len(doc.paragraphs)}
        except Exception:
            return "读取失败", {}
    if ext in (".xlsx", ".csv"):
        try:
            if ext == ".csv":
                with open(path, encoding="utf-8-sig", newline="") as f:
                    rows = list(csv.reader(f))
            else:
                wb = load_workbook(path, data_only=True)
                ws = wb.active
                rows = [list(r) for r in ws.iter_rows(values_only=True)]
                wb.close()
            header = [str(c).strip() if c is not None else "" for c in (rows[0] if rows else [])]
            flat = " ".join(header)
            if "发票" in flat or ("税额" in flat and "金额" in flat):
                return "发票台账", {"rows": len(rows) - 1}
            if "日期" in flat and "金额" in flat:
                return "收支流水", {"rows": len(rows) - 1}
            return "其他表格", {"rows": len(rows) - 1}
        except Exception:
            return "读取失败", {}
    return "其他", {}


def gather_invoice_stats(path):
    """发票台账金额/税额合计（独立于分类）。"""
    try:
        if path.lower().endswith(".csv"):
            with open(path, encoding="utf-8-sig", newline="") as f:
                rows = list(csv.reader(f))
        else:
            wb = load_workbook(path, data_only=True)
            ws = wb.active
            rows = [list(r) for r in ws.iter_rows(values_only=True)]
            wb.close()
        if not rows:
            return None
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        amount_col = tax_col = None
        for i, h in enumerate(header):
            if "金额" in h and amount_col is None:
                amount_col = i
            if "税额" in h and tax_col is None:
                tax_col = i
        total = tax_total = 0.0
        n = 0
        for r in rows[1:]:
            if not r or all(c is None or str(c).strip() == "" for c in r):
                continue
            n += 1
            if amount_col is not None and amount_col < len(r):
                try:
                    total += float(str(r[amount_col]).replace(",", ""))
                except (ValueError, TypeError):
                    pass
            if tax_col is not None and tax_col < len(r):
                try:
                    tax_total += float(str(r[tax_col]).replace(",", ""))
                except (ValueError, TypeError):
                    pass
        return {"invoice_rows": n, "amount_total": round(total, 2),
                "tax_total": round(tax_total, 2)}
    except Exception:
        return None


def select_rules(rules, business):
    ttype = business.get("taxpayer_type", "")
    acts = set(business.get("activities", []))
    selected = []
    for r in rules:
        cond = r.get("applies_when", {})
        types = cond.get("taxpayer_type", [])
        acts_req = cond.get("activities", [])
        if types and ttype not in types:
            continue
        if acts_req and not acts & set(acts_req):
            continue
        selected.append(r)
    return selected


def main():
    ap = argparse.ArgumentParser(description="税务合规材料归集与规则分析")
    ap.add_argument("--materials", required=True)
    ap.add_argument("--business", required=True)
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        with open(args.business, encoding="utf-8") as f:
            business = json.load(f)
    except Exception as e:
        print(f"ERROR: bad business json: {e}", file=sys.stderr)
        return 2
    with open(RULES_PATH, encoding="utf-8") as f:
        rules = json.load(f)

    files = []
    for root, _dirs, names in sorted(os.walk(args.materials)):
        for n in sorted(names):
            if not n.startswith("~$"):
                files.append(os.path.join(root, n))
    ledger = []
    for f in files:
        cls, stats = classify_file(f)
        if cls == "发票台账":
            inv = gather_invoice_stats(f)
            if inv:
                stats = inv
        ledger.append({"file": os.path.relpath(f, args.materials), "class": cls,
                       "stats": stats})

    has_contract = any(x["class"] == "合同" for x in ledger)
    has_invoice = any(x["class"] == "发票台账" for x in ledger)
    has_flow = any(x["class"] == "收支流水" for x in ledger)

    selected = select_rules(rules.get("rules", []), business)
    confirmations = []
    analysis = []
    for r in selected:
        status = "applicable"
        pending = []
        if r["id"] == "INV-COMPLIANCE" and not has_invoice:
            pending.append("未发现发票台账材料，发票合规核对范围待确认")
        if r["id"] == "VAT-CROSS-FREE" and not has_contract:
            pending.append("跨境服务免征需合同等支撑材料，未发现合同文件")
        if r["id"] == "CIT-25" and not has_flow:
            pending.append("缺少收支流水，收入口径待确认")
        if pending:
            status = "needs_confirmation"
            for p in pending:
                confirmations.append({"rule_id": r["id"], "issue": p})
        analysis.append({"id": r["id"], "tax_type": r["tax_type"],
                         "summary": r["summary"],
                         "source": f"{r['source']['regulation']} {r['source']['article']}",
                         "scope": r["scope"], "status": status})

    os.makedirs(args.outdir, exist_ok=True)
    # 台账 xlsx
    wb = Workbook()
    ws = wb.active
    ws.title = "材料归集"
    ws.append(["文件", "分类", "关键统计"])
    for x in ledger:
        ws.append([x["file"], x["class"], json.dumps(x["stats"], ensure_ascii=False)])
    wb.save(os.path.join(args.outdir, "materials-ledger.xlsx"))

    result = {
        "business": business,
        "materials": ledger,
        "rules": analysis,
        "confirmations": confirmations,
        "materials_summary": {"contracts": sum(1 for x in ledger if x["class"] == "合同"),
                              "invoice_files": sum(1 for x in ledger if x["class"] == "发票台账"),
                              "flow_files": sum(1 for x in ledger if x["class"] == "收支流水")},
        "disclaimer": DISCLAIMER,
    }
    with open(os.path.join(args.outdir, "rule-analysis.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.outdir, "confirmations.json"), "w", encoding="utf-8") as f:
        json.dump({"confirmations": confirmations}, f, ensure_ascii=False, indent=2)
    md = ["# 税务合规材料归集与规则分析", "",
          f"- 纳税人类型：{business.get('taxpayer_type')}",
          f"- 业务活动：{'、'.join(business.get('activities', []))}",
          f"- 材料概况：合同 {result['materials_summary']['contracts']} 份，"
          f"发票台账 {result['materials_summary']['invoice_files']} 份，"
          f"流水 {result['materials_summary']['flow_files']} 份", "",
          "## 适用规则（逐条溯源）", ""]
    for a in analysis:
        md.append(f"### {a['id']} {a['tax_type']}（{a['status']}）")
        md.append(f"- 要点：{a['summary']}")
        md.append(f"- 来源：{a['source']}")
        md.append(f"- 适用范围：{a['scope']}")
        md.append("")
    md += ["## 待确认项", ""]
    md += [f"- [{c['rule_id']}] {c['issue']}" for c in confirmations] or ["（无）"]
    md += ["", "---", DISCLAIMER]
    with open(os.path.join(args.outdir, "rule-analysis.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print(json.dumps({"files": len(ledger), "rules_selected": len(analysis),
                      "confirmations": len(confirmations)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
