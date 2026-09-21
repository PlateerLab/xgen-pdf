"""Content stream tokenising, state tracking and glyph-level rewriting.

A content stream is parsed into a list of operations ``(operands, operator)``
plus inline images.  Serialising the list back yields an equivalent stream.
Rewriting is only ever applied to the text-showing operators (and painting
operators) that a redaction touches; every other operation is re-emitted
unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .geometry import Matrix, Point, Rect
from .pdfsyntax import Keyword, Lexer, Name, parse_object, serialize

__all__ = ["Op", "parse_content", "serialize_content", "FontCodec", "codec_for_font"]

_TEXT_SHOW = {"Tj", "TJ", "'", '"'}
_PAINT_OPS = {"S", "s", "f", "F", "f*", "B", "B*", "b", "b*", "n"}


@dataclass(slots=True)
class Op:
    operands: list
    operator: str
    inline_image: bytes | None = None  # raw "BI ... EI" bytes


def parse_content(data: bytes) -> list[Op]:
    lexer = Lexer(data)
    ops: list[Op] = []
    operands: list = []
    while True:
        try:
            obj = parse_object(lexer, allow_refs=False)
        except EOFError:
            break
        if isinstance(obj, Keyword):
            op = str(obj)
            if op == "BI":
                start = lexer.pos - 2
                end = _inline_image_end(data, lexer.pos)
                ops.append(Op([], "BI", data[start:end]))
                lexer.pos = end
                operands = []
                continue
            ops.append(Op(operands, op))
            operands = []
        else:
            operands.append(obj)
    if operands:
        ops.append(Op(operands, ""))
    return ops


def _inline_image_end(data: bytes, pos: int) -> int:
    """Position just past the ``EI`` closing an inline image whose ``BI`` was
    consumed."""
    m = re.compile(rb"\bID[ \t\r\n\x0c\x00]").search(data, pos)
    if not m:
        return len(data)
    scan = m.end()
    while True:
        e = re.compile(rb"(?:^|[ \t\r\n\x0c\x00])EI(?=[ \t\r\n\x0c\x00/\[<(%]|$)").search(data, scan)
        if not e:
            return len(data)
        return e.end()


def serialize_content(ops: list[Op]) -> bytes:
    parts = []
    for op in ops:
        if op.inline_image is not None:
            parts.append(op.inline_image)
            continue
        if op.operands:
            parts.append(b" ".join(serialize(o) for o in op.operands) + b" " + op.operator.encode("latin-1"))
        else:
            parts.append(op.operator.encode("latin-1"))
    return b"\n".join(parts) + b"\n"


# --------------------------------------------------------------------------
# Font code decoding
# --------------------------------------------------------------------------

# Predefined CJK CMaps with mixed one/two byte code spaces (PDF 32000-1 §9.7.5.2).
_TWO_BYTE_ONLY = ("Identity", "UCS2", "UTF16", "UniJIS", "UniGB", "UniCNS", "UniKS", "UniHojo", "Adobe")
_MIXED_CMAPS = {
    # name prefix -> list of (low, high) single-byte ranges; everything else is two bytes
    "KSCms-UHC": [(0x00, 0x80)],
    "KSC-EUC": [(0x00, 0x80)],
    "KSCpc-EUC": [(0x00, 0x80)],
    "KSCms-UHC-HW": [(0x00, 0x80)],
    "90ms-RKSJ": [(0x00, 0x80), (0xA0, 0xDF)],
    "90msp-RKSJ": [(0x00, 0x80), (0xA0, 0xDF)],
    "90pv-RKSJ": [(0x00, 0x80), (0xA0, 0xDF)],
    "83pv-RKSJ": [(0x00, 0x80), (0xA0, 0xDF)],
    "Add-RKSJ": [(0x00, 0x80), (0xA0, 0xDF)],
    "Ext-RKSJ": [(0x00, 0x80), (0xA0, 0xDF)],
    "EUC": [(0x00, 0x80)],
    "H": None,  # JIS 2-byte only
    "V": None,
    "GBK-EUC": [(0x00, 0x80)],
    "GBKp-EUC": [(0x00, 0x80)],
    "GB-EUC": [(0x00, 0x80)],
    "GBpc-EUC": [(0x00, 0x80)],
    "GBK2K": [(0x00, 0x80)],
    "B5pc": [(0x00, 0x80)],
    "ETen-B5": [(0x00, 0x80)],
    "ETenms-B5": [(0x00, 0x80)],
    "HKscs-B5": [(0x00, 0x80)],
    "CNS-EUC": [(0x00, 0x80)],
}


@dataclass(slots=True)
class FontCodec:
    """Splits a PDF string into character codes for one font."""

    ranges: list[tuple[int, int, int]] = field(default_factory=list)  # (nbytes, low, high)
    simple: bool = True
    vertical: bool = False
    known: bool = True

    def codes(self, text: bytes) -> list[bytes]:
        if self.simple:
            return [text[i : i + 1] for i in range(len(text))]
        out = []
        i = 0
        n = len(text)
        while i < n:
            taken = None
            for nbytes, low, high in self.ranges:
                if i + nbytes <= n:
                    value = int.from_bytes(text[i : i + nbytes], "big")
                    if low <= value <= high:
                        taken = nbytes
                        break
            if taken is None:
                # Partial match rule: use the shortest codespace length.
                taken = min((r[0] for r in self.ranges), default=1)
                taken = min(taken, n - i)
            out.append(text[i : i + taken])
            i += taken
        return out


def codec_for_font(font: dict, pdf) -> FontCodec:
    """Build a codec from a font dictionary (``pdf`` resolves references)."""
    resolve = pdf.get
    subtype = str(resolve(font.get("Subtype", "")))
    if subtype != "Type0":
        return FontCodec(simple=True)
    encoding = resolve(font.get("Encoding"))
    vertical = False
    if hasattr(encoding, "dict"):
        # embedded CMap stream
        info = encoding.dict
        wmode = resolve(info.get("WMode", 0))
        vertical = int(wmode or 0) == 1
        try:
            data = pdf.stream_data(encoding)
        except Exception:
            return FontCodec(simple=False, ranges=[(2, 0, 0xFFFF)], vertical=vertical, known=False)
        ranges = _parse_codespace(data)
        if not ranges:
            ranges = [(2, 0, 0xFFFF)]
        return FontCodec(simple=False, ranges=ranges, vertical=vertical)
    name = str(encoding) if encoding is not None else "Identity-H"
    vertical = name.endswith("-V") or name == "V"
    base = name[:-2] if name.endswith(("-H", "-V")) else name
    if any(base.startswith(prefix) for prefix in _TWO_BYTE_ONLY):
        return FontCodec(simple=False, ranges=[(2, 0, 0xFFFF)], vertical=vertical)
    single = _MIXED_CMAPS.get(base, "missing")
    if single == "missing":
        return FontCodec(simple=False, ranges=[(2, 0, 0xFFFF)], vertical=vertical, known=False)
    ranges: list[tuple[int, int, int]] = []
    for low, high in single or []:
        ranges.append((1, low, high))
    ranges.append((2, 0, 0xFFFF))
    return FontCodec(simple=False, ranges=ranges, vertical=vertical)


def _parse_codespace(data: bytes) -> list[tuple[int, int, int]]:
    ranges: list[tuple[int, int, int]] = []
    for m in re.finditer(rb"begincodespacerange(.*?)endcodespacerange", data, re.S):
        body = m.group(1)
        for lo, hi in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", body):
            nbytes = max(1, len(lo) // 2)
            ranges.append((nbytes, int(lo, 16), int(hi, 16)))
    ranges.sort(key=lambda r: r[0])
    return ranges


# --------------------------------------------------------------------------
# Graphics / text state walker
# --------------------------------------------------------------------------


@dataclass(slots=True)
class TextState:
    font: str = ""
    size: float = 0.0
    char_spacing: float = 0.0
    word_spacing: float = 0.0
    hscale: float = 1.0
    leading: float = 0.0
    rise: float = 0.0
    tm: Matrix = field(default_factory=Matrix)
    tlm: Matrix = field(default_factory=Matrix)


@dataclass(slots=True)
class GState:
    ctm: Matrix = field(default_factory=Matrix)


@dataclass(slots=True)
class ShowOp:
    """A text-showing operation seen by the walker."""

    stream_id: object
    index: int  # index in the stream's op list
    op: Op
    font: str
    size: float
    hscale: float
    matrix: Matrix  # Tm x CTM x display (text space -> display, without size)
    strings: list[bytes]
    rise: float = 0.0
    resources: dict = field(default_factory=dict)


@dataclass(slots=True)
class PaintOp:
    stream_id: object
    index: int
    op: Op
    bbox: Rect  # display space
    clip: bool


@dataclass(slots=True)
class ImageOp:
    stream_id: object
    index: int
    op: Op
    name: str
    bbox: Rect
    matrix: Matrix = field(default_factory=Matrix)  # unit square -> display
    ref: object = None  # XObject reference


class ContentWalker:
    """Walks a page's content (following form XObjects) and reports text
    showing, path painting and image drawing operations with their
    geometry in display space."""

    def __init__(self, pdf, display: Matrix, get_ops, get_resources):
        self.pdf = pdf
        self.display = display
        self.get_ops = get_ops  # stream_id -> list[Op]
        self.get_resources = get_resources  # stream_id -> resources dict
        self.shows: list[ShowOp] = []
        self.paints: list[PaintOp] = []
        self.images: list[ImageOp] = []
        self._depth = 0

    def walk(self, stream_id, ctm: Matrix, resources: dict) -> None:
        if self._depth > 12:
            return
        ops = self.get_ops(stream_id)
        gs = GState(ctm=Matrix(ctm))
        stack: list[GState] = []
        ts = TextState()
        path_points: list[Point] = []
        pending_clip = False
        fonts = self.pdf.get(resources.get("Font")) if resources else None
        xobjects = self.pdf.get(resources.get("XObject")) if resources else None
        for index, op in enumerate(ops):
            o = op.operator
            a = op.operands
            try:
                if o == "q":
                    stack.append(GState(ctm=Matrix(gs.ctm)))
                elif o == "Q":
                    if stack:
                        gs = stack.pop()
                elif o == "cm" and len(a) >= 6:
                    gs.ctm = Matrix(*[float(v) for v in a[:6]]) * gs.ctm
                elif o == "BT":
                    ts.tm = Matrix()
                    ts.tlm = Matrix()
                elif o == "Tf" and len(a) >= 2:
                    ts.font = str(a[0])
                    ts.size = float(a[1])
                elif o == "Tc" and a:
                    ts.char_spacing = float(a[0])
                elif o == "Tw" and a:
                    ts.word_spacing = float(a[0])
                elif o == "Tz" and a:
                    ts.hscale = float(a[0]) / 100.0
                elif o == "TL" and a:
                    ts.leading = float(a[0])
                elif o == "Ts" and a:
                    ts.rise = float(a[0])
                elif o == "Tm" and len(a) >= 6:
                    ts.tm = Matrix(*[float(v) for v in a[:6]])
                    ts.tlm = Matrix(ts.tm)
                elif o == "Td" and len(a) >= 2:
                    ts.tlm = Matrix(1, 0, 0, 1, float(a[0]), float(a[1])) * ts.tlm
                    ts.tm = Matrix(ts.tlm)
                elif o == "TD" and len(a) >= 2:
                    ts.leading = -float(a[1])
                    ts.tlm = Matrix(1, 0, 0, 1, float(a[0]), float(a[1])) * ts.tlm
                    ts.tm = Matrix(ts.tlm)
                elif o == "T*":
                    ts.tlm = Matrix(1, 0, 0, 1, 0, -ts.leading) * ts.tlm
                    ts.tm = Matrix(ts.tlm)
                elif o in _TEXT_SHOW:
                    if o == "'":
                        ts.tlm = Matrix(1, 0, 0, 1, 0, -ts.leading) * ts.tlm
                        ts.tm = Matrix(ts.tlm)
                    elif o == '"' and len(a) >= 3:
                        ts.word_spacing = float(a[0])
                        ts.char_spacing = float(a[1])
                        ts.tlm = Matrix(1, 0, 0, 1, 0, -ts.leading) * ts.tlm
                        ts.tm = Matrix(ts.tlm)
                    strings = _strings_of(op)
                    if strings:
                        self.shows.append(
                            ShowOp(
                                stream_id=stream_id,
                                index=index,
                                op=op,
                                font=ts.font,
                                size=ts.size,
                                hscale=ts.hscale,
                                matrix=ts.tm * gs.ctm * self.display,
                                strings=strings,
                                rise=ts.rise,
                                resources=resources or {},
                            )
                        )
                elif o == "m" and len(a) >= 2:
                    path_points.append(Point(float(a[0]), float(a[1])).transform(gs.ctm))
                elif o == "l" and len(a) >= 2:
                    path_points.append(Point(float(a[0]), float(a[1])).transform(gs.ctm))
                elif o == "c" and len(a) >= 6:
                    for i in range(0, 6, 2):
                        path_points.append(Point(float(a[i]), float(a[i + 1])).transform(gs.ctm))
                elif o in ("v", "y") and len(a) >= 4:
                    for i in range(0, 4, 2):
                        path_points.append(Point(float(a[i]), float(a[i + 1])).transform(gs.ctm))
                elif o == "re" and len(a) >= 4:
                    x, y, w, h = (float(v) for v in a[:4])
                    for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
                        path_points.append(Point(px, py).transform(gs.ctm))
                elif o in ("W", "W*"):
                    pending_clip = True
                elif o in _PAINT_OPS:
                    if path_points:
                        rect = Rect()
                        for p in path_points:
                            rect.include_point(Point(p).transform(self.display))
                        self.paints.append(PaintOp(stream_id, index, op, rect, pending_clip))
                    path_points = []
                    pending_clip = False
                elif o == "Do" and a and xobjects is not None:
                    name = str(a[0])
                    xref = xobjects.get(name) if isinstance(xobjects, dict) else None
                    xobj = self.pdf.get(xref)
                    if xobj is None or not hasattr(xobj, "dict"):
                        continue
                    subtype = str(self.pdf.get(xobj.dict.get("Subtype", "")))
                    if subtype == "Image":
                        unit = gs.ctm * self.display
                        bbox = Rect(0, 0, 1, 1).transform(unit).normalize()
                        self.images.append(ImageOp(stream_id, index, op, name, bbox, Matrix(unit), xref))
                    elif subtype == "Form":
                        matrix = self.pdf.get(xobj.dict.get("Matrix"))
                        form_matrix = Matrix(*[float(self.pdf.get(v)) for v in matrix]) if matrix else Matrix()
                        form_res = self.pdf.get(xobj.dict.get("Resources")) or resources
                        self._depth += 1
                        try:
                            self.walk(xref, form_matrix * gs.ctm, form_res if isinstance(form_res, dict) else {})
                        finally:
                            self._depth -= 1
            except (TypeError, ValueError):
                continue
        _ = fonts


def _strings_of(op: Op) -> list[bytes]:
    if op.operator == "TJ":
        arr = op.operands[-1] if op.operands else []
        if not isinstance(arr, list):
            return []
        return [item for item in arr if isinstance(item, (bytes, bytearray))]
    if op.operands and isinstance(op.operands[-1], (bytes, bytearray)):
        return [bytes(op.operands[-1])]
    return []


# --------------------------------------------------------------------------
# Rewriting text-showing operators
# --------------------------------------------------------------------------


def rewrite_show(op: Op, codec: FontCodec, removals: set[int], adjustments: dict[int, float]) -> Op:
    """Return a replacement for ``op`` with the glyphs in ``removals`` gone.

    ``removals`` holds global glyph indices over all strings of the operator;
    ``adjustments`` maps the index of the first glyph of each removed run to
    the TJ number (thousandths of text space) that keeps the following glyphs
    in place.
    """
    items: list = []  # TJ array being built
    if op.operator == "TJ":
        source = op.operands[-1] if op.operands and isinstance(op.operands[-1], list) else []
    else:
        source = [op.operands[-1]] if op.operands and isinstance(op.operands[-1], (bytes, bytearray)) else []
    glyph_no = 0
    for element in source:
        if not isinstance(element, (bytes, bytearray)):
            items.append(element)
            continue
        codes = codec.codes(bytes(element))
        buf = bytearray()
        for code in codes:
            if glyph_no in removals:
                if buf:
                    items.append(bytes(buf))
                    buf = bytearray()
                adj = adjustments.get(glyph_no)
                if adj:
                    items.append(round(adj, 3))
            else:
                buf += code
            glyph_no += 1
        if buf:
            items.append(bytes(buf))
    # merge consecutive numbers
    merged: list = []
    for item in items:
        if merged and isinstance(item, (int, float)) and isinstance(merged[-1], (int, float)):
            merged[-1] = round(merged[-1] + item, 3)
        else:
            merged.append(item)
    if op.operator in ("'", '"'):
        # Keep the line-advance semantics: emit T* (and spacing) then TJ.
        pre = []
        if op.operator == '"' and len(op.operands) >= 3:
            pre.append(Op([op.operands[0]], "Tw"))
            pre.append(Op([op.operands[1]], "Tc"))
        pre.append(Op([], "T*"))
        return _MultiOp(pre + [Op([merged], "TJ")])
    return Op([merged], "TJ")


class _MultiOp(Op):
    """Placeholder expanding to several ops when serialised."""

    def __init__(self, ops: list[Op]):
        super().__init__([], "")
        self.ops = ops


def expand_ops(ops: list[Op]) -> list[Op]:
    out: list[Op] = []
    for op in ops:
        if isinstance(op, _MultiOp):
            out.extend(op.ops)
        else:
            out.append(op)
    return out


_ = Name
