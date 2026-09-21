# xgen-pdf

PDF text layout, tables, drawings, images, rendering and redaction for XGEN,
built on **pdfium** (via [pypdfium2](https://github.com/pypdfium2-team/pypdfium2))
with a `fitz`-shaped API so document code can switch with one import.

```python
import xgen_pdf as fitz

doc = fitz.open("report.pdf")
for page in doc:
    page.get_text("text")            # plain text in reading order
    page.get_text("dict")            # blocks -> lines -> spans (size, flags, font, color, bbox)
    page.get_text("rawdict")         # ... down to characters
    page.get_text("blocks") / ("words")
    page.get_drawings()              # vector paths: items, rect, fill, color, width
    page.get_images() / doc.extract_image(xref) / page.get_image_info(xrefs=True)
    page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=rect, alpha=False).tobytes("png")
    page.find_tables().tables[0].to_markdown()
    page.search_for("needle")        # -> [Rect, ...]
    page.add_redact_annot(rect, text="***"); page.apply_redactions()
doc.tobytes()
```

## Why

XGEN's document pipelines used PyMuPDF (AGPL-3.0).  This package replaces it
with permissively licensed parts only:

| Layer | Provided by | Licence |
|---|---|---|
| PDF parsing, font decoding, rasterising, image decoding | pdfium via pypdfium2 | BSD-3-Clause / Apache-2.0 |
| PNG/JPEG encoding | Pillow | MIT-CMU |
| Text layout (chars → spans → lines → blocks), drawings, table detection, search, redaction, geometry, page authoring | this package | Apache-2.0 |

## What is implemented here

* **Text layout** – pdfium supplies characters with boxes, fonts and colours;
  the grouping into spans, lines and blocks (baseline tracking, word gaps,
  paragraph pitch, column breaks) and the `text` / `dict` / `rawdict` /
  `blocks` / `words` / `json` / `html` renderings are our own.
* **Drawings** – path objects (including inside Form XObjects) flattened
  into `l` / `re` / `c` items with fill, stroke, width and dash information.
* **Tables** – edge/intersection/cell detection from ruling lines, text
  alignment or explicit lines; `Table.extract()`, `to_markdown()`,
  `to_pandas()`, `header`, `rows`, `cells`.
* **Search** – `search_for` over the laid-out lines.
* **Redaction** – `add_redact_annot` + `apply_redactions` edit the content
  streams directly: glyphs under the rectangle are cut out of the text
  operators (a `TJ` adjustment keeps the rest in place), line art inside is
  neutralised, image draws inside are dropped, partially covered images get
  their pixels blanked, and the box/label is painted from an appended
  stream.  The rest of the page stays byte-identical, and the saved file is
  fully rewritten so the removed content does not survive as an orphan.
* **Rendering** – `get_pixmap` with matrix/dpi/clip/alpha, `Pixmap.tobytes`,
  `save`, `pil_image`.
* **Authoring** – `Document()`, `new_page`, `insert_text` (standard fonts or a
  font file), `insert_image`, `draw_line`, `draw_rect`, `save` / `tobytes`.
* **Geometry** – `Rect`, `IRect`, `Point`, `Matrix`, `Quad` with the usual
  operators (`&`, `|`, `+`, `*`, `contains`, `intersects`, `get_area`, ...).

## Differences from PyMuPDF worth knowing

* Image `xref`s are engine-assigned ids (1, 2, ...), stable within a
  document, not PDF object numbers.
* `get_text("blocks")` does not include image blocks; `dict` does.
* Spans are merged whenever font, size, flags and colour agree, so span
  counts are lower than PyMuPDF's.
* Table detection treats section title bands as separate from the table
  they sit on; PyMuPDF sometimes absorbs them as a first row.
* Redaction of text encoded with a predefined CMap we do not know splits
  the whole text run instead of individual glyphs (never less than asked).

## Installation

xgen-pdf is distributed as a wheel on GitHub Releases, not on PyPI. Consumers
pin the release URL directly (PEP 508 direct reference):

```toml
"xgen-pdf @ https://github.com/PlateerLab/xgen-pdf/releases/download/v0.1.1/xgen_pdf-0.1.1-py3-none-any.whl",
```

A package that requires `xgen-pdf` only by name (for example xgen-doc2chunk on
PyPI) resolves against that pinned URL with both pip and uv; XGEN services
install with `pip install .` from their pyproject, so nothing else is needed.

## Development

```bash
pip install -e ".[dev]"
pytest -q
ruff check xgen_pdf/ tests/
```

Fixtures under `tests/fixtures/` are generated with this package itself
(`latin.pdf`, `korean.pdf`) except `encrypted.pdf`.
