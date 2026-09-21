"""Object-level access to a classic (non-object-stream) PDF file.

pdfium always saves documents in this layout (one ``N G obj`` per object,
a classic ``xref`` table, streams with a direct ``/Length``).  We load that
output, edit the handful of objects we care about, and write a complete new
file: untouched objects are copied verbatim, edited objects are
re-serialised, and a fresh xref table and trailer are appended.  Nothing
from the old file survives unreferenced.
"""

from __future__ import annotations

import re
import zlib
from dataclasses import dataclass

from .pdfsyntax import Keyword, Lexer, Name, Ref, Stream, parse_object, serialize

__all__ = ["PdfFile", "PdfFileError"]

_OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj\b")
_STARTXREF_RE = re.compile(rb"startxref\s+(\d+)\s*%%EOF", re.S)


class PdfFileError(ValueError):
    pass


@dataclass(slots=True)
class _Entry:
    offset: int
    gen: int
    raw: bytes | None = None  # verbatim "N G obj ... endobj" when untouched
    value: object = None  # parsed object once needed / edited
    edited: bool = False


class PdfFile:
    def __init__(self, data: bytes):
        self.data = data
        self.entries: dict[int, _Entry] = {}
        self.trailer: dict = {}
        self._load()

    # -- loading -------------------------------------------------------------
    def _load(self) -> None:
        data = self.data
        if not data.startswith(b"%PDF"):
            raise PdfFileError("not a PDF file")
        offsets = self._offsets_from_xref()
        if not offsets:
            offsets = self._offsets_by_scan()
        for num, (offset, gen) in offsets.items():
            self.entries[num] = _Entry(offset=offset, gen=gen)
        if not self.trailer:
            raise PdfFileError("trailer not found")

    def _offsets_from_xref(self) -> dict[int, tuple[int, int]]:
        m = None
        for m in _STARTXREF_RE.finditer(self.data):
            pass
        if m is None:
            return {}
        start = int(m.group(1))
        offsets: dict[int, tuple[int, int]] = {}
        seen = set()
        while start and start not in seen and start < len(self.data):
            seen.add(start)
            lexer = Lexer(self.data, start)
            kind, value = lexer.next_token()
            if kind != "kw" or value != "xref":
                return {}  # cross-reference stream: fall back to scanning
            while True:
                lexer.skip_ws()
                if self.data.startswith(b"trailer", lexer.pos):
                    lexer.pos += len(b"trailer")
                    trailer = parse_object(lexer)
                    if not isinstance(trailer, dict):
                        return {}
                    if not self.trailer:
                        self.trailer = trailer
                    prev = trailer.get("Prev")
                    start = int(prev) if isinstance(prev, (int, float)) else 0
                    break
                k1, first = lexer.next_token()
                k2, count = lexer.next_token()
                if k1 != "num" or k2 != "num":
                    return {}
                lexer.skip_ws()
                for i in range(int(count)):
                    line = self.data[lexer.pos : lexer.pos + 20]
                    parts = line.split()
                    if len(parts) < 3:
                        return {}
                    off, gen, typ = int(parts[0]), int(parts[1]), parts[2]
                    num = int(first) + i
                    if typ == b"n" and num not in offsets:
                        offsets[num] = (off, gen)
                    lexer.pos += 20 if line[18:20] in (b"\r\n", b" \n", b" \r") else len(line.split(b"\n")[0]) + 1
                    lexer.skip_ws()
        # validate a sample of offsets; fall back to scanning if they are wrong
        for num, (off, _gen) in list(offsets.items())[:5]:
            m2 = _OBJ_RE.match(self.data, off)
            if not m2 or int(m2.group(1)) != num:
                return {}
        return offsets

    def _offsets_by_scan(self) -> dict[int, tuple[int, int]]:
        offsets: dict[int, tuple[int, int]] = {}
        pos = 0
        data = self.data
        while True:
            m = _OBJ_RE.search(data, pos)
            if not m:
                break
            num, gen = int(m.group(1)), int(m.group(2))
            offsets[num] = (m.start(), gen)
            end = self._object_end(m.start())
            pos = end
        tpos = data.rfind(b"trailer")
        if tpos >= 0:
            lexer = Lexer(data, tpos + len(b"trailer"))
            trailer = parse_object(lexer)
            if isinstance(trailer, dict):
                self.trailer = trailer
        return offsets

    def _object_end(self, offset: int) -> int:
        """Offset just past ``endobj`` for the object starting at ``offset``."""
        data = self.data
        m = _OBJ_RE.match(data, offset)
        if not m:
            raise PdfFileError(f"no object at offset {offset}")
        lexer = Lexer(data, m.end())
        value = parse_object(lexer)
        lexer.skip_ws()
        if data.startswith(b"stream", lexer.pos) and isinstance(value, dict):
            start = lexer.pos + len(b"stream")
            if data[start : start + 2] == b"\r\n":
                start += 2
            elif data[start : start + 1] in (b"\n", b"\r"):
                start += 1
            length = self._resolve_length(value.get("Length"))
            end = start + length if length is not None else -1
            if end < 0 or not data[end : end + 200].lstrip().startswith(b"endstream"):
                end = data.find(b"endstream", start)
                if end < 0:
                    raise PdfFileError("unterminated stream")
                # strip the EOL pdfium puts before endstream
                if data[end - 2 : end] == b"\r\n":
                    end -= 2
                elif data[end - 1 : end] in (b"\n", b"\r"):
                    end -= 1
            pos = data.find(b"endstream", end)
            lexer.pos = pos + len(b"endstream")
        lexer.skip_ws()
        if data.startswith(b"endobj", lexer.pos):
            return lexer.pos + len(b"endobj")
        return lexer.pos

    def _resolve_length(self, length) -> int | None:
        if isinstance(length, (int, float)):
            return int(length)
        if isinstance(length, Ref):
            try:
                value = self.get(length)
            except Exception:
                return None
            if isinstance(value, (int, float)):
                return int(value)
        return None

    # -- objects -------------------------------------------------------------
    def _parse_entry(self, num: int) -> _Entry:
        entry = self.entries.get(num)
        if entry is None:
            raise KeyError(num)
        if entry.value is None and entry.raw is None:
            end = self._object_end(entry.offset)
            entry.raw = self.data[entry.offset : end]
            m = _OBJ_RE.match(entry.raw)
            lexer = Lexer(entry.raw, m.end())
            value = parse_object(lexer)
            lexer.skip_ws()
            if entry.raw.startswith(b"stream", lexer.pos) and isinstance(value, dict):
                start = lexer.pos + len(b"stream")
                if entry.raw[start : start + 2] == b"\r\n":
                    start += 2
                elif entry.raw[start : start + 1] in (b"\n", b"\r"):
                    start += 1
                length = self._resolve_length(value.get("Length"))
                end_pos = entry.raw.rfind(b"endstream")
                data_end = start + length if length is not None and start + length <= end_pos else end_pos
                if length is None or start + length > end_pos:
                    if entry.raw[data_end - 2 : data_end] == b"\r\n":
                        data_end -= 2
                    elif entry.raw[data_end - 1 : data_end] in (b"\n", b"\r"):
                        data_end -= 1
                value = Stream(value, entry.raw[start:data_end])
            entry.value = value
        return entry

    def get(self, ref):
        """Resolve a reference (or return the value itself)."""
        if isinstance(ref, Ref):
            return self._parse_entry(ref.num).value
        return ref

    def set(self, num: int, value) -> None:
        entry = self.entries.get(num)
        if entry is None:
            entry = _Entry(offset=-1, gen=0)
            self.entries[num] = entry
        entry.value = value
        entry.edited = True
        entry.raw = None

    def add(self, value) -> Ref:
        num = max(self.entries) + 1 if self.entries else 1
        self.set(num, value)
        return Ref(num, 0)

    def stream_data(self, stream: Stream) -> bytes:
        """Decode a stream (FlateDecode only; others are returned raw)."""
        filters = stream.dict.get("Filter")
        if filters is None:
            return stream.raw
        if not isinstance(filters, list):
            filters = [filters]
        data = stream.raw
        params = stream.dict.get("DecodeParms")
        if not isinstance(params, list):
            params = [params]
        for i, filt in enumerate(filters):
            name = str(self.get(filt))
            if name in ("FlateDecode", "Fl"):
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompressobj().decompress(data)
                parm = self.get(params[i]) if i < len(params) else None
                if isinstance(parm, dict) and parm.get("Predictor", 1) not in (None, 1):
                    data = _undo_predictor(data, parm, self)
            elif name in ("ASCIIHexDecode", "AHx"):
                data = bytes.fromhex(re.sub(rb"[^0-9A-Fa-f]", b"", data.split(b">")[0]).decode())
            else:
                raise PdfFileError(f"unsupported stream filter {name}")
        return data

    @staticmethod
    def make_stream(data: bytes, extra: dict | None = None) -> Stream:
        raw = zlib.compress(data, 6)
        info = {"Length": len(raw), "Filter": Name("FlateDecode")}
        if extra:
            for key, value in extra.items():
                if key in ("Filter", "DecodeParms", "Length"):
                    continue
                info[key] = value
        return Stream(info, raw)

    # -- page tree -----------------------------------------------------------
    @property
    def catalog(self) -> dict:
        root = self.get(self.trailer.get("Root"))
        if not isinstance(root, dict):
            raise PdfFileError("catalog not found")
        return root

    def page_refs(self) -> list[Ref]:
        pages = self.catalog.get("Pages")
        out: list[Ref] = []
        seen = set()

        def visit(ref) -> None:
            if ref in seen:
                return
            seen.add(ref)
            node = self.get(ref)
            if not isinstance(node, dict):
                return
            kind = str(node.get("Type", ""))
            if kind == "Page" or ("Kids" not in node and "Contents" in node):
                out.append(ref)
                return
            for kid in self.get(node.get("Kids", [])) or []:
                visit(kid)

        visit(pages)
        return out

    def inherited(self, page: dict, key: str):
        node = page
        seen = 0
        while isinstance(node, dict) and seen < 64:
            if key in node:
                return node[key]
            node = self.get(node.get("Parent"))
            seen += 1
        return None

    # -- writing -------------------------------------------------------------
    def write(self) -> bytes:
        out = bytearray(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n")
        offsets: dict[int, int] = {}
        for num in sorted(self.entries):
            entry = self.entries[num]
            offsets[num] = len(out)
            if entry.edited or entry.raw is None and entry.value is not None:
                out += self._serialize_entry(num, entry)
            else:
                if entry.raw is None:
                    self._parse_entry(num)
                out += entry.raw
            out += b"\n"
        size = max(self.entries) + 1 if self.entries else 1
        xref_pos = len(out)
        out += b"xref\n0 %d\n" % size
        out += b"0000000000 65535 f \n"
        for num in range(1, size):
            if num in offsets:
                out += b"%010d %05d n \n" % (offsets[num], self.entries[num].gen)
            else:
                out += b"0000000000 65535 f \n"
        trailer = {k: v for k, v in self.trailer.items() if k not in ("Prev", "XRefStm", "Size")}
        trailer["Size"] = size
        out += b"trailer\n" + serialize(trailer) + b"\nstartxref\n%d\n%%%%EOF\n" % xref_pos
        return bytes(out)

    def _serialize_entry(self, num: int, entry: _Entry) -> bytes:
        value = entry.value
        head = b"%d %d obj\n" % (num, entry.gen)
        if isinstance(value, Stream):
            info = dict(value.dict)
            info["Length"] = len(value.raw)
            return head + serialize(info) + b"\nstream\n" + value.raw + b"\nendstream\nendobj"
        return head + serialize(value) + b"\nendobj"


def _undo_predictor(data: bytes, parm: dict, pdf: PdfFile) -> bytes:
    predictor = int(pdf.get(parm.get("Predictor", 1)) or 1)
    if predictor < 10:
        return data
    colors = int(pdf.get(parm.get("Colors", 1)) or 1)
    bpc = int(pdf.get(parm.get("BitsPerComponent", 8)) or 8)
    columns = int(pdf.get(parm.get("Columns", 1)) or 1)
    bpp = max(1, colors * bpc // 8)
    rowlen = (colors * bpc * columns + 7) // 8
    out = bytearray()
    prev = bytearray(rowlen)
    pos = 0
    while pos + 1 <= len(data):
        ft = data[pos]
        row = bytearray(data[pos + 1 : pos + 1 + rowlen])
        pos += 1 + rowlen
        if len(row) < rowlen:
            break
        for i in range(rowlen):
            left = row[i - bpp] if i >= bpp else 0
            up = prev[i]
            ul = prev[i - bpp] if i >= bpp else 0
            if ft == 1:
                row[i] = (row[i] + left) & 0xFF
            elif ft == 2:
                row[i] = (row[i] + up) & 0xFF
            elif ft == 3:
                row[i] = (row[i] + ((left + up) >> 1)) & 0xFF
            elif ft == 4:
                p = left + up - ul
                pa, pb, pc = abs(p - left), abs(p - up), abs(p - ul)
                pred = left if pa <= pb and pa <= pc else up if pb <= pc else ul
                row[i] = (row[i] + pred) & 0xFF
        out += row
        prev = row
    return bytes(out)


_ = Keyword
