#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes-actions: 会议转写稿 -> 纪要docx + 待办追踪xlsx（跨期五态承接）。

用法:
  python minutes_actions.py --transcript t.txt [--prev-actions prev.xlsx] --outdir out/

退出码: 0=完成 2=输入错误
"""
import argparse
import json
import os
import re
import sys

from docx import Document
from openpyxl import Workbook, load_workbook

LINE_RE = re.compile(r"^\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s*([^:：\s]{1,20})[:：]\s*(.*)$")
ACTION_VERBS = ["负责", "牵头", "跟进", "落实", "推进", "完成", "提交", "对接", "整理", "部署", "排查", "修订"]
OWNER_RE = re.compile(r"([\u4e00-\u9fa5]{2,4})(?:同学|老师)?(?:负责|牵头|跟进|落实|对接)")
DEADLINE_RE = re.compile(r"(本周[一二三四五六日天]|下周[一二三四五六日天]|月底前?|年底前?|\d{1,2}月\d{1,2}日[前后]?|\d+个工作?日内|尽快)")
DECISION_RE = re.compile(r"(决定|一致同意|原则通过|通过|确认)")
OPEN_STATES = {"进行中", "未完成", "受阻", "开放", "open", "in_progress"}


def parse_transcript(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for ln, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            m = LINE_RE.match(line)
            if m:
                rows.append({"line": ln, "time": m.group(1), "speaker": m.group(2),
                             "text": m.group(3).strip()})
            else:
                rows.append({"line": ln, "time": None, "speaker": "", "text": line})
    return rows


def extract_actions(rows):
    actions = []
    decisions = []
    for r in rows:
        text = r["text"]
        if not text:
            continue
        if DECISION_RE.search(text):
            decisions.append(r)
        is_action = any(v in text for v in ACTION_VERBS)
        if not is_action:
            continue
        owner = None
        om = OWNER_RE.search(text)
        if om:
            owner = om.group(1)
        elif r["speaker"] and len(r["speaker"]) <= 6 and not any(c.isdigit() for c in r["speaker"]):
            owner = r["speaker"]
        dm = DEADLINE_RE.search(text)
        actions.append({
            "owner": owner or "待确认",
            "task": text,
            "deadline": dm.group(1) if dm else "",
            "source": f"L{r['line']}@{r['time'] or '无时间戳'}",
            "mentioned": True,
        })
    return actions, decisions


def asr_issues(actions, decisions):
    issues = []
    seen = set()
    for r in actions + decisions:
        text = r.get("task") or r.get("text") or ""
        for num in re.findall(r"\d+(?:\.\d+)?[%万元亿]?", text):
            key = ("number", num, r.get("source", f"L{r.get('line')}"))
            if key not in seen:
                seen.add(key)
                issues.append({"type": "number", "value": num,
                               "where": r.get("source", f"L{r.get('line')}"),
                               "note": "ASR数字待人工核对"})
    return issues


def load_prev(path):
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    headers = [str(c.value).strip() if c.value else "" for c in next(ws.iter_rows(max_row=1))]
    idx = {h: i for i, h in enumerate(headers)}
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or not r[0]:
            continue
        rows.append({
            "id": str(r[idx.get("编号", 0)] or ""),
            "owner": str(r[idx.get("责任人", 1)] or ""),
            "task": str(r[idx.get("任务", 2)] or ""),
            "deadline": str(r[idx.get("截止", 3)] or ""),
            "status": str(r[idx.get("状态", 4)] or ""),
        })
    wb.close()
    return rows


def carry_over(prev, new_actions):
    """上期待办承接: 开放项全部承接新编号; 判断本期是否提及+完成。"""
    carried = []
    for p in prev:
        entry = dict(p)
        entry["carried_from"] = p["id"]
        mentioned = False
        done = False
        # 用任务文本的显著词判断是否被本期提及
        tokens = [t for t in re.split(r"[^\u4e00-\u9fa5]{2,}", p["task"])
                  if p["task"].find(t) >= 0 and len(t) >= 2]
        keywords = re.findall(r"[\u4e00-\u9fa5]{2,}", p["task"])
        for a in new_actions:
            if any(k in a["task"] for k in keywords if len(k) >= 2):
                mentioned = True
                if re.search(r"(已完成|已完成|完成|办结|闭环)", a["task"]):
                    done = True
                break
        # 也扫描全部转写文本由 extract 阶段决定; 这里仅依据行动项
        if p["status"] in OPEN_STATES:
            entry["status"] = "已完成" if done else "进行中"
            entry["mentioned"] = mentioned
            carried.append(entry)
        else:
            entry["status"] = p["status"]
            entry["mentioned"] = mentioned
            carried.append(entry)
    return carried


def write_outputs(outdir, actions, decisions, carried, issues):
    os.makedirs(outdir, exist_ok=True)
    # 统一编号: 承接项在前(保留旧序), 新项在后
    all_rows = []
    n = 0
    for c in carried:
        n += 1
        all_rows.append([f"A-{n:03d}", c["owner"], c["task"], c["deadline"],
                         c["status"], c["carried_from"], "", "是" if c["mentioned"] else "否"])
    for a in actions:
        n += 1
        all_rows.append([f"A-{n:03d}", a["owner"], a["task"], a["deadline"],
                         "新增", "", a["source"], "是"])

    ax = os.path.join(outdir, "actions.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "待办追踪"
    ws.append(["编号", "责任人", "任务", "截止", "状态", "承接自", "来源时间戳", "本期是否提及"])
    for row in all_rows:
        ws.append(row)
    wb.save(ax)

    md = os.path.join(outdir, "minutes.docx")
    doc = Document()
    doc.add_heading("会议纪要", level=1)
    doc.add_heading("一、会议结论", level=2)
    if decisions:
        for d in decisions:
            doc.add_paragraph(f"[{d.get('time') or ''}] {d['text']}", style="List Bullet")
    else:
        doc.add_paragraph("（未识别到明确决议语句）")
    doc.add_heading("二、行动项", level=2)
    table = doc.add_table(rows=1 + len(all_rows), cols=5)
    table.rows[0].cells[0].text = "编号"
    table.rows[0].cells[1].text = "责任人"
    table.rows[0].cells[2].text = "任务"
    table.rows[0].cells[3].text = "截止"
    table.rows[0].cells[4].text = "状态"
    for i, row in enumerate(all_rows, start=1):
        for j in range(5):
            table.rows[i].cells[j].text = str(row[j])
    doc.save(md)

    with open(os.path.join(outdir, "asr-issues.json"), "w", encoding="utf-8") as f:
        json.dump({"issues": issues,
                   "note": "ASR存疑项不自动改写，由人工核对"}, f, ensure_ascii=False, indent=2)
    return {"minutes": md, "actions": ax, "n_actions": len(all_rows)}


def main():
    ap = argparse.ArgumentParser(description="会议纪要与行动项闭环")
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--prev-actions")
    ap.add_argument("--outdir", required=True)
    args = ap.parse_args()
    try:
        rows = parse_transcript(args.transcript)
    except Exception as e:
        print(f"ERROR: cannot read transcript: {e}", file=sys.stderr)
        return 2
    actions, decisions = extract_actions(rows)
    issues = asr_issues(actions, decisions)
    carried = []
    if args.prev_actions:
        try:
            prev = load_prev(args.prev_actions)
            carried = carry_over(prev, actions)
        except Exception as e:
            print(f"WARNING: cannot read prev actions ({e}); continuing without carry-over",
                  file=sys.stderr)
    result = write_outputs(args.outdir, actions, decisions, carried, issues)
    result["decisions"] = len(decisions)
    result["asr_issues"] = len(issues)
    result["carried"] = len(carried)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
