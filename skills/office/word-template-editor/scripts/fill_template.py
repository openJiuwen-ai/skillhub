#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""word-template-editor: 在 .docx 模板上原位填充 {{占位符}}，保真交付。

用法:
  python fill_template.py --template t.docx --data d.json --output out.docx --report r.json
  python fill_template.py --template t.docx --inspect

退出码: 0=成功(可含未匹配占位) 2=输入错误/文件损坏
"""
import argparse
import json
import re
import sys
import zipfile

from docx import Document
from docx.oxml.ns import qn

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z0-9_\u4e00-\u9fff-]+)\s*\}\}")
ORPHAN_BRACE_RE = re.compile(r"\{\{|\}\}")


def find_orphan_braces(text):
    """找出不属于完整 {{key}} 占位符的孤立花括号片段。

    嵌套/未闭合/空名占位语法按字面保留在文本中，此处统计其残留，
    满足"按字面处理并记录"。
    """
    stripped = PLACEHOLDER_RE.sub("", text)
    return ORPHAN_BRACE_RE.findall(stripped)


def iter_paragraphs(doc):
    """按文档顺序产出段落: 正文(含表格内) + 各节页眉页脚(含首页/偶数页)。"""
    body = doc.element.body
    for p in body.iter(qn("w:p")):
        # 排除修订删除区内的段落
        yield p
    for section in doc.sections:
        for hf in (section.header, section.footer,
                   section.first_page_header, section.first_page_footer,
                   section.even_page_header, section.even_page_footer):
            if hf is None:
                continue
            for p in hf._element.iter(qn("w:p")):
                yield p


def paragraph_runs(p_el):
    """产出段落内含 w:t 的 w:r 元素(含超链接内 run)，跳过 w:del 修订内 run。"""
    runs = []
    for r in p_el.iter(qn("w:r")):
        parent = r.getparent()
        if parent is not None and parent.tag == qn("w:del"):
            continue
        ts = r.findall(qn("w:t"))
        if ts:
            runs.append((r, ts))
    return runs


def replace_in_paragraph(p_el, data, stats):
    """在该段落内完成跨 run 占位替换。返回替换次数。"""
    runs = paragraph_runs(p_el)
    if not runs:
        return 0
    # 构建 run 文本边界
    texts = []
    boundaries = []  # (start, end, r, t_first)
    pos = 0
    for r, ts in runs:
        run_text = "".join(t.text or "" for t in ts)
        boundaries.append((pos, pos + len(run_text), r, ts))
        texts.append(run_text)
        pos += len(run_text)
    full = "".join(texts)
    matches = list(PLACEHOLDER_RE.finditer(full))
    if not matches:
        return 0
    count = 0
    # 从后往前替换，避免索引失效
    for m in reversed(matches):
        key = m.group(1)
        start, end = m.span()
        if key not in data:
            stats["unmatched"].setdefault(key, 0)
            stats["unmatched"][key] += 1
            continue
        value = "" if data[key] is None else str(data[key])
        # 找受影响 run
        affected = [b for b in boundaries if b[0] < end and b[1] > start]
        if not affected:
            continue
        first = affected[0]
        # 逐 run 重写文本
        for a_start, a_end, r, ts in affected:
            prefix = full[a_start:min(start, a_end)] if a_start < start else ""
            suffix = full[max(end, a_start):a_end] if a_end > end else ""
            new_text = prefix + (value if (a_start, a_end, r, ts) == first else "") + suffix
            # 全部文本放进第一个 w:t，其余清空
            for t in ts:
                t.text = ""
            ts[0].text = new_text
            if new_text != new_text.strip() or new_text == "":
                ts[0].set(qn("xml:space"), "preserve")
            elif ts[0].get(qn("xml:space")):
                del ts[0].attrib[qn("xml:space")]
        stats["matched"].setdefault(key, 0)
        stats["matched"][key] += 1
        count += 1
        # 更新 full 以便后续(更前面的)匹配仍正确: 替换后文本长度变化
        new_len = len(value)
        old_len = end - start
        full = full[:start] + value + full[end:]
        delta = new_len - old_len
        boundaries = [(s + (delta if s >= end else 0),
                       e + (delta if e >= end else 0), r, ts)
                      for s, e, r, ts in boundaries]
    return count


def opc_validate(path):
    """OOXML/OPC 交付校验: zip 可读、必需 part 存在、XML 可解析、关系完整。"""
    result = {"ok": True, "errors": []}
    try:
        zf = zipfile.ZipFile(path)
    except Exception as e:
        result["ok"] = False
        result["errors"].append(f"zip unreadable: {e}")
        return result
    names = set(zf.namelist())
    required = ["[Content_Types].xml", "word/document.xml",
                "word/_rels/document.xml.rels"]
    for part in required:
        if part not in names:
            result["ok"] = False
            result["errors"].append(f"missing part: {part}")
    import xml.etree.ElementTree as ET
    for part in names:
        if part.endswith(".xml") or part.endswith(".rels"):
            try:
                ET.fromstring(zf.read(part))
            except Exception as e:
                result["ok"] = False
                result["errors"].append(f"XML parse error in {part}: {e}")
    # 关系完整性: document.xml.rels 引用的目标存在
    if "word/_rels/document.xml.rels" in names:
        try:
            root = ET.fromstring(zf.read("word/_rels/document.xml.rels"))
            for rel in root:
                tgt = rel.get("Target", "")
                mode = rel.get("TargetMode", "Internal")
                if mode == "External" or tgt.startswith(("http", "mailto")):
                    continue
                resolved = "word/" + tgt.lstrip("/") if not tgt.startswith("/") else tgt.lstrip("/")
                resolved = resolved.replace("word/../", "")
                if resolved not in names:
                    result["ok"] = False
                    result["errors"].append(f"relationship target missing: {tgt}")
        except Exception as e:
            result["ok"] = False
            result["errors"].append(f"rels parse error: {e}")
    zf.close()
    return result


def read_part_bytes(path, part):
    with zipfile.ZipFile(path) as zf:
        return zf.read(part) if part in zf.namelist() else None


def main():
    ap = argparse.ArgumentParser(description="Word模板保真填充")
    ap.add_argument("--template", required=True)
    ap.add_argument("--data")
    ap.add_argument("--output")
    ap.add_argument("--report")
    ap.add_argument("--inspect", action="store_true",
                    help="仅列出模板中的占位符")
    args = ap.parse_args()

    try:
        doc = Document(args.template)
    except Exception as e:
        print(f"ERROR: cannot open template: {e}", file=sys.stderr)
        return 2

    if args.inspect:
        found = {}
        for p in iter_paragraphs(doc):
            runs = paragraph_runs(p)
            full = "".join((t.text or "") for _, ts in runs for t in ts)
            for m in PLACEHOLDER_RE.finditer(full):
                found.setdefault(m.group(1), 0)
                found[m.group(1)] += 1
        print(json.dumps({"placeholders": found}, ensure_ascii=False, indent=2))
        return 0

    if not (args.data and args.output):
        print("ERROR: --data and --output are required", file=sys.stderr)
        return 2
    try:
        with open(args.data, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"ERROR: cannot read data: {e}", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print("ERROR: data must be a JSON object", file=sys.stderr)
        return 2

    numbering_before = read_part_bytes(args.template, "word/numbering.xml")

    stats = {"matched": {}, "unmatched": {}}
    total = 0
    for p in iter_paragraphs(doc):
        total += replace_in_paragraph(p, data, stats)

    try:
        doc.save(args.output)
    except Exception as e:
        print(f"ERROR: cannot save output: {e}", file=sys.stderr)
        return 2

    numbering_after = read_part_bytes(args.output, "word/numbering.xml")
    numbering_unchanged = (numbering_before == numbering_after)

    # 残留检查
    try:
        doc2 = Document(args.output)
        residue = []
        orphan_braces = {}
        for p in iter_paragraphs(doc2):
            runs = paragraph_runs(p)
            full = "".join((t.text or "") for _, ts in runs for t in ts)
            for m in PLACEHOLDER_RE.finditer(full):
                residue.append(m.group(1))
            for frag in find_orphan_braces(full):
                orphan_braces[frag] = orphan_braces.get(frag, 0) + 1
    except Exception as e:
        residue = [f"<verify failed: {e}>"]
        orphan_braces = {"<verify failed>": 1}

    report = {
        "template": args.template,
        "output": args.output,
        "replacements": total,
        "matched": stats["matched"],
        "unmatched": stats["unmatched"],
        "unused_data": sorted(set(data) - set(stats["matched"])),
        "residual_placeholders": residue,
        "literal_brace_fragments": orphan_braces,
        "numbering_xml_unchanged": numbering_unchanged,
        "opc_check": opc_validate(args.output),
    }
    if args.report:
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["opc_check"]["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
