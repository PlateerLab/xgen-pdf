"""Table detection from ruling lines or text alignment.

The approach is the classic edge/intersection/cell model: collect edges
(from vector drawings, text alignment, or explicit coordinates), snap and
join them, intersect vertical with horizontal edges, grow the smallest
closed cell from every intersection, and group connected cells into tables.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .geometry import Rect

__all__ = ["TableFinder", "Table", "TableRow", "TableHeader"]

DEFAULTS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "vertical_lines": None,
    "horizontal_lines": None,
    "snap_tolerance": 3,
    "snap_x_tolerance": None,
    "snap_y_tolerance": None,
    "join_tolerance": 3,
    "join_x_tolerance": None,
    "join_y_tolerance": None,
    "edge_min_length": 3,
    "min_words_vertical": 3,
    "min_words_horizontal": 1,
    "intersection_tolerance": 3,
    "intersection_x_tolerance": None,
    "intersection_y_tolerance": None,
    "text_tolerance": 3,
    "text_x_tolerance": None,
    "text_y_tolerance": None,
}


@dataclass(slots=True)
class Edge:
    orientation: str  # "v" or "h"
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def coord(self) -> float:
        return self.x0 if self.orientation == "v" else self.y0

    @property
    def start(self) -> float:
        return self.y0 if self.orientation == "v" else self.x0

    @property
    def end(self) -> float:
        return self.y1 if self.orientation == "v" else self.x1

    @property
    def length(self) -> float:
        return self.end - self.start


@dataclass(slots=True)
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str

    @property
    def cx(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:
        return (self.y0 + self.y1) / 2


def _cluster(values: list[float], tolerance: float) -> list[list[float]]:
    if not values:
        return []
    ordered = sorted(values)
    groups = [[ordered[0]]]
    for v in ordered[1:]:
        if v - groups[-1][-1] <= tolerance:
            groups[-1].append(v)
        else:
            groups.append([v])
    return groups


def _cluster_objects(objs, key, tolerance: float) -> list[list]:
    if not objs:
        return []
    ordered = sorted(objs, key=key)
    groups = [[ordered[0]]]
    for obj in ordered[1:]:
        if key(obj) - key(groups[-1][-1]) <= tolerance:
            groups[-1].append(obj)
        else:
            groups.append([obj])
    return groups


# --------------------------------------------------------------------------
# Edge sources
# --------------------------------------------------------------------------


def _edges_from_drawings(page, clip: Rect | None, strict: bool) -> list[Edge]:
    edges: list[Edge] = []
    for drawing in page.get_drawings():
        items = drawing["items"]
        curved = any(item[0] == "c" for item in items)
        if not strict and curved and drawing.get("closePath") and "f" in drawing.get("type", ""):
            # Filled rounded rectangles (cards, table frames): the straight runs
            # stop short of the corners, so use the whole outline box as edges.
            rect = Rect(drawing["rect"])
            if not rect.is_empty:
                edges.append(Edge("h", rect.x0, rect.y0, rect.x1, rect.y0))
                edges.append(Edge("h", rect.x0, rect.y1, rect.x1, rect.y1))
                edges.append(Edge("v", rect.x0, rect.y0, rect.x0, rect.y1))
                edges.append(Edge("v", rect.x1, rect.y0, rect.x1, rect.y1))
            continue
        for item in items:
            op = item[0]
            if op == "l":
                p1, p2 = item[1], item[2]
                _add_line_edge(edges, p1.x, p1.y, p2.x, p2.y)
            elif op == "re":
                rect = Rect(item[1])
                if rect.is_empty and rect.width == 0 and rect.height == 0:
                    continue
                thin_h = rect.height <= 2.0 and rect.width > rect.height
                thin_v = rect.width <= 2.0 and rect.height > rect.width
                if thin_h:
                    y = (rect.y0 + rect.y1) / 2
                    edges.append(Edge("h", rect.x0, y, rect.x1, y))
                elif thin_v:
                    x = (rect.x0 + rect.x1) / 2
                    edges.append(Edge("v", x, rect.y0, x, rect.y1))
                elif not strict:
                    edges.append(Edge("h", rect.x0, rect.y0, rect.x1, rect.y0))
                    edges.append(Edge("h", rect.x0, rect.y1, rect.x1, rect.y1))
                    edges.append(Edge("v", rect.x0, rect.y0, rect.x0, rect.y1))
                    edges.append(Edge("v", rect.x1, rect.y0, rect.x1, rect.y1))
            elif op == "qu":
                quad = item[1]
                rect = quad.rect
                if not strict and not rect.is_empty:
                    edges.append(Edge("h", rect.x0, rect.y0, rect.x1, rect.y0))
                    edges.append(Edge("h", rect.x0, rect.y1, rect.x1, rect.y1))
                    edges.append(Edge("v", rect.x0, rect.y0, rect.x0, rect.y1))
                    edges.append(Edge("v", rect.x1, rect.y0, rect.x1, rect.y1))
    if clip is not None:
        edges = [e for e in edges if clip.intersects(Rect(e.x0 - 1, e.y0 - 1, e.x1 + 1, e.y1 + 1))]
    return edges


def _add_line_edge(edges: list[Edge], x0: float, y0: float, x1: float, y1: float) -> None:
    if abs(y0 - y1) <= 1.0 and abs(x1 - x0) > 0:
        y = (y0 + y1) / 2
        edges.append(Edge("h", min(x0, x1), y, max(x0, x1), y))
    elif abs(x0 - x1) <= 1.0 and abs(y1 - y0) > 0:
        x = (x0 + x1) / 2
        edges.append(Edge("v", x, min(y0, y1), x, max(y0, y1)))


def _edges_from_words_v(words: list[Word], min_words: int, tolerance: float) -> list[Edge]:
    edges: list[Edge] = []
    for key in (lambda w: w.x0, lambda w: w.x1, lambda w: w.cx):
        for group in _cluster_objects(words, key, tolerance):
            if len(group) < min_words:
                continue
            x = sum(key(w) for w in group) / len(group)
            y0 = min(w.y0 for w in group)
            y1 = max(w.y1 for w in group)
            edges.append(Edge("v", x, y0, x, y1))
    if words:
        x0 = min(w.x0 for w in words)
        x1 = max(w.x1 for w in words)
        y0 = min(w.y0 for w in words)
        y1 = max(w.y1 for w in words)
        edges.append(Edge("v", x0, y0, x0, y1))
        edges.append(Edge("v", x1, y0, x1, y1))
    return edges


def _edges_from_words_h(words: list[Word], min_words: int, tolerance: float) -> list[Edge]:
    edges: list[Edge] = []
    if not words:
        return edges
    x0 = min(w.x0 for w in words)
    x1 = max(w.x1 for w in words)
    for group in _cluster_objects(words, lambda w: w.y0, tolerance):
        if len(group) < min_words:
            continue
        top = min(w.y0 for w in group)
        bottom = max(w.y1 for w in group)
        edges.append(Edge("h", x0, top, x1, top))
        edges.append(Edge("h", x0, bottom, x1, bottom))
    return edges


def _explicit_edges(lines, orientation: str, page_rect: Rect) -> list[Edge]:
    edges: list[Edge] = []
    for line in lines or []:
        if isinstance(line, (int, float)):
            if orientation == "v":
                edges.append(Edge("v", float(line), page_rect.y0, float(line), page_rect.y1))
            else:
                edges.append(Edge("h", page_rect.x0, float(line), page_rect.x1, float(line)))
        else:
            rect = Rect(line)
            if orientation == "v":
                edges.append(Edge("v", rect.x0, rect.y0, rect.x0, rect.y1))
            else:
                edges.append(Edge("h", rect.x0, rect.y0, rect.x1, rect.y0))
    return edges


# --------------------------------------------------------------------------
# Edge processing
# --------------------------------------------------------------------------


def _snap(edges: list[Edge], tolerance: float) -> list[Edge]:
    out: list[Edge] = []
    for group in _cluster_objects(edges, lambda e: e.coord, tolerance):
        coord = sum(e.coord for e in group) / len(group)
        for e in group:
            if e.orientation == "v":
                out.append(Edge("v", coord, e.y0, coord, e.y1))
            else:
                out.append(Edge("h", e.x0, coord, e.x1, coord))
    return out


def _join(edges: list[Edge], tolerance: float) -> list[Edge]:
    by_coord: dict[float, list[Edge]] = defaultdict(list)
    for e in edges:
        by_coord[round(e.coord, 4)].append(e)
    out: list[Edge] = []
    for group in by_coord.values():
        group.sort(key=lambda e: e.start)
        current = group[0]
        for e in group[1:]:
            if e.start <= current.end + tolerance:
                if e.end > current.end:
                    if current.orientation == "v":
                        current = Edge("v", current.x0, current.y0, current.x1, e.end)
                    else:
                        current = Edge("h", current.x0, current.y0, e.end, current.y1)
            else:
                out.append(current)
                current = e
        out.append(current)
    return out


def _prepare(edges: list[Edge], snap_tol: float, join_tol: float, min_length: float) -> list[Edge]:
    edges = _snap(edges, snap_tol)
    edges = _join(edges, join_tol)
    return [e for e in edges if e.length >= min_length]


def _intersections(v_edges: list[Edge], h_edges: list[Edge], x_tol: float, y_tol: float):
    points: dict[tuple[float, float], tuple[set[int], set[int]]] = {}
    for vi, v in enumerate(v_edges):
        for hi, h in enumerate(h_edges):
            if (h.x0 - x_tol) <= v.x0 <= (h.x1 + x_tol) and (v.y0 - y_tol) <= h.y0 <= (v.y1 + y_tol):
                key = (round(v.x0, 3), round(h.y0, 3))
                vs, hs = points.setdefault(key, (set(), set()))
                vs.add(vi)
                hs.add(hi)
    return points


def _cells_from_points(points) -> list[tuple[float, float, float, float]]:
    xs_by_y: dict[float, list[float]] = defaultdict(list)
    ys_by_x: dict[float, list[float]] = defaultdict(list)
    for x, y in points:
        xs_by_y[y].append(x)
        ys_by_x[x].append(y)
    for lst in xs_by_y.values():
        lst.sort()
    for lst in ys_by_x.values():
        lst.sort()

    def share_h(a, b) -> bool:
        return bool(points[a][1] & points[b][1])

    def share_v(a, b) -> bool:
        return bool(points[a][0] & points[b][0])

    cells = []
    for x, y in sorted(points):
        rights = [rx for rx in xs_by_y[y] if rx > x and share_h((x, y), (rx, y))]
        belows = [by for by in ys_by_x[x] if by > y and share_v((x, y), (x, by))]
        found = None
        for rx in rights:
            for by in belows:
                corner = (rx, by)
                if corner in points and share_v((rx, y), corner) and share_h((x, by), corner):
                    found = (x, y, rx, by)
                    break
            if found:
                break
        if found:
            cells.append(found)
    return cells


def _group_cells(cells: list[tuple]) -> list[list[tuple]]:
    parent = list(range(len(cells)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    corner_index: dict[tuple[float, float], list[int]] = defaultdict(list)
    for i, (x0, y0, x1, y1) in enumerate(cells):
        for corner in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
            corner_index[corner].append(i)
    for members in corner_index.values():
        for other in members[1:]:
            union(members[0], other)
    groups: dict[int, list[tuple]] = defaultdict(list)
    for i, cell in enumerate(cells):
        groups[find(i)].append(cell)
    return [sorted(g, key=lambda cl: (cl[1], cl[0])) for g in groups.values()]


# --------------------------------------------------------------------------
# Result objects
# --------------------------------------------------------------------------


@dataclass
class TableRow:
    bbox: tuple
    cells: list


@dataclass
class TableHeader:
    bbox: tuple
    cells: list
    names: list
    external: bool = False


class Table:
    def __init__(self, finder: "TableFinder", cells: list[tuple]):
        self._finder = finder
        self.page = finder.page
        self.cells: list = []
        self.rows: list[TableRow] = []
        xs = sorted({c[0] for c in cells} | {c[2] for c in cells})
        ys = sorted({c[1] for c in cells} | {c[3] for c in cells})
        col_starts = xs[:-1]
        row_starts = ys[:-1]
        lookup = {(c[0], c[1]): c for c in cells}
        for top in row_starts:
            row_cells = []
            for left in col_starts:
                cell = lookup.get((left, top))
                row_cells.append(tuple(cell) if cell else None)
            present = [c for c in row_cells if c]
            if not present:
                continue
            bbox = (min(c[0] for c in present), top, max(c[2] for c in present), max(c[3] for c in present))
            self.rows.append(TableRow(bbox, row_cells))
            self.cells.extend(row_cells)
        self.row_count = len(self.rows)
        self.col_count = len(col_starts)
        self.bbox = (
            min(c[0] for c in cells),
            min(c[1] for c in cells),
            max(c[2] for c in cells),
            max(c[3] for c in cells),
        )
        self._extracted: list[list] | None = None
        self.header = self._make_header()

    def __repr__(self) -> str:
        return f"Table(bbox={self.bbox}, rows={self.row_count}, cols={self.col_count})"

    def extract(self) -> list[list]:
        if self._extracted is None:
            self._extracted = [
                [self._finder._cell_text(cell) if cell else None for cell in row.cells] for row in self.rows
            ]
        return [list(row) for row in self._extracted]

    def _make_header(self) -> TableHeader:
        if not self.rows:
            return TableHeader(self.bbox, [], [], False)
        first = self.rows[0]
        names = []
        for i, cell in enumerate(first.cells):
            text = self._finder._cell_text(cell) if cell else None
            if text is None or not text.strip():
                names.append(f"Col{i + 1}")
            else:
                names.append(text.replace("\n", " ").strip())
        seen: dict[str, int] = {}
        unique = []
        for name in names:
            if name in seen:
                seen[name] += 1
                unique.append(f"{name}-{seen[name]}")
            else:
                seen[name] = 0
                unique.append(name)
        return TableHeader(first.bbox, list(first.cells), unique, False)

    def to_markdown(self, clean: bool = True, fill_empty: bool = True) -> str:
        names = list(self.header.names)
        n = self.col_count

        def fmt(value) -> str:
            if value is None:
                return ""
            text = str(value)
            if clean:
                text = text.replace("|", "\\|")
                text = text.replace("\n", "<br>")
            return text

        lines = ["|" + "|".join(fmt(name) for name in names) + "|", "|" + "|".join(["---"] * n) + "|"]
        rows = self.extract()
        for row in rows[1:] if not self.header.external else rows:
            lines.append("|" + "|".join(fmt(cell) for cell in row) + "|")
        return "\n".join(lines) + "\n\n"

    def to_pandas(self):
        import pandas as pd

        rows = self.extract()
        data = rows if self.header.external else rows[1:]
        return pd.DataFrame(data, columns=self.header.names)


class TableFinder:
    def __init__(self, page, clip=None, strategy=None, add_lines=None, **kwargs):
        self.page = page
        self.settings = dict(DEFAULTS)
        unknown = set(kwargs) - set(DEFAULTS)
        if unknown:
            raise TypeError(f"unknown find_tables settings: {sorted(unknown)}")
        self.settings.update({k: v for k, v in kwargs.items() if v is not None})
        if strategy is not None:
            self.settings["vertical_strategy"] = strategy
            self.settings["horizontal_strategy"] = strategy
        for base in ("snap", "join", "intersection", "text"):
            for axis in ("x", "y"):
                key = f"{base}_{axis}_tolerance"
                if self.settings.get(key) is None:
                    self.settings[key] = self.settings[f"{base}_tolerance"]
        self.clip = Rect(clip) if clip is not None else None
        self._words = self._collect_words()
        self.edges = self._collect_edges(add_lines)
        self.tables: list[Table] = self._find()

    # -- sequence protocol -----------------------------------------------------
    def __len__(self) -> int:
        return len(self.tables)

    def __getitem__(self, index) -> Table:
        return self.tables[index]

    def __iter__(self):
        return iter(self.tables)

    @property
    def cells(self) -> list:
        return [cell for table in self.tables for cell in table.cells if cell]

    # -- pipeline --------------------------------------------------------------
    def _collect_words(self) -> list[Word]:
        words = []
        for x0, y0, x1, y1, text, *_rest in self.page.get_text("words"):
            word = Word(x0, y0, x1, y1, text)
            if self.clip is not None and not self.clip.contains((word.cx, word.cy)):
                continue
            words.append(word)
        return words

    def _collect_edges(self, add_lines) -> list[Edge]:
        s = self.settings
        page_rect = self.clip or self.page.rect
        edges: list[Edge] = []
        drawn: list[Edge] | None = None

        def drawings(strict: bool) -> list[Edge]:
            nonlocal drawn
            if drawn is None:
                drawn = _edges_from_drawings(self.page, self.clip, strict)
            return drawn

        for orientation, strategy in (("v", s["vertical_strategy"]), ("h", s["horizontal_strategy"])):
            if strategy in ("lines", "lines_strict"):
                edges.extend(e for e in drawings(strategy == "lines_strict") if e.orientation == orientation)
            elif strategy == "text":
                if orientation == "v":
                    edges.extend(_edges_from_words_v(self._words, s["min_words_vertical"], s["text_x_tolerance"]))
                else:
                    edges.extend(_edges_from_words_h(self._words, s["min_words_horizontal"], s["text_y_tolerance"]))
            elif strategy == "explicit":
                lines = s["vertical_lines"] if orientation == "v" else s["horizontal_lines"]
                edges.extend(_explicit_edges(lines, orientation, page_rect))
            else:
                raise ValueError(f"unknown strategy {strategy!r}")
        for line in add_lines or []:
            p1, p2 = line
            _add_line_edge(edges, p1[0], p1[1], p2[0], p2[1])
        v = [e for e in edges if e.orientation == "v"]
        h = [e for e in edges if e.orientation == "h"]
        v = _prepare(v, s["snap_x_tolerance"], s["join_y_tolerance"], s["edge_min_length"])
        h = _prepare(h, s["snap_y_tolerance"], s["join_x_tolerance"], s["edge_min_length"])
        return v + h

    def _find(self) -> list[Table]:
        s = self.settings
        v = [e for e in self.edges if e.orientation == "v"]
        h = [e for e in self.edges if e.orientation == "h"]
        if not v or not h:
            return []
        points = _intersections(v, h, s["intersection_x_tolerance"], s["intersection_y_tolerance"])
        cells = _cells_from_points(points)
        page_rect = self.page.rect
        # A "cell" that is practically the page itself is a background, not a table.
        cells = [
            cl
            for cl in cells
            if not ((cl[2] - cl[0]) >= 0.9 * page_rect.width and (cl[3] - cl[1]) >= 0.9 * page_rect.height)
        ]
        tables = []
        for group in _group_cells(cells):
            if len(group) < 2:
                continue
            tables.append(Table(self, group))
        tables.sort(key=lambda t: (t.bbox[1], t.bbox[0]))
        return tables

    # -- text ------------------------------------------------------------------
    def _cell_text(self, cell) -> str:
        if cell is None:
            return ""
        x0, y0, x1, y1 = cell
        inside = [w for w in self._words if x0 <= w.cx <= x1 and y0 <= w.cy <= y1]
        if not inside:
            return ""
        lines = []
        for group in _cluster_objects(inside, lambda w: w.cy, self.settings["text_y_tolerance"]):
            group.sort(key=lambda w: w.x0)
            lines.append(" ".join(w.text for w in group))
        return "\n".join(lines)
