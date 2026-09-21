"""Thin ctypes helpers over the pdfium C API (via pypdfium2.raw).

Everything that talks to pdfium directly lives here so the rest of the
package works with plain Python values in top-left page coordinates.
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from typing import Iterator

import pypdfium2.raw as c

from .geometry import Matrix, Point, Rect

# pdfium page object types (fpdf_edit.h)
OBJ_UNKNOWN = 0
OBJ_TEXT = 1
OBJ_PATH = 2
OBJ_IMAGE = 3
OBJ_SHADING = 4
OBJ_FORM = 5

# path segment types
SEG_UNKNOWN = -1
SEG_LINETO = 0
SEG_BEZIERTO = 1
SEG_MOVETO = 2

# fill modes
FILL_NONE = 0
FILL_ALTERNATE = 1
FILL_WINDING = 2

# PDF font descriptor flags (PDF 32000-1 §9.8.2)
FONT_FIXED_PITCH = 1 << 0
FONT_SERIF = 1 << 1
FONT_SYMBOLIC = 1 << 2
FONT_SCRIPT = 1 << 3
FONT_NONSYMBOLIC = 1 << 5
FONT_ITALIC = 1 << 6
FONT_FORCE_BOLD = 1 << 18


def ptr_value(handle) -> int:
    """Stable integer identity of a pdfium handle (for dict keys)."""
    if handle is None:
        return 0
    if isinstance(handle, int):
        return handle
    try:
        return ctypes.cast(handle, ctypes.c_void_p).value or 0
    except Exception:
        return id(handle)


def utf16_buffer(text: str):
    """NUL-terminated UTF-16 code unit array as pdfium's FPDF_WIDESTRING."""
    units = list(text.encode("utf-16-le"))
    count = len(units) // 2
    arr = (ctypes.c_ushort * (count + 1))()
    for i in range(count):
        arr[i] = units[2 * i] | (units[2 * i + 1] << 8)
    arr[count] = 0
    return arr


def get_meta_text(doc_handle, key: str) -> str:
    length = c.FPDF_GetMetaText(doc_handle, key.encode("ascii"), None, 0)
    if length <= 2:
        return ""
    buf = ctypes.create_string_buffer(length)
    c.FPDF_GetMetaText(doc_handle, key.encode("ascii"), buf, length)
    return buf.raw[: length - 2].decode("utf-16-le", errors="replace")


# --------------------------------------------------------------------------
# Page geometry
# --------------------------------------------------------------------------


def display_matrix(page_handle, width: float, height: float) -> Matrix:
    """Matrix mapping pdfium page (user) space to top-left display space.

    Uses ``FPDF_DeviceToPage`` on a huge integer viewport to recover the exact
    affine map that pdfium itself applies when rendering, including /Rotate
    and the CropBox offset, without depending on undocumented internals.
    """
    size = 1_000_000
    px, py = ctypes.c_double(), ctypes.c_double()

    def to_page(dx: int, dy: int) -> Point:
        c.FPDF_DeviceToPage(page_handle, 0, 0, size, size, 0, dx, dy, px, py)
        return Point(px.value, py.value)

    origin = to_page(0, 0)  # display top-left
    right = to_page(size, 0)  # display top-right
    down = to_page(0, size)  # display bottom-left
    # page -> display: solve inverse of display -> page
    ex = (right - origin) / width  # page-space vector for one display x unit
    ey = (down - origin) / height  # page-space vector for one display y unit
    inv = Matrix(ex.x, ex.y, ey.x, ey.y, origin.x, origin.y)  # display -> page
    return ~inv


# --------------------------------------------------------------------------
# Characters
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RawChar:
    index: int
    char: str
    bbox: Rect  # loose (ascender/descender based) box, display coords
    tight: Rect  # glyph box, display coords
    origin: Point  # display coords
    size: float  # effective font size in points
    font: str
    font_flags: int
    weight: int
    color: int  # 0xRRGGBB
    obj: int  # owning text object identity
    dir: tuple[float, float]  # writing direction in display space (unit)
    generated: bool
    page_origin: Point = field(default=None)  # pdfium user-space origin


def font_name_and_flags(textpage, index: int) -> tuple[str, int]:
    flags = ctypes.c_int()
    length = c.FPDFText_GetFontInfo(textpage, index, None, 0, flags)
    if length <= 0:
        return "", 0
    buf = ctypes.create_string_buffer(length)
    c.FPDFText_GetFontInfo(textpage, index, buf, length, flags)
    name = buf.raw[: length - 1].decode("utf-8", errors="replace")
    return name, flags.value


def clean_font_name(name: str) -> str:
    if len(name) > 7 and name[6] == "+" and name[:6].isalpha() and name[:6].isupper():
        return name[7:]
    return name


def iter_chars(textpage, page_handle, dm: Matrix) -> Iterator[RawChar]:
    """Yield every character of the text page in content order.

    pdfium reports non-BMP code points as UTF-16 surrogate pairs; they are
    merged here into one character spanning both boxes.
    """
    pending: RawChar | None = None
    for ch in _iter_chars_raw(textpage, page_handle, dm):
        if pending is not None:
            if 0xDC00 <= ord(ch.char) <= 0xDFFF and ch.obj == pending.obj:
                code = 0x10000 + ((ord(pending.char) - 0xD800) << 10) + (ord(ch.char) - 0xDC00)
                pending.char = chr(code)
                pending.bbox = Rect(pending.bbox).include_rect(ch.bbox)
                pending.tight = Rect(pending.tight).include_rect(ch.tight)
                yield pending
                pending = None
                continue
            pending.char = "\ufffd"
            yield pending
            pending = None
        if 0xD800 <= ord(ch.char) <= 0xDBFF:
            pending = ch
            continue
        yield ch
    if pending is not None:
        pending.char = "\ufffd"
        yield pending


def _iter_chars_raw(textpage, page_handle, dm: Matrix) -> Iterator[RawChar]:
    count = c.FPDFText_CountChars(textpage)
    left, right, bottom, top = (ctypes.c_double() for _ in range(4))
    ox, oy = ctypes.c_double(), ctypes.c_double()
    loose = c.FS_RECTF()
    mat = c.FS_MATRIX()
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    obj_cache: dict[int, tuple] = {}
    for i in range(count):
        code = c.FPDFText_GetUnicode(textpage, i)
        ch = chr(code) if 0 <= code < 0x110000 else "�"
        generated = c.FPDFText_IsGenerated(textpage, i) == 1
        obj = ptr_value(c.FPDFText_GetTextObject(textpage, i))
        c.FPDFText_GetCharBox(textpage, i, left, right, bottom, top)
        tight = Rect(Point(left.value, bottom.value), Point(right.value, top.value))
        c.FPDFText_GetLooseCharBox(textpage, i, loose)
        loose_rect = Rect(Point(loose.left, loose.bottom), Point(loose.right, loose.top))
        c.FPDFText_GetCharOrigin(textpage, i, ox, oy)
        origin = Point(ox.value, oy.value)
        cached = obj_cache.get(obj)
        if cached is None:
            raw_size = c.FPDFText_GetFontSize(textpage, i)
            c.FPDFText_GetMatrix(textpage, i, mat)
            scale_y = (mat.c * mat.c + mat.d * mat.d) ** 0.5
            size = raw_size * (scale_y if scale_y > 0 else 1.0)
            font, fflags = font_name_and_flags(textpage, i)
            font = clean_font_name(font)
            weight = c.FPDFText_GetFontWeight(textpage, i)
            if c.FPDFText_GetFillColor(textpage, i, r, g, b, a):
                color = (r.value << 16) | (g.value << 8) | b.value
            else:
                color = 0
            # direction: text-space x axis through the char matrix, then display
            dvec = Point(mat.a, mat.b)
            dvec = dvec.transform(Matrix(dm.a, dm.b, dm.c, dm.d, 0, 0))
            length = abs(dvec)
            direction = (dvec.x / length, dvec.y / length) if length > 0 else (1.0, 0.0)
            cached = (size, font, fflags, weight, color, direction)
            obj_cache[obj] = cached
        size, font, fflags, weight, color, direction = cached
        yield RawChar(
            index=i,
            char=ch,
            bbox=loose_rect.transform(dm).normalize(),
            tight=tight.transform(dm).normalize(),
            origin=Point(origin).transform(dm),
            size=size,
            font=font,
            font_flags=fflags,
            weight=weight,
            color=color,
            obj=obj,
            dir=direction,
            generated=generated,
            page_origin=origin,
        )


# --------------------------------------------------------------------------
# Page objects
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RawObject:
    handle: object  # FPDF_PAGEOBJECT
    ident: int
    type: int
    matrix: Matrix  # object -> page user space (form matrices composed)
    parent: object  # FPDF_PAGEOBJECT of the enclosing form, or None
    index: int  # index within its container
    seq: int  # global walk order
    level: int


def get_object_matrix(handle) -> Matrix:
    mat = c.FS_MATRIX()
    if c.FPDFPageObj_GetMatrix(handle, mat):
        return Matrix(mat.a, mat.b, mat.c, mat.d, mat.e, mat.f)
    return Matrix()


def walk_objects(page_handle, max_depth: int = 16) -> list[RawObject]:
    """All page objects, forms recursively flattened, with composed matrices."""
    out: list[RawObject] = []
    counter = [0]

    def visit(container, count_fn, get_fn, parent_matrix: Matrix, parent, level: int) -> None:
        n = count_fn(container)
        for idx in range(n):
            handle = get_fn(container, idx)
            if not handle:
                continue
            otype = c.FPDFPageObj_GetType(handle)
            own = get_object_matrix(handle)
            if otype == OBJ_FORM:
                composed = own * parent_matrix
                if level < max_depth:
                    visit(
                        handle,
                        c.FPDFFormObj_CountObjects,
                        c.FPDFFormObj_GetObject,
                        composed,
                        handle,
                        level + 1,
                    )
                continue
            out.append(
                RawObject(
                    handle=handle,
                    ident=ptr_value(handle),
                    type=otype,
                    matrix=own * parent_matrix,
                    parent=parent,
                    index=idx,
                    seq=counter[0],
                    level=level,
                )
            )
            counter[0] += 1

    visit(page_handle, c.FPDFPage_CountObjects, c.FPDFPage_GetObject, Matrix(), None, 0)
    return out


def object_bounds(handle) -> Rect | None:
    left, bottom, right, top = (ctypes.c_float() for _ in range(4))
    if not c.FPDFPageObj_GetBounds(handle, left, bottom, right, top):
        return None
    return Rect(Point(left.value, bottom.value), Point(right.value, top.value))


def fill_color(handle) -> tuple[float, float, float, float] | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not c.FPDFPageObj_GetFillColor(handle, r, g, b, a):
        return None
    return (r.value / 255.0, g.value / 255.0, b.value / 255.0, a.value / 255.0)


def stroke_color(handle) -> tuple[float, float, float, float] | None:
    r, g, b, a = (ctypes.c_uint() for _ in range(4))
    if not c.FPDFPageObj_GetStrokeColor(handle, r, g, b, a):
        return None
    return (r.value / 255.0, g.value / 255.0, b.value / 255.0, a.value / 255.0)


def stroke_width(handle) -> float:
    width = ctypes.c_float()
    if c.FPDFPageObj_GetStrokeWidth(handle, width):
        return float(width.value)
    return 1.0


def dash_array(handle) -> list[float]:
    count = c.FPDFPageObj_GetDashCount(handle)
    if count <= 0:
        return []
    arr = (ctypes.c_float * count)()
    if not c.FPDFPageObj_GetDashArray(handle, arr, count):
        return []
    return [float(v) for v in arr]


def dash_phase(handle) -> float:
    phase = ctypes.c_float()
    if c.FPDFPageObj_GetDashPhase(handle, phase):
        return float(phase.value)
    return 0.0


def path_segments(handle) -> list[tuple[int, float, float, bool]]:
    """(segment type, x, y, closes) in object space."""
    n = c.FPDFPath_CountSegments(handle)
    x, y = ctypes.c_float(), ctypes.c_float()
    out = []
    for i in range(n):
        seg = c.FPDFPath_GetPathSegment(handle, i)
        if not seg:
            continue
        stype = c.FPDFPathSegment_GetType(seg)
        if not c.FPDFPathSegment_GetPoint(seg, x, y):
            continue
        closes = bool(c.FPDFPathSegment_GetClose(seg))
        out.append((stype, float(x.value), float(y.value), closes))
    return out


def path_draw_mode(handle) -> tuple[int, bool]:
    fillmode, stroke = ctypes.c_int(), ctypes.c_int()
    if not c.FPDFPath_GetDrawMode(handle, fillmode, stroke):
        return FILL_NONE, False
    return fillmode.value, bool(stroke.value)


def image_metadata(handle, page_handle):
    meta = c.FPDF_IMAGEOBJ_METADATA()
    if not c.FPDFImageObj_GetImageMetadata(handle, page_handle, meta):
        return None
    return meta


def image_filters(handle) -> list[str]:
    n = c.FPDFImageObj_GetImageFilterCount(handle)
    out = []
    for i in range(n):
        length = c.FPDFImageObj_GetImageFilter(handle, i, None, 0)
        if length <= 0:
            continue
        buf = ctypes.create_string_buffer(length)
        c.FPDFImageObj_GetImageFilter(handle, i, buf, length)
        out.append(buf.raw[: length - 1].decode("ascii", errors="replace"))
    return out


def image_raw_data(handle) -> bytes:
    length = c.FPDFImageObj_GetImageDataRaw(handle, None, 0)
    if length <= 0:
        return b""
    buf = ctypes.create_string_buffer(length)
    c.FPDFImageObj_GetImageDataRaw(handle, buf, length)
    return buf.raw[:length]


def image_decoded_data(handle) -> bytes:
    length = c.FPDFImageObj_GetImageDataDecoded(handle, None, 0)
    if length <= 0:
        return b""
    buf = ctypes.create_string_buffer(length)
    c.FPDFImageObj_GetImageDataDecoded(handle, buf, length)
    return buf.raw[:length]
