"""Page: text, drawings, images, rendering, tables, redaction, simple authoring."""

from __future__ import annotations

import ctypes
import hashlib
import math
from typing import TYPE_CHECKING

import pypdfium2 as pdfium
import pypdfium2.raw as c

from . import _bridge
from .geometry import Matrix, Point, Quad, Rect
from .text import Block, layout_chars

if TYPE_CHECKING:
    from .document import Document
    from .pixmap import Pixmap
    from .tables import TableFinder

__all__ = ["Page"]


class Page:
    def __init__(self, doc: "Document", raw_page: pdfium.PdfPage, number: int):
        self._doc = doc
        self._raw = raw_page
        self.number = number
        self._closed = False
        self._modified = False
        self._pending_redactions: list[dict] = []
        self._reset_caches()
        self._width = float(raw_page.get_width())
        self._height = float(raw_page.get_height())
        self._dm = _bridge.display_matrix(raw_page.raw, self._width, self._height)
        self._inv = ~self._dm

    # -- basics --------------------------------------------------------------
    def _reset_caches(self) -> None:
        self._textpage = None
        self._chars = None
        self._blocks: list[Block] | None = None
        self._objects = None
        self._drawings = None
        self._image_entries = None

    def _close(self) -> None:
        if self._closed:
            return
        self._drop_textpage()
        try:
            self._raw.close()
        except Exception:
            pass
        self._closed = True

    def _drop_textpage(self) -> None:
        if self._textpage is not None:
            try:
                self._textpage.close()
            except Exception:
                pass
        self._textpage = None

    def _invalidate(self) -> None:
        self._drop_textpage()
        self._reset_caches()

    def _rebind(self, raw_page: pdfium.PdfPage) -> None:
        """Attach to a freshly loaded pdfium page (same page number)."""
        self._raw = raw_page
        self._closed = False
        self._modified = False
        self._reset_caches()
        self._width = float(raw_page.get_width())
        self._height = float(raw_page.get_height())
        self._dm = _bridge.display_matrix(raw_page.raw, self._width, self._height)
        self._inv = ~self._dm

    def _flush(self) -> None:
        if self._modified:
            self._raw.gen_content()
            self._modified = False
            self._doc._dirty = True

    @property
    def parent(self) -> "Document":
        return self._doc

    @property
    def raw(self):
        return self._raw.raw

    @property
    def rect(self) -> Rect:
        return Rect(0, 0, self._width, self._height)

    def bound(self) -> Rect:
        return self.rect

    @property
    def rotation(self) -> int:
        return int(self._raw.get_rotation())

    @property
    def mediabox(self) -> Rect:
        left, bottom, right, top = self._raw.get_mediabox()
        return Rect(left, bottom, right, top)

    @property
    def cropbox(self) -> Rect:
        box = self._raw.get_cropbox()
        if box is None:
            return self.mediabox
        left, bottom, right, top = box
        return Rect(left, bottom, right, top)

    @property
    def rotation_matrix(self) -> Matrix:
        return Matrix(self._dm)

    @property
    def derotation_matrix(self) -> Matrix:
        return Matrix(self._inv)

    @property
    def transformation_matrix(self) -> Matrix:
        return Matrix(self._dm)

    def __repr__(self) -> str:
        return f"page {self.number} of {self._doc!r}"

    def __hash__(self) -> int:
        return hash((id(self._doc), self.number))

    def __eq__(self, other) -> bool:
        return isinstance(other, Page) and other._doc is self._doc and other.number == self.number

    # -- objects -------------------------------------------------------------
    def _walk(self) -> list[_bridge.RawObject]:
        if self._objects is None:
            self._objects = _bridge.walk_objects(self._raw.raw)
        return self._objects

    # -- text ----------------------------------------------------------------
    def _get_textpage(self):
        if self._textpage is None:
            self._textpage = self._raw.get_textpage()
        return self._textpage

    def _get_chars(self) -> list[_bridge.RawChar]:
        if self._chars is None:
            tp = self._get_textpage()
            self._chars = list(_bridge.iter_chars(tp.raw, self._raw.raw, self._dm))
        return self._chars

    def _get_blocks(self) -> list[Block]:
        if self._blocks is None:
            seq = {obj.ident: obj.seq for obj in self._walk() if obj.type == _bridge.OBJ_TEXT}
            self._blocks = layout_chars(self._get_chars(), seq)
        return self._blocks

    def get_textpage(self, clip=None, flags=None):
        return self

    def get_text(self, option: str = "text", clip=None, flags=None, sort: bool = False, **_ignored):
        from .textout import render_text

        option = (option or "text").lower()
        blocks = self._get_blocks()
        clip_rect = Rect(clip) if clip is not None else None
        if clip_rect is not None:
            blocks = _clip_blocks(blocks, clip_rect)
        include_images = option in ("dict", "rawdict", "json", "rawjson", "html", "xhtml")
        image_blocks = self._image_blocks(clip_rect) if include_images else []
        return render_text(self, option, blocks, image_blocks, sort=sort)

    def search_for(self, needle: str, clip=None, quads: bool = False, flags=None, **_ignored):
        needle = (needle or "").strip("\n")
        if not needle:
            return []
        lowered = needle.lower()
        clip_rect = Rect(clip) if clip is not None else None
        hits: list[Rect] = []
        for block in self._get_blocks():
            for line in block.lines:
                chars = line.chars
                text = "".join(ch.char for ch in chars).lower()
                start = 0
                while True:
                    pos = text.find(lowered, start)
                    if pos < 0:
                        break
                    rect = Rect()
                    for ch in chars[pos : pos + len(lowered)]:
                        rect.include_rect(ch.bbox)
                    start = pos + max(1, len(lowered))
                    if rect.is_empty:
                        continue
                    if clip_rect is not None and not clip_rect.intersects(rect):
                        continue
                    hits.append(rect)
        if quads:
            return [Quad(r) for r in hits]
        return hits

    # -- drawings ------------------------------------------------------------
    def get_drawings(self, extended: bool = False) -> list[dict]:
        from .drawings import collect_drawings

        if self._drawings is None:
            self._drawings = collect_drawings(self)
        return [dict(d) for d in self._drawings]

    def get_cdrawings(self, extended: bool = False) -> list[dict]:
        out = []
        for d in self.get_drawings():
            cd = dict(d)
            cd["rect"] = tuple(d["rect"])
            items = []
            for item in d["items"]:
                op = item[0]
                if op == "l":
                    items.append(("l", tuple(item[1]), tuple(item[2])))
                elif op == "re":
                    items.append(("re", tuple(item[1]), item[2]))
                elif op == "c":
                    items.append(("c",) + tuple(tuple(p) for p in item[1:]))
                elif op == "qu":
                    items.append(("qu", tuple(tuple(p) for p in item[1])))
            cd["items"] = items
            out.append(cd)
        return out

    # -- images --------------------------------------------------------------
    def _collect_images(self) -> list[dict]:
        from .images import collect_images

        if self._image_entries is None:
            self._image_entries = collect_images(self)
        return self._image_entries

    def _image_blocks(self, clip: Rect | None) -> list[dict]:
        out = []
        for entry in self._collect_images():
            bbox = entry["bbox"]
            if clip is not None and not clip.intersects(bbox):
                continue
            info = self._doc._image_cache.get(entry["xref"], {})
            out.append(
                {
                    "number": 0,
                    "type": 1,
                    "bbox": tuple(bbox),
                    "width": info.get("width", entry["width"]),
                    "height": info.get("height", entry["height"]),
                    "ext": info.get("ext", "png"),
                    "colorspace": info.get("colorspace", 3),
                    "xres": info.get("xres", 96),
                    "yres": info.get("yres", 96),
                    "bpc": info.get("bpc", 8),
                    "transform": tuple(entry["transform"]),
                    "size": len(info.get("image", b"")),
                    "image": info.get("image", b""),
                    "_seq": entry["seq"],
                    "_digest": entry["digest"],
                }
            )
        return out

    def get_images(self, full: bool = False) -> list[tuple]:
        seen = set()
        out = []
        for entry in self._collect_images():
            xref = entry["xref"]
            if xref in seen:
                continue
            seen.add(xref)
            info = self._doc._image_cache.get(xref, {})
            item = (
                xref,
                info.get("smask", 0),
                info.get("width", entry["width"]),
                info.get("height", entry["height"]),
                info.get("bpc", 8),
                info.get("cs-name", "DeviceRGB"),
                "",
                f"Im{xref}",
                (info.get("filters") or [""])[0],
            )
            out.append(item + (0,) if full else item)
        return out

    def get_image_info(self, hashes: bool = False, xrefs: bool = False) -> list[dict]:
        out = []
        for i, entry in enumerate(self._collect_images()):
            info = self._doc._image_cache.get(entry["xref"], {})
            item = {
                "number": i,
                "bbox": tuple(entry["bbox"]),
                "transform": tuple(entry["transform"]),
                "width": info.get("width", entry["width"]),
                "height": info.get("height", entry["height"]),
                "colorspace": info.get("colorspace", 3),
                "cs-name": info.get("cs-name", "DeviceRGB"),
                "xres": info.get("xres", 96),
                "yres": info.get("yres", 96),
                "bpc": info.get("bpc", 8),
                "size": len(info.get("image", b"")),
                "has-mask": bool(info.get("smask", 0)),
            }
            if hashes:
                item["digest"] = bytes.fromhex(entry["digest"])
            if xrefs:
                item["xref"] = entry["xref"]
            out.append(item)
        return out

    def get_image_rects(self, item, transform: bool = False) -> list:
        xref = item[0] if isinstance(item, (tuple, list)) else int(item)
        out = []
        for entry in self._collect_images():
            if entry["xref"] != xref:
                continue
            if transform:
                out.append((Rect(entry["bbox"]), Matrix(entry["transform"])))
            else:
                out.append(Rect(entry["bbox"]))
        return out

    def get_image_bbox(self, item, transform: bool = False):
        rects = self.get_image_rects(item, transform=transform)
        if not rects:
            return (Rect(), Matrix()) if transform else Rect()
        return rects[0]

    # -- rendering -----------------------------------------------------------
    def get_pixmap(
        self,
        matrix=None,
        dpi: int | None = None,
        colorspace=None,
        clip=None,
        alpha: bool = False,
        annots: bool = True,
        **_ignored,
    ) -> "Pixmap":
        from .pixmap import Pixmap

        self._flush()
        if dpi is not None:
            zoom_x = zoom_y = dpi / 72.0
            rotation = 0
        else:
            m = Matrix(matrix) if matrix is not None else Matrix()
            zoom_x = math.hypot(m.a, m.b) or 1.0
            zoom_y = math.hypot(m.c, m.d) or 1.0
            angle = round(math.degrees(math.atan2(m.b, m.a))) % 360
            rotation = {0: 0, 90: 1, 180: 2, 270: 3}.get(angle, 0)
        if clip is not None:
            clip_rect = Rect(clip) & self.rect
        else:
            clip_rect = self.rect
        if clip_rect.is_empty:
            clip_rect = self.rect
        crop = (
            clip_rect.x0,
            self._height - clip_rect.y1,
            self._width - clip_rect.x1,
            clip_rect.y0,
        )
        scale = zoom_x
        width_px = max(1, math.ceil(clip_rect.width * zoom_x - 1e-6))
        height_px = max(1, math.ceil(clip_rect.height * zoom_y - 1e-6))
        fill = (255, 255, 255, 0) if alpha else (255, 255, 255, 255)
        bitmap = _render(
            self._raw,
            scale=scale,
            crop=crop,
            rotation=rotation,
            fill_color=fill,
            draw_annots=annots,
            alpha=alpha,
            width=width_px,
            height=height_px,
            zoom_y=zoom_y,
        )
        image = bitmap.to_pil()
        bitmap.close()
        mode = "RGBA" if alpha else "RGB"
        if image.mode != mode:
            image = image.convert(mode)
        if colorspace is not None and getattr(colorspace, "n", 3) == 1:
            image = image.convert("L")
        origin = (int(clip_rect.x0 * zoom_x), int(clip_rect.y0 * zoom_y))
        return Pixmap._from_pil(image, origin=origin, xres=int(72 * zoom_x), yres=int(72 * zoom_y))

    def get_svg_image(self, *args, **kwargs):
        raise NotImplementedError("SVG export is not supported")

    # -- tables --------------------------------------------------------------
    def find_tables(self, **kwargs) -> "TableFinder":
        from .tables import TableFinder

        return TableFinder(self, **kwargs)

    # -- redaction -----------------------------------------------------------
    def add_redact_annot(
        self,
        quad,
        text: str | None = None,
        fontname: str = "helv",
        fontsize: float = 11,
        align: int = 0,
        fill=(1, 1, 1),
        text_color=(0, 0, 0),
        cross_out: bool = True,
    ):
        rect = Rect(quad.rect if isinstance(quad, Quad) else quad).normalize()
        if rect.is_empty:
            raise ValueError("rect is empty")
        self._pending_redactions.append(
            {
                "rect": rect,
                "text": text,
                "fontname": fontname,
                "fontsize": float(fontsize),
                "align": align,
                "fill": fill,
                "text_color": text_color,
                "cross_out": cross_out,
            }
        )
        return _RedactAnnot(rect, text)

    def apply_redactions(self, images: int = 2, graphics: int = 1, text: int = 0) -> bool:
        from .redact import apply_redactions

        if not self._pending_redactions:
            return False
        pending, self._pending_redactions = self._pending_redactions, []
        self._flush()
        apply_redactions(self, pending, images=images, graphics=graphics, text=text)
        self._invalidate()
        return True

    def annots(self, types=None):
        return iter(())

    # -- simple authoring ----------------------------------------------------
    def insert_image(
        self,
        rect,
        filename=None,
        pixmap=None,
        stream=None,
        rotate: int = 0,
        keep_proportion: bool = True,
        overlay: bool = True,
        **_ignored,
    ) -> int:
        from .images import insert_image

        rect = Rect(rect).normalize()
        if rect.is_empty:
            raise ValueError("rect must be finite and not empty")
        insert_image(
            self, rect, filename=filename, pixmap=pixmap, stream=stream, rotate=rotate, keep_proportion=keep_proportion
        )
        self._modified = True
        self._flush()
        self._invalidate()
        return 0

    def insert_text(
        self,
        point,
        text: str,
        fontsize: float = 11,
        fontname: str = "helv",
        fontfile: str | None = None,
        color=(0, 0, 0),
        **_ignored,
    ) -> int:
        from .authoring import insert_text

        lines = str(text).split("\n")
        point = Point(point)
        for i, line in enumerate(lines):
            insert_text(self, Point(point.x, point.y + i * fontsize * 1.2), line, fontsize, fontname, fontfile, color)
        self._modified = True
        self._flush()
        self._invalidate()
        return len(lines)

    def draw_line(self, p1, p2, color=(0, 0, 0), width: float = 1, **_ignored) -> None:
        from .authoring import draw_path

        draw_path(self, [Point(p1), Point(p2)], color=color, fill=None, width=width, close=False)
        self._modified = True
        self._flush()
        self._invalidate()

    def draw_rect(self, rect, color=(0, 0, 0), fill=None, width: float = 1, **_ignored) -> None:
        from .authoring import draw_path

        r = Rect(rect).normalize()
        draw_path(self, [r.tl, r.tr, r.br, r.bl], color=color, fill=fill, width=width, close=True)
        self._modified = True
        self._flush()
        self._invalidate()

    # -- helpers for other modules -------------------------------------------
    def _to_display(self, rect: Rect) -> Rect:
        return Rect(rect).transform(self._dm).normalize()

    def _to_user(self, rect: Rect) -> Rect:
        return Rect(rect).transform(self._inv).normalize()

    def _digest(self, data: bytes) -> str:
        return hashlib.md5(data).hexdigest()


class _RedactAnnot:
    def __init__(self, rect: Rect, text):
        self.rect = rect
        self.type = (12, "Redact")
        self.info = {"content": text or ""}

    def update(self, **_ignored) -> bool:
        return True


def _clip_blocks(blocks: list[Block], clip: Rect) -> list[Block]:
    from .text import Block as _Block, Line as _Line, Span as _Span

    out = []
    for block in blocks:
        new_block = _Block(number=block.number, seq=block.seq)
        for line in block.lines:
            new_line = _Line(dir=line.dir, baseline=line.baseline, size=line.size, seq=line.seq)
            for span in line.spans:
                kept = [(ch, ins) for ch, ins in zip(span.chars, span.inserted) if clip.intersects(ch.bbox)]
                if not kept:
                    continue
                new_span = _Span(
                    font=span.font, size=span.size, flags=span.flags, color=span.color, superscript=span.superscript
                )
                new_span.chars = [ch for ch, _ in kept]
                new_span.inserted = [ins for _, ins in kept]
                new_line.spans.append(new_span)
            if new_line.spans:
                new_block.lines.append(new_line)
        if new_block.lines:
            out.append(new_block)
    return out


def _render(raw_page, scale, crop, rotation, fill_color, draw_annots, alpha, width, height, zoom_y):
    """Render with pdfium, sizing the bitmap exactly to the requested pixels."""
    src_w = math.ceil(raw_page.get_width() * scale)
    src_h = math.ceil(raw_page.get_height() * zoom_y)
    left = int(round(crop[0] * scale))
    top = int(round(crop[3] * zoom_y))
    if rotation in (1, 3):
        width, height = height, width
        src_w, src_h = src_h, src_w
    bitmap = pdfium.PdfBitmap.new_native(
        width, height, format=c.FPDFBitmap_BGRA if alpha else c.FPDFBitmap_BGR, rev_byteorder=False
    )
    bitmap.fill_rect(fill_color, 0, 0, width, height)
    flags = c.FPDF_LCD_TEXT if False else 0
    if draw_annots:
        flags |= c.FPDF_ANNOT
    c.FPDF_RenderPageBitmap(bitmap.raw, raw_page.raw, -left, -top, src_w, src_h, rotation, flags)
    return bitmap


def _ctypes_utf16(text: str):
    return _bridge.utf16_buffer(text)


_ = ctypes  # keep import for type tooling
