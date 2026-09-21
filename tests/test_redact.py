import io
import re
import zlib

from PIL import Image, ImageChops

import xgen_pdf as xp
from xgen_pdf import Matrix, Rect


def _render(page, zoom=2):
    return Image.open(io.BytesIO(page.get_pixmap(matrix=Matrix(zoom, zoom)).tobytes("png"))).convert("RGB")


def _reachable_objects(data: bytes):
    objects = {int(m.group(1)): m.start() for m in re.finditer(rb"(?m)^(\d+)\s+0\s+obj\b", data)}
    refs = lambda src: {int(m) for m in re.findall(rb"(\d+)\s+0\s+R", src)}  # noqa: E731
    trailer = data[data.rfind(b"trailer") :]
    seen, stack = set(), list(refs(trailer))
    while stack:
        n = stack.pop()
        if n in seen or n not in objects:
            continue
        seen.add(n)
        end = data.find(b"endobj", objects[n])
        stack.extend(refs(data[objects[n] : end]))
    return objects, seen


def test_redaction_removes_text_and_keeps_the_rest(korean_path):
    raw = korean_path.read_bytes()
    doc = xp.open(stream=raw, filetype="pdf")
    page = doc[0]
    before = page.get_text("text")
    before_img = _render(page)
    hits = page.search_for("김진수")
    assert len(hits) == 1
    page.add_redact_annot(hits[0], text="***", fontsize=8, cross_out=False)
    for rect in page.search_for("5678"):
        page.add_redact_annot(rect, text="", cross_out=False)
    assert page.apply_redactions() is True
    out = doc.tobytes()

    again = xp.open(stream=out, filetype="pdf")
    after = again[0].get_text("text")
    assert "김진수" not in after and "5678" not in after
    expected = before.replace("김진수 ", "").replace("-5678", "-")
    norm = lambda s: re.sub(r"\s+", "", s)  # noqa: E731
    assert norm(after).replace("***", "") == norm(expected).replace("***", "")
    # the label is real text in the output
    assert "***" in after
    # the page object still works after the rewrite
    assert "***" in page.get_text("text") and "김진수" not in page.get_text("text")

    # pixels outside the redaction boxes are unchanged
    after_img = _render(again[0])
    assert after_img.size == before_img.size
    diff = ImageChops.difference(before_img, after_img).convert("L")
    boxes = [Rect(hits[0])] + [Rect(r) for r in xp.open(stream=raw, filetype="pdf")[0].search_for("5678")]
    changed_outside = 0
    for x, y in ((x, y) for y in range(0, after_img.height, 3) for x in range(0, after_img.width, 3)):
        if diff.getpixel((x, y)) > 40 and not any(
            b.x0 * 2 - 4 <= x <= b.x1 * 2 + 4 and b.y0 * 2 - 4 <= y <= b.y1 * 2 + 4 for b in boxes
        ):
            changed_outside += 1
    assert changed_outside <= 8  # anti-aliasing at the glyph cut boundary
    # the box itself is painted white
    box = after_img.crop(
        (int(hits[0].x0 * 2) + 1, int(hits[0].y0 * 2) + 1, int(hits[0].x1 * 2) - 1, int(hits[0].y0 * 2) + 3)
    )
    assert box.getextrema()[0][0] > 240


def test_redacted_output_has_no_orphans_and_no_old_content(korean_path):
    raw = korean_path.read_bytes()
    doc = xp.open(stream=raw, filetype="pdf")
    page = doc[0]
    original_content = b""
    for m in re.finditer(rb"stream\r?\n", raw):
        chunk = raw[m.end() : raw.find(b"endstream", m.end())]
        try:
            chunk = zlib.decompress(chunk)
        except zlib.error:
            continue
        if b"Tj" in chunk or b"TJ" in chunk:
            original_content = chunk
            break
    assert original_content
    page.add_redact_annot(page.search_for("김진수")[0])
    page.apply_redactions()
    out = doc.tobytes()
    objects, reachable = _reachable_objects(out)
    assert set(objects) == reachable
    streams = []
    for m in re.finditer(rb"stream\r?\n", out):
        chunk = out[m.end() : out.find(b"endstream", m.end())]
        try:
            streams.append(zlib.decompress(chunk))
        except zlib.error:
            streams.append(chunk)
    assert not any(original_content[:80] in s for s in streams)
    assert xp.open(stream=out, filetype="pdf")[0].search_for("김진수") == []


def test_redaction_of_image_and_line_art(korean_path):
    doc = xp.open(korean_path)
    page = doc[0]
    assert len(page.get_images()) == 1 and len(page.get_drawings()) == 9
    # cover the image completely and the first table rule fully
    page.add_redact_annot(Rect(295, 395, 385, 465))
    page.add_redact_annot(Rect(35, 295, 465, 305))
    page.apply_redactions()
    out = xp.open(stream=doc.tobytes(), filetype="pdf")[0]
    assert out.get_images() == []
    assert len([d for d in out.get_drawings() if d["items"][0][0] == "l"]) == 7
    # partially covered filled rectangle keeps its drawing but text remains intact
    assert "XGEN 문서 처리 요약" in out.get_text("text")


def test_partial_image_cover_blanks_pixels(korean_path):
    doc = xp.open(korean_path)
    page = doc[0]
    page.add_redact_annot(Rect(300, 400, 340, 460), fill=None)
    page.apply_redactions()
    out = xp.open(stream=doc.tobytes(), filetype="pdf")[0]
    assert len(out.get_images()) == 1
    info = doc.extract_image(out.get_images()[0][0])
    pil = Image.open(io.BytesIO(info["image"])).convert("RGB")
    assert pil.getpixel((10, 30)) == (255, 255, 255)  # left half blanked
    assert pil.getpixel((70, 30)) == (30, 120, 200)  # right half intact


def test_apply_without_annotations_is_noop(korean):
    assert korean[0].apply_redactions() is False


def test_redaction_on_latin_page_keeps_layout(latin):
    page = latin[0]
    hit = page.search_for("line one")[0]
    page.add_redact_annot(hit)
    page.apply_redactions()
    text = page.get_text("text")
    assert "line one" not in text
    assert "First paragraph line two." in text
    words = {w[4]: w for w in page.get_text("words")}
    # the full stop that followed the removed words stays where it was
    assert abs(words["."][0] - (hit.x1)) < 3
    assert abs(words["two."][0] - words["paragraph"][2]) < 60
