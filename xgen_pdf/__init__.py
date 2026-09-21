"""xgen-pdf: PDF text, layout, image, table, rendering and redaction engine.

Parsing, font decoding, rasterising and image decoding are delegated to
pdfium (via pypdfium2, BSD-3-Clause / Apache-2.0).  Everything above that
line - text layout, drawings, tables, search, redaction, geometry - is
implemented here.  The public surface is intentionally shaped like the
``fitz`` API so existing document code can switch with an import change::

    import xgen_pdf as fitz
    doc = fitz.open("file.pdf")
    for page in doc:
        page.get_text("dict")
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from .document import Document, open
from .errors import EmptyFileError, FileDataError, PasswordError, XgenPdfError
from .geometry import FZ_MAX, EPSILON, Identity, IRect, Matrix, Point, Quad, Rect
from .page import Page
from .pixmap import Colorspace, Pixmap, csCMYK, csGRAY, csRGB
from .tables import Table, TableFinder, TableHeader, TableRow
from .text import (
    TEXT_CID_FOR_UNKNOWN_UNICODE,
    TEXT_DEHYPHENATE,
    TEXT_INHIBIT_SPACES,
    TEXT_MEDIABOX_CLIP,
    TEXT_PRESERVE_IMAGES,
    TEXT_PRESERVE_LIGATURES,
    TEXT_PRESERVE_SPANS,
    TEXT_PRESERVE_WHITESPACE,
    TEXTFLAGS_BLOCKS,
    TEXTFLAGS_DICT,
    TEXTFLAGS_TEXT,
    TEXTFLAGS_WORDS,
)

try:
    __version__ = version("xgen-pdf")
except PackageNotFoundError:  # pragma: no cover - source checkout
    __version__ = "0.0.0"

VersionBind = __version__
version = (__version__, __version__, "xgen-pdf")

# Redaction option constants.
PDF_REDACT_IMAGE_NONE = 0
PDF_REDACT_IMAGE_REMOVE = 1
PDF_REDACT_IMAGE_PIXELS = 2
PDF_REDACT_IMAGE_REMOVE_UNLESS_INVISIBLE = 3
PDF_REDACT_LINE_ART_NONE = 0
PDF_REDACT_LINE_ART_REMOVE_IF_COVERED = 1
PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED = 2
PDF_REDACT_TEXT_REMOVE = 0
PDF_REDACT_TEXT_NONE = 1

INFINITE_RECT = Rect(-FZ_MAX - 1, -FZ_MAX - 1, FZ_MAX, FZ_MAX)
EMPTY_RECT = Rect()

Doc = Document

__all__ = [
    "Document",
    "Doc",
    "Page",
    "Pixmap",
    "Colorspace",
    "Rect",
    "IRect",
    "Point",
    "Matrix",
    "Quad",
    "Identity",
    "Table",
    "TableFinder",
    "TableHeader",
    "TableRow",
    "open",
    "csRGB",
    "csGRAY",
    "csCMYK",
    "XgenPdfError",
    "FileDataError",
    "PasswordError",
    "EmptyFileError",
    "EPSILON",
    "__version__",
    "TEXT_PRESERVE_LIGATURES",
    "TEXT_PRESERVE_WHITESPACE",
    "TEXT_PRESERVE_IMAGES",
    "TEXT_INHIBIT_SPACES",
    "TEXT_DEHYPHENATE",
    "TEXT_PRESERVE_SPANS",
    "TEXT_MEDIABOX_CLIP",
    "TEXT_CID_FOR_UNKNOWN_UNICODE",
    "TEXTFLAGS_TEXT",
    "TEXTFLAGS_DICT",
    "TEXTFLAGS_WORDS",
    "TEXTFLAGS_BLOCKS",
    "PDF_REDACT_IMAGE_NONE",
    "PDF_REDACT_IMAGE_REMOVE",
    "PDF_REDACT_IMAGE_PIXELS",
    "PDF_REDACT_LINE_ART_NONE",
    "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED",
    "PDF_REDACT_LINE_ART_REMOVE_IF_TOUCHED",
    "PDF_REDACT_TEXT_REMOVE",
    "PDF_REDACT_TEXT_NONE",
    "INFINITE_RECT",
    "EMPTY_RECT",
]
