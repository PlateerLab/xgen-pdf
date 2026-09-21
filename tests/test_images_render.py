import io

from PIL import Image

import xgen_pdf as xp
from xgen_pdf import IRect, Matrix, Rect


def test_get_images_and_extract(korean):
    page = korean[0]
    images = page.get_images()
    assert len(images) == 1
    xref = images[0][0]
    assert images[0][2] == 80 and images[0][3] == 60
    info = korean.extract_image(xref)
    assert info["width"] == 80 and info["height"] == 60
    assert info["ext"] in ("png", "jpeg")
    pil = Image.open(io.BytesIO(info["image"]))
    assert pil.size == (80, 60)
    assert pil.convert("RGB").getpixel((10, 10)) == (30, 120, 200)
    full = page.get_images(full=True)
    assert len(full[0]) == 10


def test_image_info_and_rects(korean):
    page = korean[0]
    info = page.get_image_info(xrefs=True)
    assert len(info) == 1
    entry = info[0]
    x0, y0, x1, y1 = entry["bbox"]
    assert abs(x0 - 300) < 1 and abs(y0 - 400) < 1 and abs(x1 - 380) < 1 and abs(y1 - 460) < 1
    assert entry["xref"] == page.get_images()[0][0]
    rects = page.get_image_rects(entry["xref"])
    assert len(rects) == 1 and abs(Rect(entry["bbox"]) & rects[0]) > 4700
    rects_t = page.get_image_rects(entry["xref"], transform=True)
    assert isinstance(rects_t[0][1], Matrix)
    hashed = page.get_image_info(hashes=True)
    assert isinstance(hashed[0]["digest"], bytes)


def test_get_drawings_items(korean):
    drawings = korean[0].get_drawings()
    # 8 table rules + 1 filled rectangle
    assert len(drawings) == 9
    lines = [d for d in drawings if d["items"][0][0] == "l"]
    rects = [d for d in drawings if d["items"][0][0] == "re"]
    assert len(lines) == 8 and len(rects) == 1
    line = lines[0]
    assert line["type"] == "s" and line["color"] == (0.0, 0.0, 0.0) and line["fill"] is None
    assert abs(line["width"] - 0.8) < 0.05
    p1, p2 = line["items"][0][1], line["items"][0][2]
    assert hasattr(p1, "x") and hasattr(p2, "y")
    filled = rects[0]
    assert filled["type"] == "fs"
    assert all(abs(v - 0.9) < 0.01 for v in filled["fill"])
    assert filled["color"] == (0.0, 0.0, 0.0)
    assert Rect(filled["rect"]) == Rect(40, 400, 200, 440)
    assert isinstance(filled["items"][0][1], Rect)
    for key in (
        "items",
        "type",
        "even_odd",
        "fill_opacity",
        "fill",
        "rect",
        "seqno",
        "closePath",
        "color",
        "width",
        "dashes",
        "stroke_opacity",
    ):
        assert key in filled


def test_cdrawings_are_plain_tuples(korean):
    cd = korean[0].get_cdrawings()
    assert len(cd) == 9
    assert isinstance(cd[0]["rect"], tuple)
    assert isinstance(cd[0]["items"][0][1], tuple)


def test_pixmap_rendering_and_output(korean, tmp_path):
    page = korean[0]
    pix = page.get_pixmap(matrix=Matrix(2, 2))
    assert (pix.width, pix.height) == (1000, 1400)
    assert pix.n == 3 and pix.alpha == 0
    assert pix.samples[:3] == b"\xff\xff\xff"
    png = pix.tobytes("png")
    assert png.startswith(b"\x89PNG")
    im = Image.open(io.BytesIO(png))
    assert im.size == (1000, 1400)
    # the blue image sits at (300..380, 400..460) in points
    assert im.convert("RGB").getpixel((680, 860)) == (30, 120, 200)
    jpg = pix.tobytes("jpeg")
    assert jpg.startswith(b"\xff\xd8")
    out = tmp_path / "page.png"
    pix.save(out)
    assert Image.open(out).size == (1000, 1400)
    assert pix.pil_image().size == (1000, 1400)


def test_pixmap_dpi_clip_and_alpha(korean):
    page = korean[0]
    pix = page.get_pixmap(dpi=144)
    assert (pix.width, pix.height) == (1000, 1400)
    clip = page.get_pixmap(matrix=Matrix(3, 3), clip=Rect(300, 400, 380, 460), alpha=False)
    assert (clip.width, clip.height) == (240, 180)
    assert tuple(clip.irect) == (900, 1200, 1140, 1380)
    assert clip.pil_image().convert("RGB").getpixel((120, 90)) == (30, 120, 200)
    alpha = page.get_pixmap(alpha=True)
    assert alpha.n == 4 and alpha.alpha == 1
    assert alpha.pil_image().getpixel((5, 5))[3] == 0  # transparent background


def test_pixmap_constructors():
    pix = xp.Pixmap(xp.csRGB, IRect(0, 0, 20, 10), False)
    assert (pix.width, pix.height, pix.n) == (20, 10, 3)
    pix.clear_with(200)
    assert pix.pixel(1, 1) == (200, 200, 200)
    data = pix.tobytes("png")
    again = xp.Pixmap(data)
    assert (again.width, again.height) == (20, 10)
    gray = xp.Pixmap(xp.csGRAY, pix)
    assert gray.n == 1 and gray.is_monochrome
