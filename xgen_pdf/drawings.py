"""Vector drawings: path page objects -> list of drawing dicts."""

from __future__ import annotations

from . import _bridge
from .geometry import Matrix, Point, Rect

__all__ = ["collect_drawings"]


def _rgb(color):
    if color is None:
        return None
    return (color[0], color[1], color[2])


def _is_axis_rect(points: list[Point]) -> Rect | None:
    """If four (or five with closing repeat) points form an axis-aligned
    rectangle, return it."""
    if len(points) == 5 and points[0] == points[4]:
        points = points[:4]
    if len(points) != 4:
        return None
    xs = sorted({round(p.x, 3) for p in points})
    ys = sorted({round(p.y, 3) for p in points})
    if len(xs) != 2 or len(ys) != 2:
        return None
    for p in points:
        if round(p.x, 3) not in xs or round(p.y, 3) not in ys:
            return None
    # consecutive points must share x or y (no diagonals)
    for a, b in zip(points, points[1:] + points[:1]):
        if abs(a.x - b.x) > 1e-3 and abs(a.y - b.y) > 1e-3:
            return None
    return Rect(xs[0], ys[0], xs[1], ys[1])


def _subpaths(segments, matrix: Matrix):
    """Split segments into subpaths of display-space points."""
    subpaths = []
    current = []
    closed = False
    for stype, x, y, closes in segments:
        pt = Point(x, y).transform(matrix)
        if stype == _bridge.SEG_MOVETO:
            if current:
                subpaths.append((current, closed))
            current = [("m", pt)]
            closed = False
        elif stype == _bridge.SEG_LINETO:
            current.append(("l", pt))
        elif stype == _bridge.SEG_BEZIERTO:
            current.append(("c", pt))
        if closes:
            closed = True
    if current:
        subpaths.append((current, closed))
    return subpaths


def _items_for(subpaths) -> tuple[list, Rect, bool]:
    items = []
    rect = Rect()
    any_closed = False
    seen_point = False
    for ops, closed in subpaths:
        any_closed = any_closed or closed
        points = [pt for _op, pt in ops]
        line_only = all(op in ("m", "l") for op, _pt in ops)
        as_rect = _is_axis_rect(points) if line_only and closed else None
        if as_rect is None and line_only and len(points) == 5 and points[0] == points[4]:
            as_rect = _is_axis_rect(points)
        if as_rect is not None:
            items.append(("re", as_rect, 1))
            if seen_point:
                rect.include_rect(as_rect)
            else:
                rect = Rect(as_rect)
                seen_point = True
            continue
        last = None
        pending_curve: list[Point] = []
        for op, pt in ops:
            if seen_point:
                rect.include_point(pt)
            else:
                rect = Rect(pt.x, pt.y, pt.x, pt.y)
                seen_point = True
            if op == "m":
                last = pt
                continue
            if op == "l":
                if last is not None:
                    items.append(("l", Point(last), Point(pt)))
                last = pt
            elif op == "c":
                pending_curve.append(pt)
                if len(pending_curve) == 3 and last is not None:
                    items.append(("c", Point(last), pending_curve[0], pending_curve[1], pending_curve[2]))
                    last = pending_curve[2]
                    pending_curve = []
        if closed and last is not None and points and last != points[0] and line_only:
            items.append(("l", Point(last), Point(points[0])))
    return items, rect, any_closed


def collect_drawings(page) -> list[dict]:
    dm = page._dm
    out = []
    for obj in page._walk():
        if obj.type != _bridge.OBJ_PATH:
            continue
        segments = _bridge.path_segments(obj.handle)
        if not segments:
            continue
        matrix = obj.matrix * dm
        subpaths = _subpaths(segments, matrix)
        items, rect, closed = _items_for(subpaths)
        if not items:
            continue
        fillmode, stroked = _bridge.path_draw_mode(obj.handle)
        fill_rgba = _bridge.fill_color(obj.handle) if fillmode != _bridge.FILL_NONE else None
        stroke_rgba = _bridge.stroke_color(obj.handle) if stroked else None
        width = _bridge.stroke_width(obj.handle) if stroked else 0.0
        scale = (abs(matrix.a * matrix.d - matrix.b * matrix.c)) ** 0.5
        if stroked and scale > 0:
            width *= scale
        dashes = _bridge.dash_array(obj.handle) if stroked else []
        kind = ("f" if fill_rgba is not None else "") + ("s" if stroked else "")
        if not kind:
            continue
        out.append(
            {
                "items": items,
                "type": kind,
                "even_odd": fillmode == _bridge.FILL_ALTERNATE,
                "fill_opacity": fill_rgba[3] if fill_rgba is not None else None,
                "fill": _rgb(fill_rgba),
                "rect": rect,
                "seqno": obj.seq,
                "layer": "",
                "level": obj.level,
                "closePath": closed,
                "color": _rgb(stroke_rgba),
                "width": width if stroked else 0.0,
                "lineCap": (0, 0, 0),
                "lineJoin": 0,
                "dashes": f"[{' '.join(str(round(d, 3)) for d in dashes)}] 0" if dashes else "[] 0",
                "stroke_opacity": stroke_rgba[3] if stroke_rgba is not None else None,
            }
        )
    return out
