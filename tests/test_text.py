import json

from xgen_pdf import Rect


def test_plain_text_lines_in_reading_order(korean):
    text = korean[0].get_text("text")
    lines = text.splitlines()
    assert lines[0] == "XGEN 문서 처리 요약"
    assert lines[1] == "첫 번째 문단입니다. 한국어 본문 텍스트가 여러 줄에"
    assert "담당자 김진수 연락처 010-1234-5678" in lines
    assert text.endswith("\n")
    # every table cell is its own line (cells are far apart on the baseline)
    for cell in ("항목", "값", "비고", "검증됨"):
        assert cell in lines


def test_get_text_default_is_text(korean):
    assert korean[0].get_text() == korean[0].get_text("text")


def test_dict_structure_and_spans(korean):
    info = korean[0].get_text("dict")
    assert info["width"] == 500 and info["height"] == 700
    blocks = info["blocks"]
    text_blocks = [b for b in blocks if b["type"] == 0]
    image_blocks = [b for b in blocks if b["type"] == 1]
    assert len(image_blocks) == 1
    img = image_blocks[0]
    assert img["width"] == 80 and img["height"] == 60 and img["ext"] in ("png", "jpeg")
    assert len(img["image"]) > 0 and len(img["bbox"]) == 4
    # the heading is its own block with one span at 16pt
    heading = text_blocks[0]
    span = heading["lines"][0]["spans"][0]
    assert span["text"] == "XGEN 문서 처리 요약"
    assert abs(span["size"] - 16) < 0.05
    assert "NotoSansCJK" in span["font"]
    assert span["color"] == 0
    assert span["ascender"] > 0 > span["descender"]
    assert set(span) >= {"size", "flags", "font", "color", "ascender", "descender", "origin", "bbox", "text"}
    line = heading["lines"][0]
    assert line["wmode"] == 0 and line["dir"] == (1.0, 0.0)
    # three body lines with regular pitch form one block; the gapped line another
    body = text_blocks[1]
    starts = [ln["spans"][0]["text"] for ln in body["lines"]]
    assert len(starts) == 3
    assert starts[0].startswith("첫 번째") and starts[1].startswith("걸쳐") and starts[2].startswith("두 번째")
    assert text_blocks[2]["lines"][0]["spans"][0]["text"].startswith("다음 문단은")


def test_two_columns_on_one_baseline_are_separate_lines_same_block(korean):
    info = korean[0].get_text("dict")
    for block in info["blocks"]:
        if block["type"] != 0:
            continue
        texts = ["".join(s["text"] for s in ln["spans"]) for ln in block["lines"]]
        if "왼쪽 열 텍스트" in texts:
            assert texts == ["왼쪽 열 텍스트", "오른쪽 열 텍스트"]
            break
    else:
        raise AssertionError("column block not found")


def test_rawdict_has_chars_with_boxes(korean):
    raw = korean[0].get_text("rawdict")
    span = [b for b in raw["blocks"] if b["type"] == 0][0]["lines"][0]["spans"][0]
    assert "text" not in span
    chars = span["chars"]
    assert "".join(c["c"] for c in chars) == "XGEN 문서 처리 요약"
    for c in chars:
        assert len(c["bbox"]) == 4 and len(c["origin"]) == 2
    assert chars[1]["bbox"][0] >= chars[0]["bbox"][0]


def test_blocks_and_words_tuples(korean):
    blocks = korean[0].get_text("blocks")
    assert all(len(b) == 7 for b in blocks)
    assert blocks[0][4].startswith("XGEN 문서 처리 요약\n") and blocks[0][6] == 0
    assert all(b[6] == 0 for b in blocks)  # no image blocks in "blocks"
    words = korean[0].get_text("words")
    assert all(len(w) == 8 for w in words)
    texts = [w[4] for w in words]
    assert "김진수" in texts and "010-1234-5678" in texts
    x0, y0, x1, y1 = words[0][:4]
    assert x0 < x1 and y0 < y1


def test_sort_orders_blocks_top_to_bottom(korean):
    blocks = korean[0].get_text("dict", sort=True)["blocks"]
    tops = [b["bbox"][3] for b in blocks]
    assert tops == sorted(tops)


def test_clip_limits_text(korean):
    page = korean[0]
    clipped = page.get_text("text", clip=Rect(0, 240, 500, 265))
    assert clipped.strip() == "담당자 김진수 연락처 010-1234-5678"


def test_json_output(korean):
    data = json.loads(korean[0].get_text("json"))
    assert data["width"] == 500 and data["blocks"]


def test_html_output_mentions_text(korean):
    html = korean[0].get_text("html")
    assert "XGEN 문서 처리 요약" in html and "<img" in html


def test_search_for_returns_rects(korean):
    page = korean[0]
    hits = page.search_for("김진수")
    assert len(hits) == 1
    rect = hits[0]
    assert rect.width > 10 and rect.height > 5
    assert page.get_text("text", clip=rect).strip() == "김진수"
    assert page.search_for("없는문장") == []
    assert page.search_for("xgen") == page.search_for("XGEN")  # case-insensitive
    assert len(page.search_for("김진수", quads=True)) == 1


def test_latin_layout(latin):
    lines = latin[0].get_text("text").splitlines()
    assert lines == [
        "Heading Line",
        "First paragraph line one.",
        "First paragraph line two.",
        "Second paragraph starts after a gap.",
        "Left cell",
        "Right cell",
    ]
    blocks = [b for b in latin[0].get_text("dict")["blocks"] if b["type"] == 0]
    assert [len(b["lines"]) for b in blocks] == [1, 2, 1, 2]
    span = blocks[0]["lines"][0]["spans"][0]
    assert span["font"] == "Helvetica" and abs(span["size"] - 16) < 0.05
