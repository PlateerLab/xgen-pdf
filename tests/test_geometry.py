import math

from xgen_pdf import IRect, Matrix, Point, Quad, Rect


def test_rect_construction_forms():
    assert tuple(Rect()) == (0, 0, 0, 0)
    assert tuple(Rect(1, 2, 3, 4)) == (1, 2, 3, 4)
    assert tuple(Rect((1, 2, 3, 4))) == (1, 2, 3, 4)
    assert tuple(Rect(Point(1, 2), Point(3, 4))) == (1, 2, 3, 4)
    assert tuple(Rect(Rect(1, 2, 3, 4))) == (1, 2, 3, 4)
    assert tuple(Rect(IRect(1, 2, 3, 4))) == (1, 2, 3, 4)


def test_rect_properties_and_area():
    r = Rect(10, 20, 30, 60)
    assert r.width == 20 and r.height == 40
    assert r.get_area() == 800 and abs(r) == 800
    assert not r.is_empty and not r.is_infinite
    assert Rect().is_empty and not Rect()
    assert Rect(5, 5, 5, 9).is_empty
    assert r.tl == Point(10, 20) and r.br == Point(30, 60)
    assert r.irect == IRect(10, 20, 30, 60)
    assert r.quad.rect == r


def test_rect_intersection_union_and_tests():
    a = Rect(0, 0, 10, 10)
    b = Rect(5, 5, 20, 20)
    assert a & b == Rect(5, 5, 10, 10)
    assert a | b == Rect(0, 0, 20, 20)
    assert a.intersects(b) and not a.intersects(Rect(11, 11, 12, 12))
    assert (a & Rect(11, 11, 12, 12)).is_empty
    assert a.contains(Rect(1, 1, 2, 2)) and not a.contains(b)
    assert (3, 4) in a and Point(50, 50) not in a
    assert Rect(1, 1, 2, 2) in a
    assert abs(a & b) == 25.0


def test_rect_is_mutable_and_supports_arithmetic():
    r = Rect(0, 0, 10, 10)
    r.y1 = 5
    assert r.height == 5
    assert r + 1 == Rect(1, 1, 11, 6)
    assert r * 2 == Rect(0, 0, 20, 10)
    assert Rect(10, 10, 0, 0).normalize() == Rect(0, 0, 10, 10)
    assert Rect(3, 3, 1, 1).get_area() == 0  # not normalised: empty


def test_include_rect_accumulates_from_empty():
    acc = Rect()
    for box in (Rect(5, 5, 6, 6), Rect(1, 1, 2, 2), Rect(3, 3, 3, 3)):
        acc.include_rect(box)
    assert acc == Rect(1, 1, 6, 6)
    acc = Rect().include_point((4, 4)).include_point((2, 9))
    assert acc == Rect(2, 4, 4, 9)


def test_irect_rounds_outward():
    assert tuple(IRect(Rect(0.2, 0.7, 9.1, 9.9))) == (0, 0, 10, 10)
    assert IRect(1, 1, 5, 5).width == 4


def test_matrix_forms_and_composition():
    assert tuple(Matrix()) == (1, 0, 0, 1, 0, 0)
    assert tuple(Matrix(2)) == (2, 0, 0, 2, 0, 0)
    assert tuple(Matrix(2, 3)) == (2, 0, 0, 3, 0, 0)
    scale = Matrix(2, 2)
    shift = Matrix(1, 0, 0, 1, 10, 20)
    combined = scale * shift  # scale first, then shift
    assert Point(1, 1) * combined == Point(12, 22)
    assert (~combined) * combined == Matrix()
    assert Rect(0, 0, 1, 1) * combined == Rect(10, 20, 12, 22)
    rot = Matrix().prerotate(90)
    assert Point(1, 0) * rot == Point(0, 1)
    assert Matrix(1, 0, 0, 1, 0, 0).is_rectilinear
    assert math.isclose(Matrix(3, 4, 0, 0, 0, 0).norm(), 5.0)


def test_point_arithmetic():
    assert Point(1, 2) + Point(3, 4) == Point(4, 6)
    assert Point(3, 4).distance_to(Point(0, 0)) == 5
    assert abs(Point(3, 4)) == 5
    assert tuple(Point((7, 8))) == (7, 8)
    assert Point(1, 1).distance_to(Rect(2, 1, 4, 4)) == 1


def test_quad_from_rect():
    q = Quad(Rect(0, 0, 2, 4))
    assert q.width == 2 and q.height == 4 and q.is_rectangular
    assert q.rect == Rect(0, 0, 2, 4)
