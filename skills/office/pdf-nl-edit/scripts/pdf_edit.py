#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pdf-nl-edit: PDF 指定页局部文字编辑（底层删除 + 同位插入）。

语义：默认把指定页内**所有** `--find` 命中全部替换为 `--replace`，
并逐处插入新文本；不会“删多处只插一处”。命中数为 0 时退出 1 且不产出文件。

用法:
  python pdf_edit.py --input in.pdf --page 2 --find 旧标题 --replace 新标题 \
      --output out.pdf [--report r.json] [--purge]

退出码:
  0 = 成功
  1 = 目标文本未找到（不产出输出文件）
  2 = 输入/参数错误（页码越界/损坏/加密/同输入输出路径/替换文本放不下/覆盖邻近内容/不支持的结构/写入失败）

安全性说明: 普通编辑使用增量保存，会保留旧对象版本，**不构成可恢复的敏感原文删除**；
如需不可恢复重写请使用 `--purge`（仍非取证级安全擦除），或使用 doc-sensitive-redact。

实现说明（运行时依赖全部宽松许可，无 PyMuPDF/fitz）:
  - 定位：pdfplumber / pdfminer.six (MIT) 给出字符框与文本（等价 search_for 语义）
  - 改写：直接对目标页内容流做算子级手术（删除命中字形码、就位插入新文本），
          不做整页栅格化，不使用白框遮盖，不整对象误删含未匹配文本的内容
  - 落盘：pypdf (BSD-3-Clause) 结构读写；默认 append-only 会话来写（真正增量，
          原文件字节原样保留 + 追加修改对象），`--purge` 才整文件重写
"""
import argparse
import io
import json
import os
import re
import sys
import tempfile

import pdfplumber
import pypdf
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)

PAGE_MARGIN = 6.0
MIN_FONTSIZE = 4.0

# 标准 14 号基础字体的字宽来自运行时依赖 pdfminer.six（MIT）自带的数据
# `pdfminer.fontmetrics.FONT_METRICS`；该模块内嵌 Adobe Core AFM 许可（见其
# 文件头），故不自行手抄/维护字体宽度表。仅在简单字体没有 /Widths 数组时使用。
# Symbol / ZapfDingbats 不是普通 ASCII 映射，不参与此回退。
try:
    from pdfminer.fontmetrics import FONT_METRICS as _STD_FONT_METRICS
except Exception:  # pragma: no cover - pdfminer.six 随 pdfplumber 提供
    _STD_FONT_METRICS = {}

_NON_TEXT_STANDARD_FONTS = ("Symbol", "ZapfDingbats")


def _standard_char_width(basefont, ch):
    """标准 14 号基础字体中字符 ch 的 AFM 宽度（1/1000 em）；无则返回 None。"""
    name = (basefont or "").lstrip("/")
    if "+" in name:  # 子集前缀 AAAAAA+Times-Roman
        name = name.split("+", 1)[1]
    if not name or name in _NON_TEXT_STANDARD_FONTS:
        return None
    entry = _STD_FONT_METRICS.get(name)
    if not entry:
        return None
    w = entry[1].get(ch)
    return float(w) if w is not None else None


def _is_wide(ch):
    """CJK/全角等按 1em 估算的宽字符。"""
    o = ord(ch)
    return (
        0x1100 <= o <= 0x115F
        or 0x2E80 <= o <= 0xA4CF
        or 0xAC00 <= o <= 0xD7A3
        or 0xF900 <= o <= 0xFAFF
        or 0xFE30 <= o <= 0xFE4F
        or 0xFF00 <= o <= 0xFF60
        or 0xFFE0 <= o <= 0xFFE6
    )


def _has_cjk(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


# ---------------------------------------------------------------------------
# 字体解码（用于把内容流里的字符串还原为 Unicode，并反向编码新文本）
# ---------------------------------------------------------------------------

_WS = b"\x00\x09\x0a\x0c\x0d\x20"
_DELIM = b"()<>[]{}/%"


def _parse_tounicode(data):
    mapping = {}
    for blk in re.findall(rb"beginbfchar(.*?)endbfchar", data, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]*)>", blk):
            try:
                mapping[int(src, 16)] = bytes.fromhex(dst.decode()).decode(
                    "utf-16-be", "replace")
            except ValueError:
                continue
    for blk in re.findall(rb"beginbfrange(.*?)endbfrange", data, re.S):
        for lo, hi, dst in re.findall(
                rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", blk):
            lo_i, hi_i, dv = int(lo, 16), int(hi, 16), int(dst, 16)
            for k in range(lo_i, hi_i + 1):
                mapping[k] = chr(dv + (k - lo_i))
    return mapping


class _FontCodec:
    """把字体资源的信息归一化为 2 字节(CID)或 1 字节(简单字体)编解码。"""

    def __init__(self, name, font_dict):
        self.name = name
        self.subtype = str(font_dict.get("/Subtype", ""))
        self.basefont = str(font_dict.get("/BaseFont", ""))
        encoding = str(font_dict.get("/Encoding", ""))
        self.is_cid = self.subtype == "/Type0"
        self.two_byte = self.is_cid
        self.tounicode = {}
        tu = font_dict.get("/ToUnicode")
        if tu is not None:
            try:
                self.tounicode = _parse_tounicode(tu.get_object().get_data())
            except Exception:
                self.tounicode = {}
        self.predefined_unicode = False
        if self.is_cid:
            self.predefined_unicode = encoding.startswith("/Uni")
            if not self.predefined_unicode and encoding.startswith("/Identity"):
                self.predefined_unicode = True
        else:
            self.winansi = encoding in ("/WinAnsiEncoding", "/StandardEncoding", "")
            self.macroman = encoding == "/MacRomanEncoding"
        # --- 真实字宽（单位 1/1000 em）：用于字距补偿与排版测量 ---
        self.widths = {}          # 简单字体：码位 -> 字宽
        self.cid_widths = {}      # CID 字体：码位 -> 字宽
        self.dw = 1000.0          # CID 默认字宽 /DW
        self.missing_width = None
        self._rev = None
        try:
            if self.is_cid:
                self._load_cid_widths(font_dict)
            else:
                self._load_simple_widths(font_dict)
        except Exception:
            self.widths = {}
            self.cid_widths = {}

    def _load_simple_widths(self, font_dict):
        widths = font_dict.get("/Widths")
        first = font_dict.get("/FirstChar")
        if widths is not None and first is not None:
            try:
                arr = widths.get_object()
                base = int(first)
                for k in range(len(arr)):
                    try:
                        self.widths[base + k] = float(arr[k])
                    except (TypeError, ValueError, IndexError):
                        pass
            except Exception:
                self.widths = {}
        fd = font_dict.get("/FontDescriptor")
        if fd is not None:
            try:
                mw = fd.get_object().get("/MissingWidth")
                if mw is not None:
                    self.missing_width = float(mw)
            except Exception:
                pass

    def _load_cid_widths(self, font_dict):
        desc = font_dict.get("/DescendantFonts")
        d0 = None
        if desc is not None:
            try:
                d0 = desc.get_object()[0].get_object()
            except Exception:
                d0 = None
        if d0 is None:
            return
        dw = d0.get("/DW")
        if dw is not None:
            try:
                self.dw = float(dw)
            except (TypeError, ValueError):
                pass
        warr = d0.get("/W")
        if warr is None:
            return
        try:
            arr = warr.get_object()
        except Exception:
            return
        i, n = 0, len(arr)
        while i < n:
            try:
                first = int(arr[i])
            except (TypeError, ValueError):
                i += 1
                continue
            i += 1
            if i >= n:
                break
            item = arr[i]
            if hasattr(item, "get_object"):
                item = item.get_object()
            if isinstance(item, (list, tuple)) or type(item).__name__ == "ArrayObject":
                for k, v in enumerate(item):
                    try:
                        self.cid_widths[first + k] = float(v)
                    except (TypeError, ValueError):
                        pass
                i += 1
            else:
                i += 1
                if i >= n:
                    break
                try:
                    last = int(item)
                    wv = float(arr[i])
                except (TypeError, ValueError):
                    i += 1
                    continue
                i += 1
                for c in range(first, last + 1):
                    self.cid_widths[c] = wv

    def code_for(self, ch):
        """字符 -> 该字体中的字形码；无法表示返回 None。"""
        if self.two_byte:
            if self.predefined_unicode:
                cp = ord(ch)
                return cp if cp <= 0xFFFF else None
            if self.tounicode:
                if self._rev is None:
                    self._rev = {v: k for k, v in self.tounicode.items()}
                return self._rev.get(ch)
            return None
        try:
            return ch.encode("cp1252")[0]
        except (UnicodeEncodeError, IndexError):
            return None

    def width_for_code(self, code, ch=None):
        """字形码字宽（1/1000 em）：优先字体 /Widths|/W，再标准 14 号 AFM。"""
        if self.is_cid:
            return self.cid_widths.get(code, self.dw)
        if code in self.widths:
            return self.widths[code]
        w = _standard_char_width(self.basefont, ch) if ch is not None else None
        if w is not None:
            return w
        if self.missing_width is not None:
            return self.missing_width
        return 500.0

    def measure(self, text, size):
        """用该字体真实字宽测量文本宽度（单位 pt）。"""
        total = 0.0
        for ch in text:
            code = self.code_for(ch)
            if code is None:
                total += 1.0 if _is_wide(ch) else 0.5
            else:
                total += self.width_for_code(code, ch) / 1000.0
        return total * size

    def decode(self, raw):
        out = []
        if self.two_byte:
            codes = [int.from_bytes(raw[i:i + 2], "big") for i in range(0, len(raw) - 1, 2)]
            if self.tounicode:
                for c in codes:
                    out.append(self.tounicode.get(c, "\ufffd"))
                return "".join(out)
            # 预定义 Unicode CMap（UniGB-UTF16-H 等）：字节即 UTF-16BE 码元
            return raw.decode("utf-16-be", "replace")
        else:
            for b in raw:
                if self.tounicode and b in self.tounicode:
                    out.append(self.tounicode[b])
                elif self.macroman:
                    out.append(bytes([b]).decode("mac-roman", "replace"))
                else:
                    out.append(bytes([b]).decode("cp1252", "replace"))
            return "".join(out)

    def can_encode(self, text):
        """是否能用该字体安全编码 text（且解码可还原）。"""
        if self.two_byte and not self.predefined_unicode and not self.tounicode:
            return False
        try:
            return self.decode(self.encode(text)) == text
        except Exception:
            return False

    def encode(self, text):
        if self.two_byte:
            if self.tounicode:
                if self._rev is None:
                    self._rev = {v: k for k, v in self.tounicode.items()}
                out = bytearray()
                for ch in text:
                    if ch not in self._rev:
                        raise ValueError(f"char {ch!r} not encodable with font {self.name}")
                    out += self._rev[ch].to_bytes(2, "big")
                return bytes(out)
            return text.encode("utf-16-be")
        return text.encode("cp1252", "replace")


# ---------------------------------------------------------------------------
# 内容流分词/解析
# ---------------------------------------------------------------------------

class _Tok:
    __slots__ = ("kind", "value", "start", "end", "items")

    def __init__(self, kind, value, start, end, items=None):
        self.kind = kind
        self.value = value
        self.start = start
        self.end = end
        self.items = items


def _scan_literal(data, i):
    """解析 data[i]=='(' 起的 PDF 字面量字符串，返回 (内容字节, 结束下标)。

    外层定界括号不进入内容；嵌套的括号作为内容保留。
    """
    depth = 1
    j = i + 1
    n = len(data)
    out = bytearray()
    while j < n:
        c = data[j]
        if c == 0x5C:  # backslash
            if j + 1 < n:
                nxt = data[j + 1]
                simple = {0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12,
                          0x28: 0x28, 0x29: 0x29, 0x5C: 0x5C}
                if nxt in simple:
                    out.append(simple[nxt])
                    j += 2
                    continue
                if 0x30 <= nxt <= 0x37:
                    oct_digits = []
                    k = j + 1
                    while k < n and len(oct_digits) < 3 and 0x30 <= data[k] <= 0x37:
                        oct_digits.append(data[k])
                        k += 1
                    out.append(int(bytes(oct_digits), 8) & 0xFF)
                    j = k
                    continue
                if nxt in (0x0A, 0x0D):
                    j += 2
                    if nxt == 0x0D and j < n and data[j] == 0x0A:
                        j += 1
                    continue
                out.append(nxt)
                j += 2
                continue
            j += 1
            continue
        if c == 0x28:
            depth += 1
            out.append(c)
            j += 1
            continue
        if c == 0x29:
            depth -= 1
            if depth == 0:
                return bytes(out), j + 1
            out.append(c)
            j += 1
            continue
        out.append(c)
        j += 1
    return bytes(out), j


def _scan_hex(data, i):
    j = data.find(b">", i)
    n = len(data)
    if j < 0:
        j = n
    raw = re.sub(rb"[^0-9A-Fa-f]", b"", data[i + 1:j])
    if len(raw) % 2:
        raw += b"0"
    return bytes.fromhex(raw.decode()), (j + 1 if j < n else n)


def _skip_balanced(data, i, opener, closer):
    depth = 0
    j = i
    n = len(data)
    while j < n:
        if data[j:j + len(opener)] == opener:
            depth += 1
            j += len(opener)
            continue
        if data[j:j + len(closer)] == closer:
            depth -= 1
            j += len(closer)
            if depth == 0:
                return j
            continue
        j += 1
    return n


def _next_token(data, i):
    n = len(data)
    while i < n and data[i] in _WS:
        i += 1
    if i >= n:
        return None, i
    c = data[i]
    if c == 0x25:  # %
        j = data.find(b"\n", i)
        return _next_token(data, n if j < 0 else j + 1)
    if c == 0x2F:  # /
        j = i + 1
        while j < n and data[j] not in _WS and data[j] not in _DELIM:
            j += 1
        return _Tok("name", data[i + 1:j], i, j), j
    if c == 0x28:  # (
        raw, j = _scan_literal(data, i)
        return _Tok("str", raw, i, j), j
    if c == 0x3C:  # <
        if data[i + 1:i + 2] == b"<":
            j = _skip_balanced(data, i, b"<<", b">>")
            return _Tok("dict", data[i:j], i, j), j
        raw, j = _scan_hex(data, i)
        return _Tok("str", raw, i, j), j
    if c == 0x5B:  # [
        items = []
        j = i + 1
        while True:
            t, j = _next_token(data, j)
            if t is None:
                break
            if t.kind == "op" and t.value == b"]":
                return _Tok("array", None, i, j, items), j
            items.append(t)
        return _Tok("array", None, i, i + 1, items), i + 1
    if c in (0x2B, 0x2D, 0x2E) or 0x30 <= c <= 0x39:  # number
        j = i
        while j < n and (data[j] in b"+-.0123456789"):
            j += 1
        return _Tok("num", data[i:j], i, j), j
    if c == 0x5D:  # ]
        return _Tok("op", b"]", i, i + 1), i + 1
    # operator / keyword
    j = i
    while j < n and data[j] not in _WS and data[j] not in _DELIM:
        j += 1
    if j == i:
        # 未识别的定界符（如孤立的 ')' '>' 等）：必须推进，绝不返回零长 token 造成死循环
        return _Tok("op", data[i:i + 1], i, i + 1), i + 1
    return _Tok("op", data[i:j], i, j), j


def _skip_inline_image(data, i):
    """跳过内联图像二进制（BI ... ID <binary> EI），返回 EI 之后的下标。

    内容流里内联图像的数据是任意二进制，绝不能被当作算子/字符串解析。
    """
    m = re.search(rb"\sEI(?=[\s\[\]<>()/%]|\Z)", data[i:])
    if not m:
        return len(data)
    return i + m.end()


def _tokenize(data):
    toks = []
    i = 0
    n = len(data)
    while i < n:
        t, i = _next_token(data, i)
        if t is None:
            break
        toks.append(t)
        if t.kind == "op" and t.value == b"ID":
            i = _skip_inline_image(data, i)
    return toks


def _decode_op_text(font, tok):
    """把文本显示算子的操作数还原为字符串（TJ 忽略数字间距项）。"""
    if tok.kind == "str":
        return font.decode(tok.value)
    if tok.kind == "array":
        return "".join(font.decode(it.value) for it in tok.items if it.kind == "str")
    return ""


def _decode_with_codes(font, raw):
    """把字符串字节解码为 [(字符, 原始字形码)]。

    保留原始字形码：这样切除命中后，保留文本可直接用**原字形码**重排，
    既不做可能失真的重编码（如 Identity-H CID 的 ToUnicode 多对一），
    也能用命中字形码的**真实字宽**做字距补偿。
    """
    out = []
    if font.two_byte:
        for i in range(0, len(raw) - 1, 2):
            code = int.from_bytes(raw[i:i + 2], "big")
            if font.tounicode:
                ch = font.tounicode.get(code, "\ufffd")
            else:
                ch = raw[i:i + 2].decode("utf-16-be", "replace")
            out.append((ch, code))
    else:
        for b in raw:
            if font.tounicode and b in font.tounicode:
                ch = font.tounicode[b]
            elif font.macroman:
                ch = bytes([b]).decode("mac-roman", "replace")
            else:
                ch = bytes([b]).decode("cp1252", "replace")
            out.append((ch, b))
    return out


def _split_occurrences(pairs, find):
    """把 [(字符, 码)] 按 find 拆成 keep/drop 段（与定位器一致的非重叠命中）。"""
    text = "".join(ch for ch, _ in pairs)
    segments = []
    idx = 0
    while True:
        pos = text.find(find, idx)
        if pos < 0:
            segments.append(("keep", pairs[idx:]))
            break
        segments.append(("keep", pairs[idx:pos]))
        segments.append(("drop", pairs[pos:pos + len(find)]))
        idx = pos + len(find)
    return segments


# ---------------------------------------------------------------------------
# 内容流改写：删除命中字形码，返回 (new_bytes, removals)
# ---------------------------------------------------------------------------

def _edit_content(content, fonts, find):
    """在内容流中删除 `find` 的字形码。

    返回 (新内容字节, 命中列表)；命中列表每个命中元素占一项，因此同一
    文本算子内的多处命中会被完整计数（与定位器一致）。只有能安全手术
    （整个文本显示算子被删除，或在算子内以字距补偿精确切除）才改写；
    若遇到无法可靠处理的内容则抛 _UnsupportedEdit，由上层显式失败。
    """
    if not find:
        return content, []
    toks = _tokenize(content)
    edits = []  # (start, end, replacement_bytes)
    hits = []
    operands = []
    cur_font = None
    cur_size = 0.0
    for t in toks:
        if t.kind != "op":
            operands.append(t)
            continue
        op = t.value
        if op == b"Tf":
            if len(operands) >= 2 and operands[-2].kind == "name":
                cur_font = fonts.get(operands[-2].value.decode("latin-1"))
                try:
                    cur_size = float(operands[-1].value)
                except (TypeError, ValueError):
                    cur_size = 0.0
            operands = []
            continue
        show_tok = None
        if op in (b"Tj", b"'", b'"') and operands and operands[-1].kind == "str":
            show_tok = operands[-1]
        elif op == b"TJ" and operands and operands[-1].kind == "array":
            show_tok = operands[-1]
        if show_tok is None or cur_font is None:
            operands = []
            continue
        decoded = _decode_op_text(cur_font, show_tok)
        occ = decoded.count(find)
        if occ:
            span_start = operands[0].start
            span_end = t.end
            replacement = _rewrite_show_token(cur_font, show_tok, find)
            if replacement is None:
                edits.append((span_start, span_end, b""))
            else:
                edits.append((show_tok.start, span_end, replacement))
            hits.extend([{"font": cur_font, "size": cur_size}] * occ)
        operands = []
    # 应用（从后往前）
    out = bytearray(content)
    for start, end, repl in sorted(edits, key=lambda e: e[0], reverse=True):
        out[start:end] = repl
    return bytes(out), hits


class _UnsupportedEdit(Exception):
    """无法可靠手术的内容结构（显式失败，不猜改）。"""


def _rewrite_show_token(font, show_tok, find):
    """改写一个文本显示算子，切除 `find`。

    返回新的算子字节（含 TJ 包装）或 None（整个算子已无保留文本，应删除）。
    目标跨同一 TJ 数组的多个字符串元素时无法可靠保留字距，抛 _UnsupportedEdit。
    保留文本用原始字形码重排，不重编码，故不受 ToUnicode 多对一影响。
    """
    if show_tok.kind == "str":
        segments = _split_occurrences(_decode_with_codes(font, show_tok.value), find)
        dumps, has_keep = _segments_to_dump(font, segments)
        if has_keep:
            return b"[" + dumps + b"]TJ"
        return None

    items = show_tok.items
    texts = []
    pos_map = []
    for idx, it in enumerate(items):
        if it.kind == "str":
            d = font.decode(it.value)
            texts.append(d)
            pos_map.extend([idx] * len(d))
    full = "".join(texts)
    start = 0
    while True:
        p = full.find(find, start)
        if p < 0:
            break
        if pos_map[p] != pos_map[p + len(find) - 1]:
            raise _UnsupportedEdit(
                "find string spans multiple strings inside one TJ array; "
                "refusing to guess at kerning")
        start = p + len(find)
    parts = []
    has_keep = False
    for it in items:
        if it.kind == "str":
            seg = _split_occurrences(_decode_with_codes(font, it.value), find)
            chunk, keep = _segments_to_dump(font, seg)
            has_keep = has_keep or keep
            parts.append(chunk)
        elif it.kind == "num":
            parts.append(it.value)
        else:
            parts.append(b"")
    if has_keep:
        body = b" ".join(p for p in parts if p)
        return b"[" + body + b"]TJ"
    return None


def _codes_to_hex(font, seg):
    if font.two_byte:
        return b"".join(code.to_bytes(2, "big") for _, code in seg).hex().encode()
    return bytes(code & 0xFF for _, code in seg).hex().encode()


def _segments_to_dump(font, segments):
    """把 keep/drop 字形码段拼成 TJ 片段；drop 段插入字距补偿号（千分之一 em）。

    返回 (dumps, has_keep)；has_keep=False 表示该算子不再含任何保留文本。
    字距 = -Σ(被删字形真实字宽/1000em)，用命中字形码的真实宽度，位置不漂移。
    """
    chunks = []
    has_keep = False
    for kind, seg in segments:
        if kind == "keep":
            if seg:
                chunks.append(b"<" + _codes_to_hex(font, seg) + b">")
                has_keep = True
        else:
            if seg:
                total = sum(font.width_for_code(code, ch) for ch, code in seg)
                kern = -int(round(total))
                if kern:
                    chunks.append(b" " + str(kern).encode())
    return b" ".join(chunks), has_keep


# ---------------------------------------------------------------------------
# 字体资源选择 / 新增
# ---------------------------------------------------------------------------

def _iter_fonts(resources):
    """返回 {裸资源名: font 对象}；找不到返回 {}。"""
    fonts = resources.get("/Font") if resources else None
    if fonts is None:
        return {}
    if hasattr(fonts, "get_object"):
        fonts = fonts.get_object()
    if not isinstance(fonts, dict):
        return {}
    out = {}
    for key, ref in fonts.items():
        bare = key[1:] if str(key).startswith("/") else str(key)
        out[bare] = ref
    return out


def _choose_font(resources, want_cjk):
    """返回 (资源名, codec)；找不到返回 (None, None)。"""
    candidates = {}
    for name, ref in _iter_fonts(resources).items():
        try:
            fdict = ref.get_object() if hasattr(ref, "get_object") else ref
            codec = _FontCodec(name, fdict)
        except Exception:
            continue
        candidates[name] = codec
    preferred = []
    for name, codec in candidates.items():
        if want_cjk:
            if codec.is_cid and codec.predefined_unicode:
                preferred.append((name, codec))
        else:
            if not codec.is_cid and codec.can_encode("Test"):
                preferred.append((name, codec))
    if preferred:
        for name, codec in preferred:
            if "china" in name or "hei" in name.lower() or "song" in name.lower():
                return name, codec
        return preferred[0]
    return None, None


def _codec_for_new_font(want_cjk):
    if want_cjk:
        return "pdflatedit-cjk", _FontCodec("pdflatedit-cjk", DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Heiti"),
            NameObject("/Encoding"): NameObject("/UniGB-UTF16-H"),
        }))
    return "pdflatedit-latin", _FontCodec("pdflatedit-latin", DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
        NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
    }))


def _add_font_resource(resources, want_cjk):
    name, _ = _codec_for_new_font(want_cjk)
    if want_cjk:
        cid = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/CIDFontType0"),
            NameObject("/BaseFont"): NameObject("/Heiti"),
            NameObject("/CIDSystemInfo"): DictionaryObject({
                NameObject("/Registry"): pypdf.generic.create_string_object("Adobe"),
                NameObject("/Ordering"): pypdf.generic.create_string_object("GB1"),
                NameObject("/Supplement"): NumberObject(5),
            }),
            NameObject("/FontDescriptor"): DictionaryObject({
                NameObject("/Type"): NameObject("/FontDescriptor"),
                NameObject("/FontName"): NameObject("/Heiti"),
                NameObject("/Flags"): NumberObject(4),
                NameObject("/FontBBox"): pypdf.generic.ArrayObject([
                    NumberObject(-200), NumberObject(-200),
                    NumberObject(1200), NumberObject(1200)]),
                NameObject("/ItalicAngle"): NumberObject(0),
                NameObject("/Ascent"): NumberObject(1000),
                NameObject("/Descent"): NumberObject(-200),
                NameObject("/StemV"): NumberObject(80),
            }),
            NameObject("/DW"): NumberObject(1000),
        })
        fontobj = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type0"),
            NameObject("/BaseFont"): NameObject("/Heiti"),
            NameObject("/Encoding"): NameObject("/UniGB-UTF16-H"),
            NameObject("/DescendantFonts"): pypdf.generic.ArrayObject([cid]),
        })
    else:
        fontobj = DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
            NameObject("/Encoding"): NameObject("/WinAnsiEncoding"),
        })
    fonts = resources.get("/Font")
    if fonts is None:
        fonts = DictionaryObject()
        resources[NameObject("/Font")] = fonts
    elif hasattr(fonts, "get_object"):
        fonts = fonts.get_object()
    fonts[NameObject("/" + name)] = fontobj
    return name


# ---------------------------------------------------------------------------
# 版面规划（与原实现等价的越页/覆盖保护）
# ---------------------------------------------------------------------------

def _rect_intersects(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


def _plan_insert(match_rect, replace, page_wh, words, ignore_rects, codec):
    """返回 (ok, size, box, overlapped, reason)。坐标均为 PDF 坐标(原点左下)。

    替换文本宽度按**插入字体真实字宽**测量（codec.measure），避免用错字宽
    导致越页/覆盖判断失真。
    """
    pr_w, pr_h = page_wh
    max_right = pr_w - PAGE_MARGIN
    max_bottom = pr_h - PAGE_MARGIN
    height = match_rect[3] - match_rect[1]
    prefer = max(6.0, min(24.0, height * 0.75))
    size = prefer
    last_within = None
    while size >= MIN_FONTSIZE - 1e-9:
        w = codec.measure(replace, size)
        box = (match_rect[0] - 0.5, match_rect[1] - 1.0,
               match_rect[0] - 0.5 + max(w, match_rect[2] - match_rect[0]) + 2.0,
               match_rect[3] + 2.0)
        within = (box[0] >= 0 and box[2] <= max_right
                  and box[1] >= 0 and box[3] <= max_bottom)
        if within:
            last_within = (size, box)
            overlapped = False
            for wtxt, wrect in words:
                if any(_rect_intersects(wrect, r) for r in ignore_rects):
                    continue
                if _rect_intersects(box, wrect):
                    overlapped = True
                    break
            if not overlapped:
                return True, size, box, False, ""
        size -= 0.5
    if last_within is not None:
        return False, last_within[0], last_within[1], True, \
            "replacement would overlap neighbouring content"
    return False, MIN_FONTSIZE, None, False, \
        "replacement text cannot fit on the page without overflowing"


def _content_block(font_name, size, matrix, text, codec):
    hexstr = codec.encode(text).hex()
    a, b, c, d, e, f = matrix
    return ("\nq\nBT\n{} {} {} {} {} {} Tm\n/{} {} Tf\n<{}> Tj\nET\nQ\n"
            .format(a, b, c, d, e, f, font_name, size, hexstr)).encode("latin-1")


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------

def _apply_page_edit(writer, pno, new_content, font_add):
    page = writer.pages[pno]
    if font_add is not None:
        resources = page.get("/Resources")
        if resources is None:
            resources = DictionaryObject()
        else:
            resources = resources.get_object()
        page[NameObject("/Resources")] = resources
        _add_font_resource(resources, font_add)
    stream = DecodedStreamObject()
    stream.set_data(new_content)
    page.replace_contents(stream)


def _write_outputs(reader, pno, new_content, font_add, purge):
    """返回 (bytes, mode)。font_add = bool(want_cjk) 或 None。"""
    if purge:
        writer = pypdf.PdfWriter(clone_from=reader)
        _apply_page_edit(writer, pno, new_content, font_add)
        buf = io.BytesIO()
        writer.write(buf)
        return buf.getvalue(), "purge"
    writer = pypdf.PdfWriter(reader, incremental=True)
    _apply_page_edit(writer, pno, new_content, font_add)
    buf = io.BytesIO()
    writer.write(buf)
    data = buf.getvalue()
    original = reader.stream
    original.seek(0)
    prefix = original.read(-1)
    if data.startswith(prefix):
        return data, "incremental"
    # 增量未成立 → 退回整文件重写，如实报告
    writer2 = pypdf.PdfWriter(clone_from=reader)
    _apply_page_edit(writer2, pno, new_content, font_add)
    buf2 = io.BytesIO()
    writer2.write(buf2)
    return buf2.getvalue(), "full_fallback"


# ---------------------------------------------------------------------------
# 文本比对
# ---------------------------------------------------------------------------

def _page_texts(path):
    texts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text() or "")
    return texts


def _strip_ws(text):
    return re.sub(r"\s+", "", text)


def _write_report(path, obj):
    if path:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="PDF局部文字编辑(1基页码; 默认替换该页全部命中)")
    ap.add_argument("--input", required=True)
    ap.add_argument("--page", type=int, required=True, help="1基页码")
    ap.add_argument("--find", required=True)
    ap.add_argument("--replace", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--report")
    ap.add_argument("--purge", action="store_true",
                    help="整文件重写以清除旧对象版本; 仍非取证级擦除")
    args = ap.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: input not found: {args.input}", file=sys.stderr)
        return 2
    if os.path.realpath(args.input) == os.path.realpath(args.output):
        print("ERROR: --input and --output must differ (would corrupt source)", file=sys.stderr)
        return 2
    outdir = os.path.dirname(os.path.abspath(args.output))
    if not os.path.isdir(outdir):
        print(f"ERROR: output directory does not exist: {outdir}", file=sys.stderr)
        return 2

    try:
        reader = pypdf.PdfReader(args.input)
    except Exception as e:
        print(f"ERROR: cannot open PDF: {e}", file=sys.stderr)
        return 2
    if reader.is_encrypted:
        print("ERROR: encrypted PDF", file=sys.stderr)
        return 2
    try:
        page_count = len(reader.pages)
    except Exception as e:
        print(f"ERROR: cannot read PDF pages: {e}", file=sys.stderr)
        return 2
    if args.page < 1 or args.page > page_count:
        print(f"ERROR: page {args.page} out of range (1..{page_count})", file=sys.stderr)
        return 2
    pno = args.page - 1

    try:
        before_texts = _page_texts(args.input)
    except Exception as e:
        print(f"ERROR: cannot read PDF text: {e}", file=sys.stderr)
        return 2

    # 用 pdfplumber 定位命中（等价 search_for 语义：连续文本）
    try:
        with pdfplumber.open(args.input) as pdf:
            page = pdf.pages[pno]
            page_wh = (page.width, page.height)
            page_height = page.height
            matches = page.search(args.find, regex=False)
            words = []
            for w in page.extract_words():
                words.append((w["text"], (w["x0"], page_height - w["bottom"],
                                          w["x1"], page_height - w["top"])))
    except Exception as e:
        print(f"ERROR: cannot search PDF page: {e}", file=sys.stderr)
        return 2

    if not matches:
        result = {"status": "not_found", "page": args.page, "find": args.find,
                  "output": args.output, "occurrences_found": 0}
        _write_report(args.report, result)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    match_rects = []
    match_chars = []
    for m in matches:
        chars = m["chars"]
        x0 = min(c["x0"] for c in chars)
        x1 = max(c["x1"] for c in chars)
        y0 = min(page_height - c["bottom"] for c in chars)
        y1 = max(page_height - c["top"] for c in chars)
        match_rects.append((x0, y0, x1, y1))
        match_chars.append(chars[0])

    # 页面字体资源
    try:
        page_obj = reader.pages[pno]
        resources = page_obj.get("/Resources")
        if resources is not None:
            resources = resources.get_object()
    except Exception:
        resources = None
    fonts = {}
    for name, ref in _iter_fonts(resources).items():
        try:
            fdict = ref.get_object() if hasattr(ref, "get_object") else ref
            fonts[name] = _FontCodec(name, fdict)
        except Exception:
            continue

    # 读取目标页内容流并做算子级删除
    try:
        content = page_obj.get_contents().get_data()
    except Exception as e:
        print(f"ERROR: cannot read page content stream: {e}", file=sys.stderr)
        return 2
    try:
        new_content, hits = _edit_content(content, fonts, args.find)
    except Exception as e:
        result = {"status": "error", "page": args.page, "find": args.find,
                  "output": args.output, "reason": f"unsupported content structure: {e}",
                  "occurrences_found": len(matches)}
        _write_report(args.report, result)
        print(f"ERROR: unsupported content structure: {e}", file=sys.stderr)
        return 2

    if len(hits) != len(matches):
        result = {"status": "error", "page": args.page, "find": args.find,
                  "output": args.output,
                  "reason": "match alignment failed between locator and content stream",
                  "occurrences_found": len(matches)}
        _write_report(args.report, result)
        print("ERROR: match alignment failed between locator and content stream", file=sys.stderr)
        return 2

    # 插入字体与字号规划
    want_cjk = _has_cjk(args.replace)
    font_name, codec = _choose_font(resources, want_cjk)
    font_add = None
    if font_name is None or not codec.can_encode(args.replace):
        font_name, codec = _codec_for_new_font(want_cjk)
        font_add = want_cjk

    plans = []
    for rect in match_rects:
        ok, size, box, overlapped, reason = _plan_insert(
            rect, args.replace, page_wh, words, match_rects, codec)
        if not ok:
            result = {"status": "error", "page": args.page, "find": args.find,
                      "output": args.output, "reason": reason,
                      "occurrences_found": len(matches)}
            _write_report(args.report, result)
            print(f"ERROR: {reason}", file=sys.stderr)
            return 2
        plans.append((size, box))

    # 追加插入块
    blocks = bytearray(new_content)
    edits = []
    for i, (rect, mchar) in enumerate(zip(match_rects, match_chars)):
        size, box = plans[i]
        matrix = tuple(mchar["matrix"])
        blocks += _content_block(font_name, size, matrix, args.replace, codec)
        edits.append({"page": args.page, "before": args.find, "after": args.replace,
                      "rect": [round(v, 1) for v in box], "fontsize": round(size, 1)})

    # 复制到临时文件落盘，成功后再原子替换
    fd, tmp = tempfile.mkstemp(prefix=".pdfedit-", suffix=".pdf", dir=outdir)
    os.close(fd)
    try:
        data, mode = _write_outputs(reader, pno, bytes(blocks), font_add, args.purge)
        with open(tmp, "wb") as f:
            f.write(data)

        # 独立回读校验
        try:
            after_texts = _page_texts(tmp)
        except Exception as e:
            print(f"ERROR: output verification failed to read: {e}", file=sys.stderr)
            return 2
        unchanged = all(after_texts[i] == before_texts[i]
                        for i in range(min(len(before_texts), len(after_texts)))
                        if i != pno)
        # 除“新文出现/旧文消失”外，还要核查**未匹配正文**未被损坏：
        # 去掉命中与替换后，目标页正文应逐字符一致（忽略空白），
        # 这样错误的解码/手术（如多出定界括号）不会以退出 0 蒙混过关。
        # 注意 before 侧也要去掉 replace，否则页面**原本就有**的相同新文本
        # （独立于命中）会被误当成“正文丢失”而误报。
        body_preserved = (
            _strip_ws(before_texts[pno]
                      .replace(args.find, "")
                      .replace(args.replace, ""))
            == _strip_ws(after_texts[pno].replace(args.replace, "")))
        report = {
            "input": args.input,
            "output": args.output,
            "page": args.page,
            "page_base": "1-based (documented; no trial-and-error)",
            "edits": edits,
            "occurrences_found": len(plans),
            "occurrences_replaced": len(plans),
            "font_note": "插入使用文档字体资源(helv/china-s 等 CID)，与原文档嵌入字体可能不一致",
            "other_pages_unchanged": unchanged,
            "target_page_contains_new_text": args.replace in after_texts[pno],
            "target_page_contains_old_text": args.find in after_texts[pno],
            "target_page_body_preserved": body_preserved,
            "replace_all_occurrences": True,
            "mode": mode,
            "irreversible": mode in ("purge", "full_fallback"),
            "security_note": ("增量保存会保留旧对象版本，不构成不可恢复的敏感原文删除；"
                              "不可逆脱敏请使用 doc-sensitive-redact"),
        }
        ok = (unchanged and report["target_page_contains_new_text"]
              and not report["target_page_contains_old_text"]
              and body_preserved)
        _write_report(args.report, report)
        print(json.dumps(report, ensure_ascii=False))
        if not ok:
            return 1
        os.replace(tmp, args.output)
        tmp = None
        return 0
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


if __name__ == "__main__":
    sys.exit(main())
