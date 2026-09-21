"""Redaction: physically remove text, image pixels and line art under a rect.

The edit happens in the PDF content streams themselves.  pdfium tells us
where every glyph is; the content stream is tokenised, the glyphs that
fall inside a redaction rectangle are cut out of their string operators
(with a ``TJ`` adjustment so the remaining glyphs keep their positions),
painting operators of line art inside a rectangle are neutralised, image
draws inside a rectangle are dropped, and partially covered images get
their pixels blanked.  Every other byte of the page content is preserved,
so nothing that pdfium's own content writer cannot express (Type 3 text,
form-scoped resources) is at risk.  Finally the rectangles are painted,
with an optional label, from a small appended content stream.
"""

from __future__ import annotations

import io
import logging

import pypdfium2 as pdfium
import pypdfium2.raw as c

from . import _bridge
from .authoring import _color255
from .content import (
    ContentWalker,
    FontCodec,
    Op,
    codec_for_font,
    expand_ops,
    parse_content,
    rewrite_show,
    serialize_content,
)
from .geometry import Matrix, Point, Rect
from .pdffile import PdfFile
from .pdfsyntax import Name, Ref, Stream

log = logging.getLogger("xgen_pdf")

__all__ = ["apply_redactions", "RedactionError"]

MATCH_TOLERANCE = 1.0  # points, glyph origin matching between pdfium and the walker
SHRINK = 0.1  # ignore the outer margin of a glyph box when testing intersection


class RedactionError(RuntimeError):
    """The page could not be redacted safely."""


# --------------------------------------------------------------------------
# Content-stream edits
# --------------------------------------------------------------------------


class _PageEditor:
    def __init__(self, page, data: bytes):
        self.page = page
        self.pdf = PdfFile(data)
        refs = self.pdf.page_refs()
        if page.number >= len(refs):
            raise RedactionError("page not found in saved document")
        self.page_ref: Ref = refs[page.number]
        self.page_dict = dict(self.pdf.get(self.page_ref))
        self._ops_cache: dict = {}
        self._codec_cache: dict = {}
        resources = self.pdf.get(self.pdf.inherited(self.page_dict, "Resources"))
        self.resources = resources if isinstance(resources, dict) else {}
        self.walker = ContentWalker(self.pdf, page._dm, self._ops, self._resources_for)
        self.walker.walk("page", Matrix(), self.resources)
        self.edited_streams: set = set()

    # -- streams ---------------------------------------------------------------
    def _ops(self, stream_id) -> list[Op]:
        ops = self._ops_cache.get(stream_id)
        if ops is None:
            if stream_id == "page":
                contents = self.pdf.get(self.page_dict.get("Contents"))
                parts = contents if isinstance(contents, list) else [self.page_dict.get("Contents")]
                data = b"\n".join(self._stream_bytes(self.pdf.get(p)) for p in parts if p is not None)
            else:
                data = self._stream_bytes(self.pdf.get(stream_id))
            ops = parse_content(data)
            self._ops_cache[stream_id] = ops
        return ops

    def _stream_bytes(self, obj) -> bytes:
        if isinstance(obj, Stream):
            return self.pdf.stream_data(obj)
        return b""

    def _resources_for(self, stream_id) -> dict:
        if stream_id == "page":
            return self.resources
        obj = self.pdf.get(stream_id)
        if isinstance(obj, Stream):
            res = self.pdf.get(obj.dict.get("Resources"))
            if isinstance(res, dict):
                return res
        return self.resources

    def _codec(self, resources: dict, font_name: str) -> FontCodec:
        fonts = self.pdf.get(resources.get("Font")) if resources else None
        ref = fonts.get(font_name) if isinstance(fonts, dict) else None
        key = ref if isinstance(ref, Ref) else (id(resources), font_name)
        codec = self._codec_cache.get(key)
        if codec is None:
            font = self.pdf.get(ref)
            codec = codec_for_font(font, self.pdf) if isinstance(font, dict) else FontCodec(simple=True, known=False)
            self._codec_cache[key] = codec
        return codec

    # -- text ------------------------------------------------------------------
    def remove_text(self, rects: list[Rect]) -> int:
        page = self.page
        chars = [ch for ch in page._get_chars() if not ch.generated and ch.char not in "\r\n"]
        by_obj: dict[int, list[_bridge.RawChar]] = {}
        for ch in chars:
            by_obj.setdefault(ch.obj, []).append(ch)
        # first-glyph origins of pdfium text objects, for matching
        origins = [(own[0].origin.x, own[0].origin.y, ident) for ident, own in by_obj.items() if own]
        removed_total = 0
        used = set()
        for show in self.walker.shows:
            origin = Point(0, show.rise).transform(show.matrix)
            best = None
            best_d = MATCH_TOLERANCE
            for ox, oy, ident in origins:
                d = abs(ox - origin.x) + abs(oy - origin.y)
                if d < best_d and ident not in used:
                    best, best_d = ident, d
            if best is None:
                continue
            own = by_obj[best]
            if not any(r.intersects(_shrink(ch.bbox)) for ch in own for r in rects):
                continue
            used.add(best)
            codec = self._codec(show.resources, show.font)
            codes = [code for text in show.strings for code in codec.codes(text)]
            removals: set[int] = set()
            adjustments: dict[int, float] = {}
            if codec.known and len(codes) == len(own):
                i = 0
                while i < len(own):
                    if any(r.intersects(_shrink(own[i].bbox)) for r in rects):
                        j = i
                        while j + 1 < len(own) and any(r.intersects(_shrink(own[j + 1].bbox)) for r in rects):
                            j += 1
                        for k in range(i, j + 1):
                            removals.add(k)
                        start = own[i].origin
                        if j + 1 < len(own):
                            end = own[j + 1].origin
                        else:
                            end = _advance_end(own[j], codec.vertical)
                        adjustments[i] = self._adjustment(show, codec, start, end)
                        i = j + 1
                    else:
                        i += 1
            else:
                # Cannot map glyphs one-to-one: remove the whole operator.
                removals = set(range(len(codes)))
                start = own[0].origin
                end = _advance_end(own[-1], codec.vertical)
                adjustments[0] = self._adjustment(show, codec, start, end)
                log.warning(
                    "redaction: removing a whole text run (%d glyphs) because its encoding could not be split",
                    len(codes),
                )
            if not removals:
                continue
            new_op = rewrite_show(show.op, codec, removals, adjustments)
            self._ops(show.stream_id)[show.index] = new_op
            self.edited_streams.add(show.stream_id)
            removed_total += len(removals)
        return removed_total

    def _adjustment(self, show, codec: FontCodec, start: Point, end: Point) -> float:
        linear = Matrix(show.matrix.a, show.matrix.b, show.matrix.c, show.matrix.d, 0, 0)
        try:
            inv = ~linear
        except Exception:
            return 0.0
        delta = Point(end.x - start.x, end.y - start.y).transform(inv)
        if show.size == 0:
            return 0.0
        if codec.vertical:
            return -delta.y * 1000.0 / show.size
        hscale = show.hscale if show.hscale else 1.0
        return -delta.x * 1000.0 / (show.size * hscale)

    # -- line art / images -------------------------------------------------------
    def remove_line_art(self, rects: list[Rect], mode: int) -> int:
        count = 0
        for paint in self.walker.paints:
            if paint.op.operator == "n":
                continue
            box = paint.bbox
            if mode == 1:
                hit = any(r.x0 <= box.x0 and r.y0 <= box.y0 and box.x1 <= r.x1 and box.y1 <= r.y1 for r in rects)
            else:
                hit = any(_touches(r, box) for r in rects)
            if not hit:
                continue
            self._ops(paint.stream_id)[paint.index] = Op([], "n")
            self.edited_streams.add(paint.stream_id)
            count += 1
        return count

    def remove_images(self, rects: list[Rect], mode: int) -> int:
        count = 0
        for image in self.walker.images:
            covered = any(r.contains(image.bbox) for r in rects)
            touched = any(r.intersects(image.bbox) for r in rects)
            if covered or (mode == 1 and touched):
                ops = self._ops(image.stream_id)
                ops[image.index] = Op([], "")  # no-op placeholder, dropped on serialise
                self.edited_streams.add(image.stream_id)
                count += 1
        return count

    def blank_images(self, rects: list[Rect]) -> int:
        """Blank the pixels of partially covered images by rewriting the
        image XObject (and its soft mask) with our own encoding."""
        count = 0
        done: set = set()
        pdfium_images = [o for o in self.page._walk() if o.type == _bridge.OBJ_IMAGE]
        for image in self.walker.images:
            if not isinstance(image.ref, Ref) or image.ref in done:
                continue
            touching = [r for r in rects if r.intersects(image.bbox)]
            if not touching or any(r.contains(image.bbox) for r in rects):
                continue
            handle = None
            for obj in pdfium_images:
                bounds = Rect(0, 0, 1, 1).transform(obj.matrix * self.page._dm).normalize()
                if abs(bounds.x0 - image.bbox.x0) < 0.5 and abs(bounds.y0 - image.bbox.y0) < 0.5:
                    handle = obj.handle
                    break
            if handle is None:
                log.warning("redaction: image placement not found for pixel blanking; removing the image")
                self._ops(image.stream_id)[image.index] = Op([], "")
                self.edited_streams.add(image.stream_id)
                continue
            pil = _decode_image(handle)
            if pil is None:
                log.warning("redaction: image could not be decoded for pixel blanking; removing the image")
                self._ops(image.stream_id)[image.index] = Op([], "")
                self.edited_streams.add(image.stream_id)
                continue
            inv = ~image.matrix
            width, height = pil.size
            boxes = []
            for rect in touching:
                unit = Rect(rect).transform(inv).normalize() & Rect(0, 0, 1, 1)
                if unit.is_empty:
                    continue
                boxes.append(
                    (
                        max(0, int(unit.x0 * width)),
                        max(0, int((1 - unit.y1) * height)),
                        min(width, int(unit.x1 * width + 0.999)),
                        min(height, int((1 - unit.y0) * height + 0.999)),
                    )
                )
            if not boxes:
                continue
            white = (255, 255, 255) if pil.mode == "RGB" else 255
            for box in boxes:
                pil.paste(white, box)
            stream = self.pdf.get(image.ref)
            if not isinstance(stream, Stream):
                continue
            info = {
                k: v
                for k, v in stream.dict.items()
                if k
                not in (
                    "Filter",
                    "DecodeParms",
                    "Length",
                    "Decode",
                    "Mask",
                    "ImageMask",
                    "ColorSpace",
                    "BitsPerComponent",
                    "Width",
                    "Height",
                    "SMask",
                )
            }
            info["Width"] = width
            info["Height"] = height
            info["BitsPerComponent"] = 8
            info["ColorSpace"] = Name("DeviceRGB" if pil.mode == "RGB" else "DeviceGray")
            smask = self.pdf.get(stream.dict.get("SMask"))
            if isinstance(smask, Stream):
                new_mask = _blank_soft_mask(self.pdf, smask, boxes, (width, height))
                if new_mask is not None:
                    info["SMask"] = self.pdf.add(new_mask)
                else:
                    log.warning("redaction: soft mask could not be edited; dropping it")
            self.pdf.set(image.ref.num, self.pdf.make_stream(pil.tobytes(), extra=info))
            done.add(image.ref)
            count += 1
        return count

    def _content_refs(self) -> list:
        """The page's content streams as a flat list of references."""
        contents = self.page_dict.get("Contents")
        resolved = self.pdf.get(contents)
        if isinstance(resolved, list):
            return [ref for ref in resolved if ref is not None]
        return [contents] if contents is not None else []

    # -- boxes -------------------------------------------------------------------
    def paint_boxes(self, specs: list[dict]) -> None:
        inv = self.page._inv
        parts = [b"q"]
        needs_font = False
        for spec in specs:
            rect = Rect(spec["rect"])
            fill = spec.get("fill")
            if fill is not None and fill is not False:
                user = Rect(rect).transform(inv).normalize()
                r, g, b = (v / 255.0 for v in _color255(fill))
                parts.append(
                    f"{r:.3f} {g:.3f} {b:.3f} rg {user.x0:.3f} {user.y0:.3f} {user.width:.3f} {user.height:.3f} re f".encode()
                )
            text = spec.get("text")
            if text:
                needs_font = True
                fontsize = float(spec.get("fontsize") or 11)
                tr, tg, tb = (v / 255.0 for v in _color255(spec.get("text_color", (0, 0, 0))))
                baseline = Point(rect.x0 + 1.0, rect.y1 - max(1.0, fontsize * 0.25))
                width = _helvetica_width(text) * fontsize
                align = spec.get("align", 0)
                if align == 1:
                    baseline.x = rect.x0 + max(0.0, (rect.width - width) / 2)
                elif align == 2:
                    baseline.x = max(rect.x0, rect.x1 - width - 1.0)
                origin = Point(baseline).transform(inv)
                encoded = text.encode("cp1252", errors="replace")
                parts.append(
                    f"BT /XgHv {fontsize:.2f} Tf {tr:.3f} {tg:.3f} {tb:.3f} rg "
                    f"{inv.a:.4f} {inv.b:.4f} {inv.c:.4f} {inv.d:.4f} {origin.x:.3f} {origin.y:.3f} Tm <{encoded.hex()}> Tj ET".encode()
                )
        parts.append(b"Q")
        stream = self.pdf.make_stream(b"\n".join(parts) + b"\n")
        box_ref = self.pdf.add(stream)
        self.page_dict["Contents"] = self._content_refs() + [box_ref]
        if needs_font:
            font_ref = self.pdf.add(
                {
                    "Type": Name("Font"),
                    "Subtype": Name("Type1"),
                    "BaseFont": Name("Helvetica"),
                    "Encoding": Name("WinAnsiEncoding"),
                }
            )
            resources = dict(self.resources)
            fonts = self.pdf.get(resources.get("Font"))
            fonts = dict(fonts) if isinstance(fonts, dict) else {}
            fonts["XgHv"] = font_ref
            resources["Font"] = fonts
            self.page_dict["Resources"] = resources
            self.resources = resources

    # -- commit --------------------------------------------------------------------
    def commit(self) -> bytes:
        # Page content: always rewrite as one stream when edited.
        if "page" in self.edited_streams:
            ops = [op for op in expand_ops(self._ops("page")) if op.operator or op.inline_image is not None]
            new_ref = self.pdf.add(self.pdf.make_stream(serialize_content(ops)))
            # keep only streams we appended after the original content (box streams)
            appended = [ref for ref in self._content_refs() if isinstance(ref, Ref) and ref.num > self._max_original]
            self.page_dict["Contents"] = [new_ref] + appended
        for stream_id in self.edited_streams:
            if stream_id == "page" or not isinstance(stream_id, Ref):
                continue
            obj = self.pdf.get(stream_id)
            if not isinstance(obj, Stream):
                continue
            ops = [op for op in expand_ops(self._ops(stream_id)) if op.operator or op.inline_image is not None]
            new_stream = self.pdf.make_stream(serialize_content(ops), extra=obj.dict)
            self.pdf.set(stream_id.num, new_stream)
        self.pdf.set(self.page_ref.num, self.page_dict)
        return self.pdf.write()

    @property
    def _max_original(self) -> int:
        return self._max_original_num

    def snapshot_object_count(self) -> None:
        self._max_original_num = max(self.pdf.entries) if self.pdf.entries else 0


def _touches(r: Rect, box: Rect) -> bool:
    """Intersection test that also works for zero-width/height boxes."""
    return r.x0 <= box.x1 and box.x0 <= r.x1 and r.y0 <= box.y1 and box.y0 <= r.y1


def _decode_image(handle):
    """Decode an image object through pdfium into a PIL image (RGB or L)."""
    bitmap_raw = c.FPDFImageObj_GetBitmap(handle)
    if not bitmap_raw:
        return None
    bitmap = pdfium.PdfBitmap.from_raw(bitmap_raw)
    try:
        pil = bitmap.to_pil()
    finally:
        bitmap.close()
    if pil.mode == "L":
        return pil
    return pil.convert("RGB")


def _blank_soft_mask(pdf: PdfFile, smask: Stream, boxes, size) -> Stream | None:
    """Return a copy of the soft mask made opaque inside ``boxes``."""
    from PIL import Image

    try:
        width = int(pdf.get(smask.dict.get("Width")))
        height = int(pdf.get(smask.dict.get("Height")))
        bpc = int(pdf.get(smask.dict.get("BitsPerComponent", 8)) or 8)
        filters = pdf.get(smask.dict.get("Filter"))
        names = [str(pdf.get(f)) for f in (filters if isinstance(filters, list) else [filters] if filters else [])]
        if names and names[-1] == "DCTDecode":
            import io as _io

            pil = Image.open(_io.BytesIO(smask.raw)).convert("L")
        else:
            data = pdf.stream_data(smask)
            if bpc != 8 or len(data) < width * height:
                return None
            pil = Image.frombytes("L", (width, height), data[: width * height])
    except Exception:
        return None
    sx, sy = width / size[0], height / size[1]
    for x0, y0, x1, y1 in boxes:
        pil.paste(255, (int(x0 * sx), int(y0 * sy), int(x1 * sx + 0.999), int(y1 * sy + 0.999)))
    info = {k: v for k, v in smask.dict.items() if k not in ("Filter", "DecodeParms", "Length", "Decode")}
    info["BitsPerComponent"] = 8
    info["ColorSpace"] = Name("DeviceGray")
    return PdfFile.make_stream(pil.tobytes(), extra=info)


def _shrink(rect: Rect, ratio: float = SHRINK) -> Rect:
    dx = rect.width * ratio
    dy = rect.height * ratio
    return Rect(rect.x0 + dx, rect.y0 + dy, rect.x1 - dx, rect.y1 - dy)


def _advance_end(ch: _bridge.RawChar, vertical: bool) -> Point:
    """Display-space point where the glyph's advance ends."""
    dx, dy = ch.dir
    box = ch.bbox
    if vertical or abs(dy) > abs(dx):
        y = box.y1 if dy >= 0 else box.y0
        return Point(ch.origin.x, y)
    x = box.x1 if dx >= 0 else box.x0
    return Point(x, ch.origin.y)


_HELV_WIDTHS = {
    " ": 278,
    "!": 278,
    '"': 355,
    "#": 556,
    "$": 556,
    "%": 889,
    "&": 667,
    "'": 191,
    "(": 333,
    ")": 333,
    "*": 389,
    "+": 584,
    ",": 278,
    "-": 333,
    ".": 278,
    "/": 278,
    "0": 556,
    "1": 556,
    "2": 556,
    "3": 556,
    "4": 556,
    "5": 556,
    "6": 556,
    "7": 556,
    "8": 556,
    "9": 556,
    ":": 278,
    ";": 278,
    "<": 584,
    "=": 584,
    ">": 584,
    "?": 556,
    "@": 1015,
    "A": 667,
    "B": 667,
    "C": 722,
    "D": 722,
    "E": 667,
    "F": 611,
    "G": 778,
    "H": 722,
    "I": 278,
    "J": 500,
    "K": 667,
    "L": 556,
    "M": 833,
    "N": 722,
    "O": 778,
    "P": 667,
    "Q": 778,
    "R": 722,
    "S": 667,
    "T": 611,
    "U": 722,
    "V": 667,
    "W": 944,
    "X": 667,
    "Y": 667,
    "Z": 611,
    "[": 278,
    "\\": 278,
    "]": 278,
    "^": 469,
    "_": 556,
    "`": 333,
    "a": 556,
    "b": 556,
    "c": 500,
    "d": 556,
    "e": 556,
    "f": 278,
    "g": 556,
    "h": 556,
    "i": 222,
    "j": 222,
    "k": 500,
    "l": 222,
    "m": 833,
    "n": 556,
    "o": 556,
    "p": 556,
    "q": 556,
    "r": 333,
    "s": 500,
    "t": 278,
    "u": 556,
    "v": 500,
    "w": 722,
    "x": 500,
    "y": 500,
    "z": 500,
    "{": 334,
    "|": 260,
    "}": 334,
    "~": 584,
}


def _helvetica_width(text: str) -> float:
    return sum(_HELV_WIDTHS.get(ch, 556) for ch in text) / 1000.0


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def apply_redactions(page, specs: list[dict], images: int = 2, graphics: int = 1, text: int = 0) -> None:
    doc = page._doc
    rects = [Rect(spec["rect"]) for spec in specs]
    flags = c.FPDF_REMOVE_SECURITY if doc.is_encrypted else c.FPDF_NO_INCREMENTAL
    buf = io.BytesIO()
    doc._pdf.save(buf, flags=flags)
    editor = _PageEditor(page, buf.getvalue())
    editor.snapshot_object_count()
    if text == 0:
        editor.remove_text(rects)
    if images:
        editor.remove_images(rects, images)
        if images == 2:
            editor.blank_images(rects)
    if graphics:
        editor.remove_line_art(rects, graphics)
    editor.paint_boxes(specs)
    new_data = editor.commit()
    doc._replace_data(new_data)
