"""PDF object syntax: lexer, parser and serializer.

Used for the parts of a file we rewrite ourselves (page dictionaries,
resources, content streams).  Objects map to Python values:

* numbers -> ``int`` / ``float``
* names   -> :class:`Name` (a ``str`` subclass, without the leading slash)
* strings -> ``bytes``
* arrays  -> ``list``; dictionaries -> ``dict`` keyed by ``str``
* references -> :class:`Ref`
* operators / keywords (content streams) -> :class:`Keyword`
* ``null`` -> ``None``; booleans -> ``bool``
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Name", "Ref", "Keyword", "Stream", "Lexer", "parse_object", "serialize", "PdfSyntaxError"]

WHITESPACE = b"\x00\t\n\x0c\r "
DELIMITERS = b"()<>[]{}/%"
_TOKEN_END = WHITESPACE + DELIMITERS


class PdfSyntaxError(ValueError):
    pass


class Name(str):
    __slots__ = ()

    def __repr__(self) -> str:
        return f"/{str(self)}"


@dataclass(frozen=True, slots=True)
class Ref:
    num: int
    gen: int = 0

    def __repr__(self) -> str:
        return f"{self.num} {self.gen} R"


class Keyword(str):
    __slots__ = ()

    def __repr__(self) -> str:
        return f"<{str(self)}>"


@dataclass(slots=True)
class Stream:
    dict: dict
    raw: bytes  # encoded bytes exactly as stored


_NUMBER_RE = re.compile(rb"[+-]?(?:\d+\.?\d*|\.\d+)")


class Lexer:
    """Tokenizer over a bytes buffer."""

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def skip_ws(self) -> None:
        data, n = self.data, len(self.data)
        while self.pos < n:
            ch = data[self.pos]
            if ch in WHITESPACE:
                self.pos += 1
            elif ch == 0x25:  # % comment
                while self.pos < n and data[self.pos] not in b"\r\n":
                    self.pos += 1
            else:
                break

    def peek_byte(self) -> int | None:
        self.skip_ws()
        if self.pos >= len(self.data):
            return None
        return self.data[self.pos]

    def next_token(self):
        """Return the next token: (kind, value). kinds: num, name, str, delim, kw, eof."""
        self.skip_ws()
        data, n = self.data, len(self.data)
        if self.pos >= n:
            return ("eof", None)
        ch = data[self.pos]
        if ch == 0x2F:  # /name
            self.pos += 1
            start = self.pos
            while self.pos < n and data[self.pos] not in _TOKEN_END:
                self.pos += 1
            raw = data[start : self.pos]
            return ("name", Name(_decode_name(raw)))
        if ch == 0x28:  # ( literal string
            return ("str", self._literal_string())
        if ch == 0x3C:  # <
            if self.pos + 1 < n and data[self.pos + 1] == 0x3C:
                self.pos += 2
                return ("delim", "<<")
            return ("str", self._hex_string())
        if ch == 0x3E:  # >
            if self.pos + 1 < n and data[self.pos + 1] == 0x3E:
                self.pos += 2
                return ("delim", ">>")
            self.pos += 1
            return ("delim", ">")
        if ch in b"[]{}":
            self.pos += 1
            return ("delim", chr(ch))
        if ch == 0x29:
            self.pos += 1
            return ("delim", ")")
        m = _NUMBER_RE.match(data, self.pos)
        if m and m.end() > self.pos and (m.end() >= n or data[m.end()] in _TOKEN_END):
            text = m.group(0)
            self.pos = m.end()
            if b"." in text:
                try:
                    return ("num", float(text))
                except ValueError:
                    return ("num", 0.0)
            try:
                return ("num", int(text))
            except ValueError:
                return ("num", 0)
        start = self.pos
        while self.pos < n and data[self.pos] not in _TOKEN_END:
            self.pos += 1
        if self.pos == start:  # lone delimiter byte
            self.pos += 1
        return ("kw", data[start : self.pos].decode("latin-1"))

    def _literal_string(self) -> bytes:
        data, n = self.data, len(self.data)
        self.pos += 1  # (
        depth = 1
        out = bytearray()
        while self.pos < n:
            ch = data[self.pos]
            if ch == 0x5C:  # backslash
                self.pos += 1
                if self.pos >= n:
                    break
                esc = data[self.pos]
                if esc in b"nrtbf":
                    out.append({0x6E: 10, 0x72: 13, 0x74: 9, 0x62: 8, 0x66: 12}[esc])
                    self.pos += 1
                elif 0x30 <= esc <= 0x37:
                    octal = 0
                    count = 0
                    while count < 3 and self.pos < n and 0x30 <= data[self.pos] <= 0x37:
                        octal = octal * 8 + (data[self.pos] - 0x30)
                        self.pos += 1
                        count += 1
                    out.append(octal & 0xFF)
                elif esc == 0x0D:
                    self.pos += 1
                    if self.pos < n and data[self.pos] == 0x0A:
                        self.pos += 1
                elif esc == 0x0A:
                    self.pos += 1
                else:
                    out.append(esc)
                    self.pos += 1
                continue
            if ch == 0x28:
                depth += 1
            elif ch == 0x29:
                depth -= 1
                if depth == 0:
                    self.pos += 1
                    break
            out.append(ch)
            self.pos += 1
        return bytes(out)

    def _hex_string(self) -> bytes:
        data, n = self.data, len(self.data)
        self.pos += 1  # <
        digits = bytearray()
        while self.pos < n and data[self.pos] != 0x3E:
            ch = data[self.pos]
            if ch not in WHITESPACE:
                digits.append(ch)
            self.pos += 1
        self.pos += 1  # >
        if len(digits) % 2:
            digits.append(0x30)
        try:
            return bytes.fromhex(digits.decode("ascii"))
        except ValueError:
            return b""


def _decode_name(raw: bytes) -> str:
    if b"#" not in raw:
        return raw.decode("latin-1")
    out = bytearray()
    i = 0
    while i < len(raw):
        if raw[i] == 0x23 and i + 2 < len(raw):
            try:
                out.append(int(raw[i + 1 : i + 3], 16))
                i += 3
                continue
            except ValueError:
                pass
        out.append(raw[i])
        i += 1
    return out.decode("latin-1")


def parse_object(lexer: Lexer, allow_refs: bool = True):
    """Parse one object starting at the lexer position.

    Returns ``Keyword`` for bare keywords/operators so content streams can be
    parsed with the same function.
    """
    kind, value = lexer.next_token()
    if kind == "eof":
        raise EOFError
    if kind == "num":
        if allow_refs and isinstance(value, int) and value >= 0:
            save = lexer.pos
            k2, v2 = lexer.next_token()
            if k2 == "num" and isinstance(v2, int) and v2 >= 0:
                k3, v3 = lexer.next_token()
                if k3 == "kw" and v3 == "R":
                    return Ref(value, v2)
            lexer.pos = save
        return value
    if kind in ("name", "str"):
        return value
    if kind == "delim":
        if value == "[":
            items = []
            while True:
                if lexer.peek_byte() == 0x5D:
                    lexer.pos += 1
                    return items
                if lexer.peek_byte() is None:
                    return items
                items.append(parse_object(lexer, allow_refs))
        if value == "<<":
            result: dict = {}
            while True:
                lexer.skip_ws()
                if lexer.data.startswith(b">>", lexer.pos):
                    lexer.pos += 2
                    return result
                k, key = lexer.next_token()
                if k == "eof":
                    return result
                if k != "name":
                    continue  # malformed key: skip
                result[str(key)] = parse_object(lexer, allow_refs)
        if value in ("{", "}"):
            return Keyword(value)
        return Keyword(value)
    # keyword
    if value == "true":
        return True
    if value == "false":
        return False
    if value == "null":
        return None
    return Keyword(value)


def serialize(obj) -> bytes:
    if isinstance(obj, bool):
        return b"true" if obj else b"false"
    if obj is None:
        return b"null"
    if isinstance(obj, int):
        return str(obj).encode("ascii")
    if isinstance(obj, float):
        if obj != obj or obj in (float("inf"), float("-inf")):
            return b"0"
        if obj == int(obj) and abs(obj) < 1e15:
            return str(int(obj)).encode("ascii")
        text = repr(obj)
        if "e" in text or "E" in text:
            text = f"{obj:.10f}"
        text = text.rstrip("0").rstrip(".")
        if text in ("", "-", "-0"):
            text = "0"
        return text.encode("ascii")
    if isinstance(obj, Name):
        return b"/" + _encode_name(str(obj))
    if isinstance(obj, Keyword):
        return str(obj).encode("latin-1")
    if isinstance(obj, Ref):
        return f"{obj.num} {obj.gen} R".encode("ascii")
    if isinstance(obj, (bytes, bytearray)):
        return b"<" + bytes(obj).hex().encode("ascii") + b">"
    if isinstance(obj, str):
        return b"/" + _encode_name(obj)
    if isinstance(obj, list):
        return b"[" + b" ".join(serialize(item) for item in obj) + b"]"
    if isinstance(obj, dict):
        parts = [b"/" + _encode_name(str(k)) + b" " + serialize(v) for k, v in obj.items()]
        return b"<<" + b" ".join(parts) + b">>"
    raise TypeError(f"cannot serialize {type(obj)!r}")


def _encode_name(name: str) -> bytes:
    out = bytearray()
    for ch in name.encode("latin-1", errors="replace"):
        if ch < 0x21 or ch > 0x7E or ch in DELIMITERS or ch == 0x23:
            out += b"#%02X" % ch
        else:
            out.append(ch)
    return bytes(out)
