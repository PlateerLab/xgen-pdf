"""Geometry primitives: Point, Rect, IRect, Matrix, Quad.

Coordinates follow the convention consumers already rely on: the page origin
is the top-left corner, y grows downward, units are PDF points.  Rect is a
mutable 4-float box; Matrix is a 6-float affine transform ``(a, b, c, d, e, f)``
mapping ``(x, y)`` to ``(a*x + c*y + e, b*x + d*y + f)``.
"""

from __future__ import annotations

import math
from typing import Iterator, Sequence

__all__ = ["Point", "Rect", "IRect", "Matrix", "Quad", "Identity", "EPSILON", "FZ_MAX"]

EPSILON = 1e-5
FZ_MAX = 2147483647.0
FZ_MIN = -2147483648.0


def _num(value, name: str = "value") -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number, got {value!r}") from None


class Point:
    __slots__ = ("x", "y")

    def __init__(self, *args):
        if len(args) == 0:
            self.x, self.y = 0.0, 0.0
        elif len(args) == 1:
            other = args[0]
            if isinstance(other, Point):
                self.x, self.y = other.x, other.y
            else:
                seq = list(other)
                if len(seq) != 2:
                    raise ValueError("Point needs two coordinates")
                self.x, self.y = _num(seq[0], "x"), _num(seq[1], "y")
        elif len(args) == 2:
            self.x, self.y = _num(args[0], "x"), _num(args[1], "y")
        else:
            raise ValueError("Point takes 0, 1 or 2 arguments")

    def __iter__(self) -> Iterator[float]:
        yield self.x
        yield self.y

    def __len__(self) -> int:
        return 2

    def __getitem__(self, index: int) -> float:
        return (self.x, self.y)[index]

    def __setitem__(self, index: int, value: float) -> None:
        if index in (0, -2):
            self.x = _num(value)
        elif index in (1, -1):
            self.y = _num(value)
        else:
            raise IndexError(index)

    def __eq__(self, other) -> bool:
        try:
            other = Point(other)
        except Exception:
            return False
        return abs(self.x - other.x) < EPSILON and abs(self.y - other.y) < EPSILON

    def __hash__(self) -> int:
        return hash((round(self.x, 5), round(self.y, 5)))

    def __repr__(self) -> str:
        return f"Point({self.x}, {self.y})"

    def __add__(self, other) -> "Point":
        if isinstance(other, (int, float)):
            return Point(self.x + other, self.y + other)
        other = Point(other)
        return Point(self.x + other.x, self.y + other.y)

    __radd__ = __add__

    def __sub__(self, other) -> "Point":
        if isinstance(other, (int, float)):
            return Point(self.x - other, self.y - other)
        other = Point(other)
        return Point(self.x - other.x, self.y - other.y)

    def __neg__(self) -> "Point":
        return Point(-self.x, -self.y)

    def __mul__(self, other) -> "Point":
        if isinstance(other, (int, float)):
            return Point(self.x * other, self.y * other)
        return self.transform(other)

    __rmul__ = __mul__

    def __truediv__(self, other) -> "Point":
        if isinstance(other, (int, float)):
            return Point(self.x / other, self.y / other)
        return self.transform(~Matrix(other))

    def __abs__(self) -> float:
        return math.hypot(self.x, self.y)

    def __bool__(self) -> bool:
        return not (self.x == 0 and self.y == 0)

    @property
    def abs_unit(self) -> "Point":
        length = abs(self)
        if length == 0:
            return Point(0, 0)
        return Point(abs(self.x) / length, abs(self.y) / length)

    @property
    def unit(self) -> "Point":
        length = abs(self)
        if length == 0:
            return Point(0, 0)
        return Point(self.x / length, self.y / length)

    def distance_to(self, other, unit: str = "px") -> float:
        if isinstance(other, Rect):
            rect = Rect(other)
            dx = max(rect.x0 - self.x, 0, self.x - rect.x1)
            dy = max(rect.y0 - self.y, 0, self.y - rect.y1)
            dist = math.hypot(dx, dy)
        else:
            other = Point(other)
            dist = math.hypot(self.x - other.x, self.y - other.y)
        return _convert_unit(dist, unit)

    def transform(self, matrix) -> "Point":
        m = Matrix(matrix)
        self.x, self.y = m.a * self.x + m.c * self.y + m.e, m.b * self.x + m.d * self.y + m.f
        return self


def _convert_unit(value: float, unit: str) -> float:
    factors = {"px": 1.0, "in": 1 / 72.0, "cm": 2.54 / 72.0, "mm": 25.4 / 72.0}
    if unit not in factors:
        raise ValueError(f"unknown unit {unit!r}")
    return value * factors[unit]


class Rect:
    """Mutable axis-aligned rectangle ``(x0, y0, x1, y1)``.

    An empty rectangle has ``x0 >= x1`` or ``y0 >= y1``.  ``Rect()`` is the
    all-zero rectangle, which is also empty.  The infinite rectangle is
    ``Rect(FZ_MIN, FZ_MIN, FZ_MAX, FZ_MAX)``.
    """

    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, *args, **kwargs):
        if kwargs:
            raise TypeError("Rect takes only positional arguments")
        if len(args) == 0:
            values = (0.0, 0.0, 0.0, 0.0)
        elif len(args) == 1:
            other = args[0]
            if isinstance(other, (Rect, IRect)):
                values = (other.x0, other.y0, other.x1, other.y1)
            elif isinstance(other, Quad):
                values = tuple(other.rect)
            else:
                seq = list(other)
                if len(seq) != 4:
                    raise ValueError("Rect needs four numbers")
                values = tuple(seq)
        elif len(args) == 2:
            p1, p2 = Point(args[0]), Point(args[1])
            values = (p1.x, p1.y, p2.x, p2.y)
        elif len(args) == 4:
            values = tuple(args)
        else:
            raise ValueError("Rect takes 0, 1, 2 or 4 arguments")
        self.x0, self.y0, self.x1, self.y1 = (
            _num(values[0], "x0"),
            _num(values[1], "y0"),
            _num(values[2], "x1"),
            _num(values[3], "y1"),
        )

    # -- sequence protocol -------------------------------------------------
    def __iter__(self) -> Iterator[float]:
        yield self.x0
        yield self.y0
        yield self.x1
        yield self.y1

    def __len__(self) -> int:
        return 4

    def __getitem__(self, index):
        return (self.x0, self.y0, self.x1, self.y1)[index]

    def __setitem__(self, index: int, value: float) -> None:
        names = ("x0", "y0", "x1", "y1")
        setattr(self, names[index], _num(value))

    def __eq__(self, other) -> bool:
        try:
            other = Rect(other)
        except Exception:
            return False
        return all(abs(a - b) < EPSILON for a, b in zip(self, other))

    def __hash__(self) -> int:
        return hash(tuple(round(v, 5) for v in self))

    def __repr__(self) -> str:
        return f"Rect({self.x0}, {self.y0}, {self.x1}, {self.y1})"

    def __bool__(self) -> bool:
        return not (self.x0 == 0 and self.y0 == 0 and self.x1 == 0 and self.y1 == 0)

    def __abs__(self) -> float:
        if self.is_empty or self.is_infinite:
            return 0.0
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    # -- properties ----------------------------------------------------------
    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)

    @property
    def is_empty(self) -> bool:
        return self.x0 >= self.x1 or self.y0 >= self.y1

    @property
    def is_infinite(self) -> bool:
        return self.x0 == FZ_MIN and self.y0 == FZ_MIN and self.x1 == FZ_MAX and self.y1 == FZ_MAX

    @property
    def is_valid(self) -> bool:
        return self.x0 <= self.x1 and self.y0 <= self.y1

    @property
    def top_left(self) -> Point:
        return Point(self.x0, self.y0)

    @property
    def top_right(self) -> Point:
        return Point(self.x1, self.y0)

    @property
    def bottom_left(self) -> Point:
        return Point(self.x0, self.y1)

    @property
    def bottom_right(self) -> Point:
        return Point(self.x1, self.y1)

    tl = top_left
    tr = top_right
    bl = bottom_left
    br = bottom_right

    @property
    def irect(self) -> "IRect":
        return IRect(self)

    @property
    def quad(self) -> "Quad":
        return Quad(self.tl, self.tr, self.bl, self.br)

    # -- operations ----------------------------------------------------------
    def get_area(self, unit: str = "px") -> float:
        if self.is_empty or self.is_infinite:
            return 0.0
        area = abs(self.x1 - self.x0) * abs(self.y1 - self.y0)
        factor = _convert_unit(1.0, unit)
        return area * factor * factor

    def normalize(self) -> "Rect":
        if self.x1 < self.x0:
            self.x0, self.x1 = self.x1, self.x0
        if self.y1 < self.y0:
            self.y0, self.y1 = self.y1, self.y0
        return self

    def round(self) -> "IRect":
        return IRect(self)

    def intersect(self, other) -> "Rect":
        other = Rect(other)
        if self.is_infinite:
            self.x0, self.y0, self.x1, self.y1 = other.x0, other.y0, other.x1, other.y1
            return self
        if other.is_infinite:
            return self
        if self.is_empty or other.is_empty:
            self.x0 = self.y0 = self.x1 = self.y1 = 0.0
            return self
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        x1, y1 = min(self.x1, other.x1), min(self.y1, other.y1)
        if x0 >= x1 or y0 >= y1:
            self.x0 = self.y0 = self.x1 = self.y1 = 0.0
        else:
            self.x0, self.y0, self.x1, self.y1 = x0, y0, x1, y1
        return self

    def include_rect(self, other) -> "Rect":
        """Grow to also contain ``other``.  The all-zero ``Rect()`` acts as the
        neutral element, so unions can be accumulated from it."""
        other = Rect(other).normalize()
        if not other:
            return self
        if other.is_infinite:
            self.x0, self.y0, self.x1, self.y1 = other.x0, other.y0, other.x1, other.y1
            return self
        if not self:
            self.x0, self.y0, self.x1, self.y1 = other.x0, other.y0, other.x1, other.y1
            return self
        if self.is_infinite:
            return self
        self.normalize()
        self.x0, self.y0 = min(self.x0, other.x0), min(self.y0, other.y0)
        self.x1, self.y1 = max(self.x1, other.x1), max(self.y1, other.y1)
        return self

    def include_point(self, point) -> "Rect":
        point = Point(point)
        if not self:
            self.x0 = self.x1 = point.x
            self.y0 = self.y1 = point.y
            return self
        if self.is_infinite:
            return self
        self.normalize()
        self.x0, self.y0 = min(self.x0, point.x), min(self.y0, point.y)
        self.x1, self.y1 = max(self.x1, point.x), max(self.y1, point.y)
        return self

    def intersects(self, other) -> bool:
        other = Rect(other)
        if self.is_empty or other.is_empty:
            return False
        if self.is_infinite or other.is_infinite:
            return True
        x0, y0 = max(self.x0, other.x0), max(self.y0, other.y0)
        x1, y1 = min(self.x1, other.x1), min(self.y1, other.y1)
        return x0 < x1 and y0 < y1

    def contains(self, other) -> bool:
        if isinstance(other, (int, float)):
            return other in tuple(self)
        if isinstance(other, Point) or (isinstance(other, Sequence) and len(other) == 2):
            p = Point(other)
            return self.x0 <= p.x <= self.x1 and self.y0 <= p.y <= self.y1
        other = Rect(other)
        if other.is_empty:
            return True
        if self.is_empty:
            return False
        return self.x0 <= other.x0 and self.y0 <= other.y0 and other.x1 <= self.x1 and other.y1 <= self.y1

    __contains__ = contains

    def transform(self, matrix) -> "Rect":
        if self.is_infinite:
            return self
        m = Matrix(matrix)
        corners = [
            Point(self.x0, self.y0).transform(m),
            Point(self.x1, self.y0).transform(m),
            Point(self.x0, self.y1).transform(m),
            Point(self.x1, self.y1).transform(m),
        ]
        self.x0 = min(p.x for p in corners)
        self.y0 = min(p.y for p in corners)
        self.x1 = max(p.x for p in corners)
        self.y1 = max(p.y for p in corners)
        return self

    def torect(self, other) -> "Matrix":
        """Matrix mapping this rectangle onto ``other``."""
        other = Rect(other)
        if self.is_empty or other.is_empty:
            raise ValueError("rectangles must not be empty")
        sx = other.width / self.width
        sy = other.height / self.height
        return Matrix(sx, 0, 0, sy, other.x0 - self.x0 * sx, other.y0 - self.y0 * sy)

    # -- operators -----------------------------------------------------------
    def __and__(self, other) -> "Rect":
        return Rect(self).intersect(other)

    def __or__(self, other) -> "Rect":
        if isinstance(other, Point) or (isinstance(other, Sequence) and len(other) == 2):
            return Rect(self).include_point(other)
        return Rect(self).include_rect(other)

    def __add__(self, other) -> "Rect":
        if isinstance(other, (int, float)):
            return Rect(self.x0 + other, self.y0 + other, self.x1 + other, self.y1 + other)
        other = Rect(other)
        return Rect(self.x0 + other.x0, self.y0 + other.y0, self.x1 + other.x1, self.y1 + other.y1)

    def __sub__(self, other) -> "Rect":
        if isinstance(other, (int, float)):
            return Rect(self.x0 - other, self.y0 - other, self.x1 - other, self.y1 - other)
        other = Rect(other)
        return Rect(self.x0 - other.x0, self.y0 - other.y0, self.x1 - other.x1, self.y1 - other.y1)

    def __mul__(self, other) -> "Rect":
        if isinstance(other, (int, float)):
            return Rect(self.x0 * other, self.y0 * other, self.x1 * other, self.y1 * other)
        return Rect(self).transform(other)

    def __truediv__(self, other) -> "Rect":
        if isinstance(other, (int, float)):
            return Rect(self.x0 / other, self.y0 / other, self.x1 / other, self.y1 / other)
        return Rect(self).transform(~Matrix(other))

    def __neg__(self) -> "Rect":
        return Rect(-self.x0, -self.y0, -self.x1, -self.y1)

    def __pos__(self) -> "Rect":
        return Rect(self)


class IRect:
    """Integer rectangle. ``IRect(rect)`` rounds outward (floor/ceil)."""

    __slots__ = ("x0", "y0", "x1", "y1")

    def __init__(self, *args):
        if len(args) == 0:
            values = (0, 0, 0, 0)
        elif len(args) == 1:
            other = args[0]
            if isinstance(other, IRect):
                values = (other.x0, other.y0, other.x1, other.y1)
            else:
                rect = Rect(other)
                values = (
                    math.floor(rect.x0 + EPSILON),
                    math.floor(rect.y0 + EPSILON),
                    math.ceil(rect.x1 - EPSILON),
                    math.ceil(rect.y1 - EPSILON),
                )
        elif len(args) == 2:
            p1, p2 = Point(args[0]), Point(args[1])
            values = (math.floor(p1.x), math.floor(p1.y), math.ceil(p2.x), math.ceil(p2.y))
        elif len(args) == 4:
            rect = Rect(*args)
            values = (
                math.floor(rect.x0 + EPSILON),
                math.floor(rect.y0 + EPSILON),
                math.ceil(rect.x1 - EPSILON),
                math.ceil(rect.y1 - EPSILON),
            )
        else:
            raise ValueError("IRect takes 0, 1, 2 or 4 arguments")
        self.x0, self.y0, self.x1, self.y1 = (int(v) for v in values)

    def __iter__(self) -> Iterator[int]:
        yield self.x0
        yield self.y0
        yield self.x1
        yield self.y1

    def __len__(self) -> int:
        return 4

    def __getitem__(self, index):
        return (self.x0, self.y0, self.x1, self.y1)[index]

    def __eq__(self, other) -> bool:
        try:
            other = IRect(other)
        except Exception:
            return False
        return tuple(self) == tuple(other)

    def __hash__(self) -> int:
        return hash(tuple(self))

    def __repr__(self) -> str:
        return f"IRect({self.x0}, {self.y0}, {self.x1}, {self.y1})"

    def __bool__(self) -> bool:
        return not (self.x0 == 0 and self.y0 == 0 and self.x1 == 0 and self.y1 == 0)

    def __abs__(self) -> float:
        return float(abs(Rect(self)))

    @property
    def width(self) -> int:
        return max(0, self.x1 - self.x0)

    @property
    def height(self) -> int:
        return max(0, self.y1 - self.y0)

    @property
    def is_empty(self) -> bool:
        return self.x0 >= self.x1 or self.y0 >= self.y1

    @property
    def is_infinite(self) -> bool:
        return Rect(self).is_infinite

    @property
    def rect(self) -> Rect:
        return Rect(self)

    @property
    def top_left(self) -> Point:
        return Point(self.x0, self.y0)

    @property
    def bottom_right(self) -> Point:
        return Point(self.x1, self.y1)

    tl = top_left
    br = bottom_right

    def get_area(self, unit: str = "px") -> float:
        return Rect(self).get_area(unit)

    def intersects(self, other) -> bool:
        return Rect(self).intersects(other)

    def contains(self, other) -> bool:
        return Rect(self).contains(other)

    __contains__ = contains

    def __and__(self, other) -> "IRect":
        return IRect(Rect(self) & other)

    def __or__(self, other) -> "IRect":
        return IRect(Rect(self) | other)

    def __add__(self, other) -> "IRect":
        return IRect(Rect(self) + other)

    def __sub__(self, other) -> "IRect":
        return IRect(Rect(self) - other)

    def __mul__(self, other) -> "IRect":
        return IRect(Rect(self) * other)


class Matrix:
    __slots__ = ("a", "b", "c", "d", "e", "f")

    def __init__(self, *args):
        if len(args) == 0:
            values = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        elif len(args) == 1:
            other = args[0]
            if isinstance(other, Matrix):
                values = (other.a, other.b, other.c, other.d, other.e, other.f)
            elif isinstance(other, (int, float)):
                values = (float(other), 0.0, 0.0, float(other), 0.0, 0.0)
            else:
                seq = list(other)
                if len(seq) != 6:
                    raise ValueError("Matrix needs six numbers")
                values = tuple(seq)
        elif len(args) == 2:
            values = (float(args[0]), 0.0, 0.0, float(args[1]), 0.0, 0.0)
        elif len(args) == 6:
            values = tuple(args)
        else:
            raise ValueError("Matrix takes 0, 1, 2 or 6 arguments")
        self.a, self.b, self.c, self.d, self.e, self.f = (_num(v) for v in values)

    def __iter__(self) -> Iterator[float]:
        yield from (self.a, self.b, self.c, self.d, self.e, self.f)

    def __len__(self) -> int:
        return 6

    def __getitem__(self, index):
        return (self.a, self.b, self.c, self.d, self.e, self.f)[index]

    def __eq__(self, other) -> bool:
        try:
            other = Matrix(other)
        except Exception:
            return False
        return all(abs(x - y) < EPSILON for x, y in zip(self, other))

    def __hash__(self) -> int:
        return hash(tuple(round(v, 5) for v in self))

    def __repr__(self) -> str:
        return f"Matrix({self.a}, {self.b}, {self.c}, {self.d}, {self.e}, {self.f})"

    def __bool__(self) -> bool:
        return any(v != 0 for v in self)

    @property
    def is_rectilinear(self) -> bool:
        return (abs(self.b) < EPSILON and abs(self.c) < EPSILON) or (abs(self.a) < EPSILON and abs(self.d) < EPSILON)

    def concat(self, m1, m2) -> "Matrix":
        """Set self to ``m1 * m2`` (apply m1 first, then m2)."""
        m1, m2 = Matrix(m1), Matrix(m2)
        self.a = m1.a * m2.a + m1.b * m2.c
        self.b = m1.a * m2.b + m1.b * m2.d
        self.c = m1.c * m2.a + m1.d * m2.c
        self.d = m1.c * m2.b + m1.d * m2.d
        self.e = m1.e * m2.a + m1.f * m2.c + m2.e
        self.f = m1.e * m2.b + m1.f * m2.d + m2.f
        return self

    def __mul__(self, other) -> "Matrix":
        if isinstance(other, (int, float)):
            return Matrix(*(v * other for v in self))
        return Matrix().concat(self, other)

    def __truediv__(self, other) -> "Matrix":
        if isinstance(other, (int, float)):
            return Matrix(*(v / other for v in self))
        return Matrix().concat(self, ~Matrix(other))

    def __invert__(self) -> "Matrix":
        det = self.a * self.d - self.b * self.c
        if abs(det) < 1e-12:
            return Matrix(0, 0, 0, 0, 0, 0)
        a = self.d / det
        b = -self.b / det
        c = -self.c / det
        d = self.a / det
        e = -(self.e * a + self.f * c)
        f = -(self.e * b + self.f * d)
        return Matrix(a, b, c, d, e, f)

    def invert(self, src=None) -> int:
        source = Matrix(src) if src is not None else Matrix(self)
        det = source.a * source.d - source.b * source.c
        if abs(det) < 1e-12:
            return 1
        inv = ~source
        self.a, self.b, self.c, self.d, self.e, self.f = tuple(inv)
        return 0

    def prescale(self, sx: float, sy: float) -> "Matrix":
        self.a *= sx
        self.b *= sx
        self.c *= sy
        self.d *= sy
        return self

    def preshear(self, h: float, v: float) -> "Matrix":
        a, b = self.a, self.b
        self.a += v * self.c
        self.b += v * self.d
        self.c += h * a
        self.d += h * b
        return self

    def prerotate(self, theta: float) -> "Matrix":
        theta = theta % 360
        if theta == 0:
            return self
        if theta == 90:
            s, c = 1.0, 0.0
        elif theta == 180:
            s, c = 0.0, -1.0
        elif theta == 270:
            s, c = -1.0, 0.0
        else:
            rad = math.radians(theta)
            s, c = math.sin(rad), math.cos(rad)
        a, b = self.a, self.b
        self.a = c * a + s * self.c
        self.b = c * b + s * self.d
        self.c = -s * a + c * self.c
        self.d = -s * b + c * self.d
        return self

    def pretranslate(self, tx: float, ty: float) -> "Matrix":
        self.e += tx * self.a + ty * self.c
        self.f += tx * self.b + ty * self.d
        return self

    def norm(self) -> float:
        return math.sqrt(sum(v * v for v in self))


class Quad:
    __slots__ = ("ul", "ur", "ll", "lr")

    def __init__(self, *args):
        if len(args) == 0:
            self.ul, self.ur, self.ll, self.lr = Point(), Point(), Point(), Point()
        elif len(args) == 1:
            other = args[0]
            if isinstance(other, Quad):
                self.ul, self.ur, self.ll, self.lr = (
                    Point(other.ul),
                    Point(other.ur),
                    Point(other.ll),
                    Point(other.lr),
                )
            elif isinstance(other, (Rect, IRect)):
                rect = Rect(other)
                self.ul, self.ur, self.ll, self.lr = rect.tl, rect.tr, rect.bl, rect.br
            else:
                seq = list(other)
                if len(seq) != 4:
                    raise ValueError("Quad needs four points")
                self.ul, self.ur, self.ll, self.lr = (Point(p) for p in seq)
        elif len(args) == 4:
            self.ul, self.ur, self.ll, self.lr = (Point(p) for p in args)
        else:
            raise ValueError("Quad takes 0, 1 or 4 arguments")

    def __iter__(self):
        yield from (self.ul, self.ur, self.ll, self.lr)

    def __len__(self) -> int:
        return 4

    def __getitem__(self, index):
        return (self.ul, self.ur, self.ll, self.lr)[index]

    def __repr__(self) -> str:
        return f"Quad({self.ul}, {self.ur}, {self.ll}, {self.lr})"

    @property
    def rect(self) -> Rect:
        xs = [p.x for p in self]
        ys = [p.y for p in self]
        return Rect(min(xs), min(ys), max(xs), max(ys))

    @property
    def width(self) -> float:
        return abs(self.ur - self.ul)

    @property
    def height(self) -> float:
        return abs(self.ll - self.ul)

    @property
    def is_empty(self) -> bool:
        return self.width < EPSILON or self.height < EPSILON

    @property
    def is_rectangular(self) -> bool:
        return self.rect.get_area() - self.width * self.height < EPSILON

    def transform(self, matrix) -> "Quad":
        m = Matrix(matrix)
        for p in self:
            p.transform(m)
        return self

    def __mul__(self, other) -> "Quad":
        return Quad(self).transform(other)


Identity = Matrix(1, 0, 0, 1, 0, 0)
