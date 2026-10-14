#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""meeting-minutes-actions: 会议转写稿 -> 纪要docx + 待办追踪xlsx（跨期状态承接）。

用法:
  python minutes_actions.py --transcript t.txt [--prev-actions prev.xlsx] --outdir out/

确定性规则要点（与 SKILL.md 一致）:
  - 上期待办全部承接并保留原编号；未提及项状态原样保留（受阻/暂缓/已取消/进行中
    不无依据改写），不推断为已完成或进行中。
  - 否定式引用（"这不是A01"、"本期没有讨论A01"）不算提及、不算新进度。
  - 对上期待办的有效提及句只更新该待办状态，不重复识别为新增行动项。
  - 责任人仅在句中出现"X 负责/牵头/…"或发言人自述承诺（我负责/我会…）时认定；
    否则记"待确认"，不以主持人或其他发言人代填。
  - 截止日期缺失记"待确认"，不编造。
  - 同一句中出现两个及以上不同金额/百分比 -> 记为数字冲突，双方数值完整保留，
    不擅自选择、不定稿（写入 asr-issues.json 与纪要"待确认事项"）。
  - 纪要 docx 行动项表与追踪 xlsx 列结构及内容完全一致（含来源时间戳）。

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
ACTION_VERBS = ["负责", "牵头", "跟进", "落实", "推进", "完成", "提交", "对接", "整理",
                "部署", "排查", "修订", "核对"]
OWNER_VERB_RE = re.compile(r"(?:负责|牵头|跟进|落实|修订|部署|排查)")
CN_RUN_RE = re.compile(r"[\u4e00-\u9fa5]+$")
PREP_CHARS = "由请让叫找派交给"
SELF_RE = re.compile(r"我负责|我来|我会|我将|由我|本人")
DEADLINE_RE = re.compile(
    r"(\d{4}年\d{1,2}月\d{1,2}日(?:之?前|之?后)?|本周[一二三四五六日天]|下周[一二三四五六日天]"
    r"|月底前?|年底前?|\d{1,2}月\d{1,2}日[前后]?|\d+个工作?日内|尽快)")
DONE_RE = re.compile(r"已完成|已经完成|完成了|已办结|已办完|已闭环")
DECISION_RE = re.compile(r"(决定|一致同意|原则通过|通过|确认)")
NUM_UNIT_RE = re.compile(r"\d+(?:\.\d+)?(?:万元|万|亿|元|%)")
# 否定式引用：该子句内对旧编号/旧任务的引用不算提及、不算新进度
NEG_PATTERNS = ["这不是", "并不是", "不是", "并非", "没有讨论", "未讨论", "没讨论",
                "没有提到", "未提及", "没提及", "不涉及", "未涉及", "不包括", "不属于", "不算"]
# 明确"未讨论/未提及"表述：可作为未提及项的来源时间戳证据
NOT_DISCUSS_RE = re.compile(r"没有讨论|未讨论|没讨论|没有提到|未提及|没提及|不涉及|未涉及")

HEADER = ["编号", "责任人", "任务", "截止", "状态", "类型", "来源时间戳", "本期是否提及", "备注"]
COL_ALIASES = {
    "编号": ["编号", "行动项编号", "ID", "id"],
    "责任人": ["责任人", "负责人"],
    "任务": ["任务", "事项", "待办"],
    "截止": ["截止", "截止日期", "期限"],
    "状态": ["状态", "state"],
}


def src_tag(row):
    return "L%d@%s" % (row["line"], row["time"] or "无时间戳")


def clauses(text):
    """按句读切分子句，用于否定式引用判定。"""
    out = []
    for part in re.split(r"[。；;！!？?\n]", text or ""):
        for sub in re.split(r"[，,]", part):
            if sub.strip():
                out.append(sub)
    return out


def clause_negated(clause):
    return any(p in clause for p in NEG_PATTERNS)


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


def load_prev(path):
    wb = load_workbook(path, data_only=True)
    ws = wb.active
    header_cells = next(ws.iter_rows(max_row=1), [])
    headers = [str(c.value).strip() if c.value is not None else "" for c in header_cells]
    idx = {}
    for canonical, aliases in COL_ALIASES.items():
        for i, h in enumerate(headers):
            if h in aliases:
                idx[canonical] = i
                break
    defaults = {"编号": 0, "责任人": 1, "任务": 2, "截止": 3, "状态": 4}
    rows = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r or all(v is None or str(v).strip() == "" for v in r):
            continue
        vals = []
        for canonical in defaults:
            i = idx.get(canonical, defaults[canonical])
            v = r[i] if i < len(r) else None
            vals.append("" if v is None else str(v).strip())
        if not vals[0]:
            continue
        rows.append({"id": vals[0], "owner": vals[1], "task": vals[2],
                     "deadline": vals[3], "status": vals[4]})
    wb.close()
    return rows


def prev_id_re(pid):
    return re.compile(r"(?<![A-Za-z0-9])%s(?![A-Za-z0-9])" % re.escape(pid))


def references_prev(clause, p, id_res):
    """子句是否引用了上期待办 p（编号或完整任务文本）。"""
    if id_res.get(p["id"]) and id_res[p["id"]].search(clause):
        return True
    task = p["task"].strip()
    if len(task) >= 3 and task in clause:
        return True
    return False


def scan_mentions(rows, prev):
    """逐行扫描对上期待办的引用。

    返回 {prev_id: [(row, kind), ...]}，kind 为 "mention"（有效提及，子句未否定）
    或 "notdiscuss"（否定式"未讨论/未提及"引用，可作未提及证据，不算提及）。
    否定式且非明确未讨论的引用（如"这不是A01"）直接忽略。
    """
    id_res = {p["id"]: prev_id_re(p["id"]) for p in prev if p["id"]}
    mentions = {p["id"]: [] for p in prev}
    for row in rows:
        for p in prev:
            for cl in clauses(row["text"]):
                if not references_prev(cl, p, id_res):
                    continue
                if clause_negated(cl):
                    if NOT_DISCUSS_RE.search(cl):
                        mentions[p["id"]].append((row, "notdiscuss"))
                else:
                    mentions[p["id"]].append((row, "mention"))
    return mentions


def extract_owner(text):
    """从"X 负责/牵头/跟进…"模式提取责任人。

    取动词前紧邻的中文串（至多8字），去掉最后一个引出词（由/请/让/派…）
    之后的部分作为责任人；长度不在2-4、含人称代词或无法定位时返回 None，
    交由上层按自述承诺/待确认处理，不猜测。
    """
    m = OWNER_VERB_RE.search(text)
    if not m:
        return None
    run = CN_RUN_RE.search(text[:m.start()])
    window = run.group(0)[-8:] if run else ""
    for i in range(len(window) - 1, -1, -1):
        if window[i] in PREP_CHARS:
            window = window[i + 1:]
            break
    window = re.sub(r"(同学|老师)$", "", window)
    if not 2 <= len(window) <= 4:
        return None
    if any(c in "我你他她它咱您" for c in window):
        return None
    return window


def extract_actions(rows, mentions):
    """识别本期新增行动项与决议。

    对上期待办的有效提及句（更新状态的汇报句）不再生成新增行动项，
    避免历史完成项被重复识别为新任务。
    """
    mentioned_row_ids = set()
    for pid, refs in mentions.items():
        for row, kind in refs:
            if kind == "mention":
                mentioned_row_ids.add(row["line"])

    actions, decisions = [], []
    for r in rows:
        text = r["text"]
        if not text:
            continue
        if DECISION_RE.search(text):
            decisions.append(r)
        if not any(v in text for v in ACTION_VERBS):
            continue
        if r["line"] in mentioned_row_ids:
            continue  # 上期待办的状态汇报，不重复立新项
        owner = extract_owner(text)
        if owner is None and SELF_RE.search(text) and r["speaker"]:
            owner = r["speaker"]  # 仅发言人自述承诺时认定，主持人等不代填
        dm = DEADLINE_RE.search(text)
        actions.append({
            "owner": owner or "待确认",
            "task": text,
            "deadline": dm.group(1) if dm else "待确认",
            "source": src_tag(r),
            "mentioned": True,
        })
    return actions, decisions


def carry_over(prev, mentions):
    """上期待办承接：保留原编号；仅有效提及且明确说完成才记已完成，
    其余情况状态原样保留（未提及不推断）。"""
    carried = []
    for p in prev:
        refs = mentions.get(p["id"], [])
        valid = [row for row, kind in refs if kind == "mention"]
        entry = dict(p)
        entry["type"] = "上期承接"
        if valid:
            first = valid[0]
            entry["mentioned"] = "是"
            entry["source"] = src_tag(first)
            if DONE_RE.search(first["text"]):
                entry["status"] = "已完成"
                entry["note"] = "据%s：%s（未独立核验）" % (src_tag(first), first["text"])
            else:
                entry["note"] = "本期提及（%s），未说明完成，维持原状态" % src_tag(first)
        else:
            entry["mentioned"] = "否"
            entry["status"] = p["status"]  # 原样保留，不推断
            nd = [row for row, kind in refs if kind == "notdiscuss"]
            if nd:
                entry["source"] = src_tag(nd[0])
                entry["note"] = "本期明确未讨论（%s），维持原记录" % src_tag(nd[0])
            else:
                entry["source"] = "上期"
                entry["note"] = "本期未提及，维持原记录（不推断状态）"
        carried.append(entry)
    return carried


def asr_issues(rows):
    """金额/百分比类数字入待核清单；同一句两个及以上不同数值记为冲突，
    双方数值完整保留，不擅自选择。"""
    issues = []
    seen = set()
    for r in rows:
        text = r["text"]
        if not text:
            continue
        nums = []
        for m in NUM_UNIT_RE.finditer(text):
            v = m.group(0)
            if (v, r["line"]) not in seen:
                seen.add((v, r["line"]))
                nums.append(v)
                issues.append({"type": "number", "value": v, "where": src_tag(r),
                               "note": "ASR数字待人工核对，不自动改写"})
        distinct = sorted(set(nums))
        if len(distinct) >= 2:
            issues.append({"type": "conflict", "values": distinct, "where": src_tag(r),
                           "note": "同句数字冲突，未核实前不选择、不定稿"})
    return issues


def next_id_factory(prev):
    """新行动项编号：沿用上期编号前缀并顺延（A01..A04 -> A05..）；
    无上期或无法解析时用 A-001 起步。"""
    best = None  # (num, width, prefix, hyphen)
    for p in prev:
        m = re.match(r"^([A-Za-z]+)-?(\d+)$", p["id"] or "")
        if not m:
            continue
        cand = (int(m.group(2)), len(m.group(2)), m.group(1), "-" in p["id"])
        if best is None or (cand[0], cand[1]) > (best[0], best[1]):
            best = cand
    if best:
        start, width, prefix, hyphen = best
    else:
        prefix, hyphen, width, start = "A", True, 3, 0
    state = {"n": start}

    def make():
        state["n"] += 1
        return "%s%s%0*d" % (prefix, "-" if hyphen else "", width, state["n"])

    return make


def write_outputs(outdir, actions, decisions, carried, issues):
    os.makedirs(outdir, exist_ok=True)
    make_id = next_id_factory(carried)

    all_rows = []
    for c in carried:
        note = c.get("note", "")
        all_rows.append([c["id"], c["owner"], c["task"], c["deadline"], c["status"],
                         c["type"], c["source"], c["mentioned"], note])
    for a in actions:
        notes = []
        if a["owner"] == "待确认":
            notes.append("责任人未明确，保留待确认")
        if a["deadline"] == "待确认":
            notes.append("截止日期未定，保留待确认")
        all_rows.append([make_id(), a["owner"], a["task"], a["deadline"], "待完成",
                         "本期新增", a["source"], "是", "；".join(notes)])

    ax = os.path.join(outdir, "actions.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "待办追踪"
    ws.append(HEADER)
    for row in all_rows:
        ws.append(row)
    wb.save(ax)

    md = os.path.join(outdir, "minutes.docx")
    doc = Document()
    doc.add_heading("会议纪要", level=1)
    doc.add_heading("一、会议结论", level=2)
    if decisions:
        for d in decisions:
            doc.add_paragraph("[%s] %s" % (d.get("time") or "无时间戳", d["text"]),
                              style="List Bullet")
    else:
        doc.add_paragraph("（未识别到明确决议语句）")
    doc.add_heading("二、行动项", level=2)
    table = doc.add_table(rows=1 + len(all_rows), cols=len(HEADER))
    for j, h in enumerate(HEADER):
        table.rows[0].cells[j].text = h
    for i, row in enumerate(all_rows, start=1):
        for j in range(len(HEADER)):
            table.rows[i].cells[j].text = str(row[j])
    doc.add_heading("三、待确认事项", level=2)
    pending = [i for i in issues if i["type"] == "conflict"]
    for row in all_rows:
        if row[5] == "本期新增" and ("待确认" in row[1] or "待确认" in row[3]):
            pending.append({"where": row[6], "values": [row[2]],
                            "note": row[8] or "责任人/截止待确认"})
        elif row[5] == "上期承接" and row[7] == "否":
            pending.append({"where": row[6], "values": ["%s %s" % (row[0], row[2])],
                            "note": "本期未提及，维持原记录（%s）" % row[4]})
    if pending:
        for i in pending:
            doc.add_paragraph("[%s] %s（%s）" % (i["where"], " 与 ".join(i["values"]), i["note"]),
                              style="List Bullet")
    else:
        doc.add_paragraph("（无待确认事项）")
    doc.add_heading("四、说明", level=2)
    doc.add_paragraph(
        "未提及的上期待办维持原状态，不推断为已完成或进行中；同句数字冲突未核实前不擅自取舍；"
        "行动项编号及内容与 actions.xlsx 完全一致（含来源时间戳）。")
    doc.save(md)

    with open(os.path.join(outdir, "asr-issues.json"), "w", encoding="utf-8") as f:
        json.dump({"issues": issues,
                   "note": "ASR存疑项不自动改写，由人工核对；冲突数值双方均保留，不擅自选择"},
                  f, ensure_ascii=False, indent=2)
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
        print("ERROR: cannot read transcript: %s" % e, file=sys.stderr)
        return 2
    prev = []
    if args.prev_actions:
        try:
            prev = load_prev(args.prev_actions)
        except Exception as e:
            print("WARNING: cannot read prev actions (%s); continuing without carry-over"
                  % e, file=sys.stderr)
    mentions = scan_mentions(rows, prev)
    actions, decisions = extract_actions(rows, mentions)
    carried = carry_over(prev, mentions)
    issues = asr_issues(rows)
    result = write_outputs(args.outdir, actions, decisions, carried, issues)
    result["decisions"] = len(decisions)
    result["asr_issues"] = len(issues)
    result["carried"] = len(carried)
    result["new"] = len(actions)
    result["conflicts"] = len([i for i in issues if i["type"] == "conflict"])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
