"""Text layout: characters -> spans -> lines -> blocks, and the output formats.

The grouping is geometric and follows the reading order of the content
stream.  A new line starts when a character leaves the running baseline,
jumps backwards, or leaves a gap wider than a word space; a new block starts
when a line is not the next line of the same paragraph (vertical gap, or a
line beside rather than below the previous one).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ._bridge import (
    FONT_FIXED_PITCH,
    FONT_FORCE_BOLD,
    FONT_ITALIC,
    FONT_SERIF,
    RawChar,
)
from .geometry import Rect

__all__ = [
    "Span",
    "Line",
    "Block",
    "layout_chars",
    "text_flags",
    "TEXT_PRESERVE_LIGATURES",
    "TEXT_PRESERVE_WHITESPACE",
    "TEXT_PRESERVE_IMAGES",
    "TEXT_INHIBIT_SPACES",
    "TEXT_DEHYPHENATE",
    "TEXT_PRESERVE_SPANS",
    "TEXT_MEDIABOX_CLIP",
    "TEXT_CID_FOR_UNKNOWN_UNICODE",
    "TEXTFLAGS_TEXT",
    "TEXTFLAGS_DICT",
    "TEXTFLAGS_WORDS",
    "TEXTFLAGS_BLOCKS",
]

# Compatible constant values.
TEXT_PRESERVE_LIGATURES = 1
TEXT_PRESERVE_WHITESPACE = 2
TEXT_PRESERVE_IMAGES = 4
TEXT_INHIBIT_SPACES = 8
TEXT_DEHYPHENATE = 16
TEXT_PRESERVE_SPANS = 32
TEXT_MEDIABOX_CLIP = 64
TEXT_CID_FOR_UNKNOWN_UNICODE = 128
TEXTFLAGS_TEXT = TEXT_PRESERVE_LIGATURES | TEXT_PRESERVE_WHITESPACE | TEXT_MEDIABOX_CLIP
TEXTFLAGS_DICT = TEXTFLAGS_TEXT | TEXT_PRESERVE_IMAGES
TEXTFLAGS_WORDS = TEXTFLAGS_TEXT
TEXTFLAGS_BLOCKS = TEXTFLAGS_DICT

# Layout thresholds, all relative to the current font size.
BASELINE_TOLERANCE = 0.5  # baseline shift that still counts as the same line
BACKWARD_TOLERANCE = 0.5  # how far a glyph may start before the previous advance end
SPACE_GAP = 0.1  # gap that gets an implicit space
LINE_GAP = 0.8  # gap that ends the line instead
PARAGRAPH_PITCH = 1.5  # baseline distance (in font sizes) that ends a block
INDENT_SHIFT = 0.2  # a line starting further right than the previous one ends a block
SUPERSCRIPT_RAISE = 0.15

_BOLD_RE = re.compile(r"bold|black|heavy|semibold|demibold|extrabold|ultrabold", re.I)
_ITALIC_RE = re.compile(r"italic|oblique", re.I)
_MONO_RE = re.compile(r"mono|courier|consolas", re.I)


def text_flags(font: str, font_flags: int, weight: int, superscript: bool = False) -> int:
    flags = 0
    if superscript:
        flags |= 1
    if font_flags & FONT_ITALIC or _ITALIC_RE.search(font or ""):
        flags |= 2
    if font_flags & FONT_SERIF:
        flags |= 4
    if font_flags & FONT_FIXED_PITCH or _MONO_RE.search(font or ""):
        flags |= 8
    if font_flags & FONT_FORCE_BOLD or weight >= 600 or _BOLD_RE.search(font or ""):
        flags |= 16
    return flags


@dataclass(slots=True)
class Span:
    chars: list[RawChar] = field(default_factory=list)
    inserted: list[bool] = field(default_factory=list)  # implicit space markers
    font: str = ""
    size: float = 0.0
    flags: int = 0
    color: int = 0
    superscript: bool = False

    @property
    def text(self) -> str:
        return "".join(ch.char for ch in self.chars)

    @property
    def bbox(self) -> Rect:
        rect = Rect()
        for ch in self.chars:
            rect.include_rect(ch.bbox)
        return rect

    @property
    def origin(self):
        return self.chars[0].origin if self.chars else None

    def ascender_descender(self) -> tuple[float, float]:
        if not self.chars or self.size <= 0:
            return 0.8, -0.2
        ch = self.chars[0]
        ascender = (ch.origin.y - ch.bbox.y0) / self.size
        descender = (ch.origin.y - ch.bbox.y1) / self.size
        return ascender, descender


@dataclass(slots=True)
class Line:
    spans: list[Span] = field(default_factory=list)
    dir: tuple[float, float] = (1.0, 0.0)
    baseline: float = 0.0
    size: float = 0.0
    seq: int = 0  # content order of the first char's text object

    @property
    def chars(self) -> list[RawChar]:
        return [ch for span in self.spans for ch in span.chars]

    @property
    def text(self) -> str:
        return "".join(span.text for span in self.spans)

    @property
    def bbox(self) -> Rect:
        rect = Rect()
        for span in self.spans:
            rect.include_rect(span.bbox)
        return rect

    @property
    def wmode(self) -> int:
        return 1 if abs(self.dir[1]) > abs(self.dir[0]) else 0


@dataclass(slots=True)
class Block:
    lines: list[Line] = field(default_factory=list)
    number: int = 0
    seq: int = 0

    @property
    def bbox(self) -> Rect:
        rect = Rect()
        for line in self.lines:
            rect.include_rect(line.bbox)
        return rect

    @property
    def text(self) -> str:
        return "".join(line.text + "\n" for line in self.lines)


def _along(ch: RawChar, direction: tuple[float, float]) -> tuple[float, float, float]:
    """(start, end, baseline) of the char projected on its writing direction."""
    dx, dy = direction
    if abs(dx) >= abs(dy):
        sign = 1.0 if dx >= 0 else -1.0
        start, end = (ch.bbox.x0, ch.bbox.x1) if sign > 0 else (-ch.bbox.x1, -ch.bbox.x0)
        return start, end, ch.origin.y
    sign = 1.0 if dy >= 0 else -1.0
    start, end = (ch.bbox.y0, ch.bbox.y1) if sign > 0 else (-ch.bbox.y1, -ch.bbox.y0)
    return start, end, ch.origin.x


def _same_dir(a: tuple[float, float], b: tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) < 0.05 and abs(a[1] - b[1]) < 0.05


def _synthetic_space(prev: RawChar, nxt: RawChar) -> RawChar:
    """A space character filling the gap between two glyphs."""
    if abs(prev.dir[0]) >= abs(prev.dir[1]):
        box = Rect(prev.bbox.x1, prev.bbox.y0, nxt.bbox.x0, prev.bbox.y1)
    else:
        box = Rect(prev.bbox.x0, prev.bbox.y1, prev.bbox.x1, nxt.bbox.y0)
    box.normalize()
    return RawChar(
        index=-1,
        char=" ",
        bbox=box,
        tight=Rect(box),
        origin=type(prev.origin)(box.x0, prev.origin.y),
        size=prev.size,
        font=prev.font,
        font_flags=prev.font_flags,
        weight=prev.weight,
        color=prev.color,
        obj=prev.obj,
        dir=prev.dir,
        generated=True,
        page_origin=prev.page_origin,
    )


def layout_chars(chars: list[RawChar], obj_seq: dict[int, int] | None = None) -> list[Block]:
    """Group characters into blocks/lines/spans following content order."""
    obj_seq = obj_seq or {}
    blocks: list[Block] = []
    block: Block | None = None
    line: Line | None = None
    span: Span | None = None
    prev: RawChar | None = None

    def start_block(seq: int) -> None:
        nonlocal block
        block = Block(number=len(blocks), seq=seq)
        blocks.append(block)

    def start_line(ch: RawChar) -> None:
        nonlocal line
        line = Line(dir=ch.dir, baseline=_along(ch, ch.dir)[2], size=ch.size, seq=obj_seq.get(ch.obj, 0))
        block.lines.append(line)

    def start_span(ch: RawChar, superscript: bool) -> None:
        nonlocal span
        span = Span(
            font=ch.font,
            size=ch.size,
            flags=text_flags(ch.font, ch.font_flags, ch.weight, superscript),
            color=ch.color,
            superscript=superscript,
        )
        line.spans.append(span)

    for ch in chars:
        if ch.generated:
            continue
        if ch.char in "\r\n":
            continue
        size = ch.size if ch.size > 0 else (prev.size if prev else 1.0)
        new_line = False
        new_block = False
        add_space = False
        if prev is None or line is None:
            new_line = new_block = True
        else:
            ref = max(size, prev.size, 1.0)
            if not _same_dir(ch.dir, line.dir):
                new_line = new_block = True
            else:
                start, _end, base = _along(ch, line.dir)
                pstart, pend, _pbase = _along(prev, line.dir)
                baseline_shift = abs(base - line.baseline)
                gap = start - pend
                # Expanded ligatures share one glyph box, so a glyph that starts
                # before the previous one *ended* is only a line break when it
                # also starts before the previous one *started*.
                backward = start < pstart - BACKWARD_TOLERANCE * ref
                if baseline_shift > BASELINE_TOLERANCE * ref:
                    new_line = True
                elif backward:
                    new_line = True
                elif gap > LINE_GAP * ref:
                    new_line = True
                elif gap > SPACE_GAP * ref and ch.char != " " and prev.char != " ":
                    add_space = True
                if new_line:
                    # Is the new line the next line of the same paragraph?
                    lb = line.bbox
                    if line.wmode == 0:
                        dy = ch.origin.y - line.baseline
                        beside = abs(dy) <= BASELINE_TOLERANCE * ref
                        if not beside:
                            horizontal_overlap = ch.bbox.x0 < lb.x1 + ref and ch.bbox.x1 > lb.x0 - ref
                            far = dy > PARAGRAPH_PITCH * size
                            indented = ch.bbox.x0 > lb.x0 + INDENT_SHIFT * size
                            if dy <= 0 or far or indented or not horizontal_overlap:
                                new_block = True
                    else:
                        dx = ch.origin.x - line.baseline
                        beside = abs(dx) <= BASELINE_TOLERANCE * ref
                        if not beside and abs(dx) > PARAGRAPH_PITCH * size:
                            new_block = True
        if new_block:
            start_block(obj_seq.get(ch.obj, 0))
        if new_line:
            start_line(ch)
            span = None
        raised = (
            line.spans
            and ch.size < line.size * 0.9
            and (line.baseline - _along(ch, line.dir)[2]) > SUPERSCRIPT_RAISE * line.size
        )
        if add_space and span is not None:
            filler = _synthetic_space(prev, ch)
            span.chars.append(filler)
            span.inserted.append(True)
        if (
            span is None
            or span.font != ch.font
            or abs(span.size - ch.size) > 0.05
            or span.color != ch.color
            or span.superscript != bool(raised)
            or span.flags != text_flags(ch.font, ch.font_flags, ch.weight, bool(raised))
        ):
            start_span(ch, bool(raised))
        span.chars.append(ch)
        span.inserted.append(False)
        prev = ch
    return blocks


def sort_blocks(blocks: list[Block]) -> list[Block]:
    ordered = sorted(blocks, key=lambda b: (b.bbox.y1, b.bbox.x0))
    for i, blk in enumerate(ordered):
        blk.number = i
    return ordered
