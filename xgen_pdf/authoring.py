"""Minimal content authoring: text lines and stroked/filled paths."""

from __future__ import annotations

import pypdfium2.raw as c

from . import _bridge
from .geometry import Point

__all__ = ["insert_text", "draw_path", "STANDARD_FONTS"]

STANDARD_FONTS = {
    "helv": "Helvetica",
    "hebo": "Helvetica-Bold",
    "heit": "Helvetica-Oblique",
    "hebi": "Helvetica-BoldOblique",
    "cour": "Courier",
    "cobo": "Courier-Bold",
    "coit": "Courier-Oblique",
    "cobi": "Courier-BoldOblique",
    "tiro": "Times-Roman",
    "tibo": "Times-Bold",
    "tiit": "Times-Italic",
    "tibi": "Times-BoldItalic",
    "symb": "Symbol",
    "zadb": "ZapfDingbats",
}


def _color255(color) -> tuple[int, int, int]:
    if color is None:
        return (0, 0, 0)
    if isinstance(color, (int, float)):
        v = int(round(float(color) * 255))
        return (v, v, v)
    vals = list(color)
    if len(vals) == 1:
        v = int(round(vals[0] * 255))
        return (v, v, v)
    if len(vals) == 4:  # CMYK -> RGB
        cc, m, y, k = vals
        return tuple(int(round(255 * (1 - min(1.0, x + k)))) for x in (cc, m, y))
    return tuple(int(round(max(0.0, min(1.0, v)) * 255)) for v in vals[:3])


def insert_text(page, point: Point, text: str, fontsize: float, fontname: str, fontfile, color) -> None:
    doc = page._doc
    if fontfile:
        font = doc._file_font(str(fontfile))
    else:
        font = doc._standard_font(STANDARD_FONTS.get(fontname, fontname or "Helvetica"))
    obj = c.FPDFPageObj_CreateTextObj(doc.raw, font, float(fontsize))
    if not obj:
        raise RuntimeError("cannot create text object")
    if not c.FPDFText_SetText(obj, _bridge.utf16_buffer(text)):
        raise RuntimeError("cannot set text")
    r, g, b = _color255(color)
    c.FPDFPageObj_SetFillColor(obj, r, g, b, 255)
    user = Point(point).transform(page._inv)
    inv = page._inv
    c.FPDFPageObj_Transform(obj, inv.a, inv.b, inv.c, inv.d, user.x, user.y)
    c.FPDFPage_InsertObject(page.raw, obj)


def draw_path(page, points: list[Point], color, fill, width: float, close: bool) -> None:
    if not points:
        return
    inv = page._inv
    first = Point(points[0]).transform(inv)
    path = c.FPDFPageObj_CreateNewPath(first.x, first.y)
    if not path:
        raise RuntimeError("cannot create path object")
    for pt in points[1:]:
        user = Point(pt).transform(inv)
        c.FPDFPath_LineTo(path, user.x, user.y)
    if close:
        c.FPDFPath_Close(path)
    stroke = color is not None
    if stroke:
        r, g, b = _color255(color)
        c.FPDFPageObj_SetStrokeColor(path, r, g, b, 255)
        c.FPDFPageObj_SetStrokeWidth(path, float(width))
    if fill is not None:
        r, g, b = _color255(fill)
        c.FPDFPageObj_SetFillColor(path, r, g, b, 255)
    c.FPDFPath_SetDrawMode(
        path, c.FPDF_FILLMODE_WINDING if fill is not None else c.FPDF_FILLMODE_NONE, 1 if stroke else 0
    )
    c.FPDFPage_InsertObject(page.raw, path)
