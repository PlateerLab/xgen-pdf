"""Output formats for ``Page.get_text``: text, blocks, words, dict, rawdict, json, html."""

from __future__ import annotations

import base64
import html
import json

from .text import Block, sort_blocks

__all__ = ["render_text"]


def _span_dict(span, raw: bool) -> dict:
    ascender, descender = span.ascender_descender()
    origin = span.origin
    item = {
        "size": span.size,
        "flags": span.flags,
        "font": span.font,
        "color": span.color,
        "ascender": ascender,
        "descender": descender,
        "origin": (origin.x, origin.y) if origin else (0.0, 0.0),
        "bbox": tuple(span.bbox),
    }
    if raw:
        item["chars"] = [
            {"origin": (ch.origin.x, ch.origin.y), "bbox": tuple(ch.bbox), "c": ch.char} for ch in span.chars
        ]
    else:
        item["text"] = span.text
    return item


def _line_dict(line, raw: bool) -> dict:
    return {
        "spans": [_span_dict(span, raw) for span in line.spans],
        "wmode": line.wmode,
        "dir": line.dir,
        "bbox": tuple(line.bbox),
    }


def _text_block_dict(block: Block, raw: bool) -> dict:
    return {
        "number": block.number,
        "type": 0,
        "bbox": tuple(block.bbox),
        "lines": [_line_dict(line, raw) for line in block.lines],
    }


def _merge(page, blocks: list[Block], image_blocks: list[dict], sort: bool) -> list[tuple[int, object]]:
    """Text and image blocks in content order (or sorted)."""
    items: list[tuple[tuple, object]] = []
    for block in blocks:
        items.append(((0, block.seq, block.number), block))
    for img in image_blocks:
        items.append(((0, img.get("_seq", 0), -1), img))
    if sort:
        items.sort(
            key=lambda pair: (
                (pair[1]["bbox"][3], pair[1]["bbox"][0])
                if isinstance(pair[1], dict)
                else (pair[1].bbox.y1, pair[1].bbox.x0)
            )
        )
    else:
        items.sort(key=lambda pair: (pair[0][1], pair[0][2]))
    return [(i, obj) for i, (_key, obj) in enumerate(items)]


def render_text(page, option: str, blocks: list[Block], image_blocks: list[dict], sort: bool = False):
    if option == "text":
        if sort:
            blocks = sort_blocks(list(blocks))
        return "".join(block.text for block in blocks)

    if option == "words":
        if sort:
            blocks = sort_blocks(list(blocks))
        words = []
        for b_no, block in enumerate(blocks):
            for l_no, line in enumerate(block.lines):
                w_no = 0
                current: list = []
                for ch in line.chars:
                    if ch.char.isspace():
                        if current:
                            words.append(_word_tuple(current, b_no, l_no, w_no))
                            w_no += 1
                            current = []
                        continue
                    current.append(ch)
                if current:
                    words.append(_word_tuple(current, b_no, l_no, w_no))
        return words

    merged = _merge(page, blocks, image_blocks, sort)

    if option == "blocks":
        out = []
        for number, obj in merged:
            if isinstance(obj, dict):
                text = (
                    f"<image: {_cs_name(obj.get('colorspace', 3))}, width: {obj.get('width', 0)}, "
                    f"height: {obj.get('height', 0)}, bpc: {obj.get('bpc', 8)}>"
                )
                out.append(tuple(obj["bbox"]) + (text, number, 1))
            else:
                out.append(tuple(obj.bbox) + (obj.text, number, 0))
        return out

    raw = option in ("rawdict", "rawjson")
    if option in ("dict", "rawdict", "json", "rawjson"):
        result_blocks = []
        for number, obj in merged:
            if isinstance(obj, dict):
                img = {k: v for k, v in obj.items() if not k.startswith("_")}
                img["number"] = number
                result_blocks.append(img)
            else:
                item = _text_block_dict(obj, raw)
                item["number"] = number
                result_blocks.append(item)
        result = {"width": page.rect.width, "height": page.rect.height, "blocks": result_blocks}
        if option in ("json", "rawjson"):
            return json.dumps(result, default=_json_default)
        return result

    if option in ("html", "xhtml"):
        return _render_html(page, merged, option == "xhtml")

    raise ValueError(f"unknown text option {option!r}")


def _word_tuple(chars, b_no: int, l_no: int, w_no: int) -> tuple:
    from .geometry import Rect

    rect = Rect()
    for ch in chars:
        rect.include_rect(ch.bbox)
    return (rect.x0, rect.y0, rect.x1, rect.y1, "".join(ch.char for ch in chars), b_no, l_no, w_no)


def _cs_name(n: int) -> str:
    return {1: "DeviceGray", 3: "DeviceRGB", 4: "DeviceCMYK"}.get(n, "DeviceRGB")


def _json_default(value):
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    return str(value)


def _render_html(page, merged, xhtml: bool) -> str:
    parts = [f'<div id="page0" style="position:relative;width:{page.rect.width}pt;height:{page.rect.height}pt">']
    for _number, obj in merged:
        if isinstance(obj, dict):
            data = obj.get("image", b"")
            if data:
                b64 = base64.b64encode(data).decode("ascii")
                x0, y0, x1, y1 = obj["bbox"]
                parts.append(
                    f'<img style="position:absolute;left:{x0}pt;top:{y0}pt;width:{x1 - x0}pt;height:{y1 - y0}pt" '
                    f'src="data:image/{obj.get("ext", "png")};base64,{b64}"/>'
                )
            continue
        for line in obj.lines:
            bbox = line.bbox
            spans = []
            for span in line.spans:
                style = f"font-family:{html.escape(span.font)};font-size:{span.size}pt;color:#{span.color:06x}"
                if span.flags & 16:
                    style += ";font-weight:bold"
                if span.flags & 2:
                    style += ";font-style:italic"
                spans.append(f'<span style="{style}">{html.escape(span.text)}</span>')
            parts.append(f'<p style="position:absolute;left:{bbox.x0}pt;top:{bbox.y0}pt;margin:0">{"".join(spans)}</p>')
    parts.append("</div>")
    return "\n".join(parts)
