import io

from PIL import Image

import xgen_pdf as xp
from xgen_pdf import IRect, Rect


def test_new_document_pages_text_lines_and_images(tmp_path):
    doc = xp.open()
    page = doc.new_page()
    assert tuple(page.rect) == (0, 0, 595, 842)
    page.insert_text((72, 72), "Annex 1 condensation criteria")
    page.insert_text((72, 120), "Region I wall junction 0.25")
    drawing = doc.new_page()
    for i in range(60):
        drawing.draw_line((50, 50 + i * 10), (500, 50 + i * 10))
    scan = doc.new_page()
    pix = xp.Pixmap(xp.csRGB, IRect(0, 0, 200, 280), False)
    pix.clear_with(200)
    scan.insert_image(scan.rect, pixmap=pix)
    doc.new_page()  # empty
    small = doc.new_page(width=300, height=200)
    img = Image.new("RGB", (120, 80), (255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    small.insert_image(Rect(10, 10, 290, 190), stream=buf.getvalue())

    path = tmp_path / "authored.pdf"
    doc.save(path)
    data = doc.tobytes()
    doc.close()

    again = xp.open(path)
    assert again.page_count == 5
    assert again[0].get_text("text").splitlines() == ["Annex 1 condensation criteria", "Region I wall junction 0.25"]
    assert len(again[1].get_drawings()) == 60
    assert again[1].get_text("text").strip() == ""
    assert len(again[2].get_images()) == 1
    coverage = abs(Rect(again[2].get_image_info()[0]["bbox"]) & again[2].rect) / abs(again[2].rect)
    assert coverage > 0.95
    assert again[3].get_text("text") == "" and again[3].get_images() == [] and again[3].get_drawings() == []
    assert tuple(again[4].rect) == (0, 0, 300, 200)
    info = again.extract_image(again[4].get_images()[0][0])
    assert Image.open(io.BytesIO(info["image"])).convert("RGB").getpixel((5, 5)) == (255, 0, 0)
    assert xp.open(stream=data, filetype="pdf").page_count == 5


def test_insert_jpeg_and_rect(tmp_path):
    doc = xp.open()
    page = doc.new_page(width=200, height=200)
    img = Image.new("RGB", (50, 50), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    page.insert_image(Rect(20, 20, 120, 120), stream=buf.getvalue())
    page.draw_rect(Rect(130, 130, 180, 180), color=(0, 0, 0), fill=(0, 1, 0))
    out = xp.open(stream=doc.tobytes(), filetype="pdf")[0]
    assert out.get_images()[0][2:4] == (50, 50)
    assert doc.extract_image(out.get_images()[0][0])["ext"] == "jpeg"
    drawings = out.get_drawings()
    assert len(drawings) == 1 and drawings[0]["type"] == "fs"
    rendered = out.get_pixmap().pil_image().convert("RGB")
    assert rendered.getpixel((155, 155)) == (0, 255, 0)
    assert rendered.getpixel((70, 70))[2] > 200


def test_insert_text_with_font_file_when_available():
    import glob

    fonts = glob.glob("/usr/share/fonts/**/NotoSansCJK*.ttc", recursive=True)
    if not fonts:
        return  # host without a CJK font: covered by the committed korean.pdf fixture
    doc = xp.open()
    page = doc.new_page()
    page.insert_text((50, 80), "한글 텍스트", fontsize=12, fontfile=fonts[0])
    assert "한글 텍스트" in page.get_text("text")


def test_delete_page_keeps_other_pages_usable():
    doc = xp.open()
    first = doc.new_page()
    first.insert_text((20, 40), "one")
    doc.new_page().insert_text((20, 40), "two")
    doc.new_page().insert_text((20, 40), "three")
    doc.delete_page(1)
    assert doc.page_count == 2
    assert [p.get_text("text").strip() for p in doc] == ["one", "three"]
    assert first.number == 0
