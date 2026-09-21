"""Document: open, iterate pages, metadata, image registry, save."""

from __future__ import annotations

import ctypes
import io
import os
from typing import Iterator

import pypdfium2 as pdfium
import pypdfium2.raw as c

from . import _bridge
from .errors import FileDataError, PasswordError
from .page import Page

__all__ = ["Document", "open"]

_META_KEYS = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creator": "Creator",
    "producer": "Producer",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
    "trapped": "Trapped",
}


class Document:
    """A PDF document backed by pdfium.

    ``Document()`` creates an empty document.  ``Document(path)`` or
    ``Document(stream=bytes)`` opens an existing one.  Password-protected
    files open with ``needs_pass`` set; call ``authenticate`` to unlock.
    """

    def __init__(self, filename=None, stream=None, filetype=None, password=None, **_ignored):
        self._pdf: pdfium.PdfDocument | None = None
        self._pages: dict[int, Page] = {}
        self._data: bytes | None = None
        self._closed = False
        self.name = ""
        self.needs_pass = False
        self.is_encrypted = False
        self.is_pdf = True
        self._password = password
        self._images: dict[str, int] = {}  # digest -> xref
        self._image_cache: dict[int, dict] = {}  # xref -> extract_image() result
        self._fonts: dict[str, object] = {}
        self._dirty = False
        if isinstance(filename, (bytes, bytearray)):
            stream, filename = bytes(filename), None
        if stream is not None:
            if hasattr(stream, "read"):
                stream = stream.read()
            self._data = bytes(stream)
            self.name = ""
        elif filename is not None:
            path = os.fspath(filename)
            self.name = path
            if not os.path.exists(path):
                raise FileNotFoundError(f"no such file: '{path}'")
            with builtin_open(path, "rb") as fh:
                self._data = fh.read()
        if self._data is None:
            self._pdf = pdfium.PdfDocument.new()
            return
        self._load(password)

    # -- lifecycle -----------------------------------------------------------
    def _load(self, password) -> None:
        try:
            self._pdf = pdfium.PdfDocument(self._data, password=password)
        except pdfium.PdfiumError as exc:
            code = c.FPDF_GetLastError()
            if code == c.FPDF_ERR_PASSWORD:
                self.needs_pass = True
                self.is_encrypted = True
                self._pdf = None
                return
            raise FileDataError(f"cannot open broken document: {exc}") from exc
        self.is_encrypted = c.FPDF_GetSecurityHandlerRevision(self._pdf.raw) != -1
        self.needs_pass = False

    def authenticate(self, password: str) -> int:
        """Unlock an encrypted document. Returns 0 on failure, non-zero on success."""
        if self._data is None:
            return 1
        self._load(password)
        if self._pdf is None:
            return 0
        self._password = password
        self._pages.clear()
        return 2

    def close(self) -> None:
        if self._closed:
            return
        for page in self._pages.values():
            page._close()
        self._pages.clear()
        if self._pdf is not None:
            try:
                self._pdf.close()
            except Exception:
                pass
        self._pdf = None
        self._closed = True

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __enter__(self) -> "Document":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"Document('{self.name}')" if self.name else "Document('<memory>')"

    def _require(self) -> pdfium.PdfDocument:
        if self._closed:
            raise ValueError("document closed")
        if self._pdf is None:
            raise PasswordError("document needs a password")
        return self._pdf

    @property
    def raw(self):
        """The pdfium document handle."""
        return self._require().raw

    # -- pages ---------------------------------------------------------------
    @property
    def page_count(self) -> int:
        if self._pdf is None:
            return 0
        return len(self._pdf)

    def __len__(self) -> int:
        return self.page_count

    def load_page(self, index: int = 0) -> Page:
        pdf = self._require()
        count = len(pdf)
        if isinstance(index, tuple):
            index = index[0]
        if index < 0:
            index += count
        if not 0 <= index < count:
            raise IndexError("page not in document")
        page = self._pages.get(index)
        if page is None or page._closed:
            page = Page(self, pdf[index], index)
            self._pages[index] = page
        return page

    def __getitem__(self, index) -> Page:
        if isinstance(index, slice):
            return [self.load_page(i) for i in range(*index.indices(self.page_count))]
        return self.load_page(index)

    def __iter__(self) -> Iterator[Page]:
        for i in range(self.page_count):
            yield self.load_page(i)

    def pages(self, start=None, stop=None, step=None) -> Iterator[Page]:
        for i in range(*slice(start, stop, step).indices(self.page_count)):
            yield self.load_page(i)

    def new_page(self, pno: int = -1, width: float = 595, height: float = 842) -> Page:
        pdf = self._require()
        count = len(pdf)
        if pno < 0 or pno > count:
            pno = count
        raw_page = pdf.new_page(width, height, index=pno)
        shifted = {}
        for index, page in self._pages.items():
            new_index = index + 1 if index >= pno else index
            page.number = new_index
            shifted[new_index] = page
        self._pages = shifted
        page = Page(self, raw_page, pno)
        self._pages[pno] = page
        return page

    def delete_page(self, pno: int = -1) -> None:
        pdf = self._require()
        if pno < 0:
            pno += len(pdf)
        page = self._pages.pop(pno, None)
        if page is not None:
            page._close()
        pdf.del_page(pno)
        shifted = {}
        for index, other in self._pages.items():
            new_index = index - 1 if index > pno else index
            other.number = new_index
            shifted[new_index] = other
        self._pages = shifted

    # -- metadata ------------------------------------------------------------
    @property
    def metadata(self) -> dict:
        if self._pdf is None:
            return {}
        out = {"format": self._format_string(), "encryption": None}
        for key, tag in _META_KEYS.items():
            out[key] = _bridge.get_meta_text(self._pdf.raw, tag)
        if self.is_encrypted:
            out["encryption"] = "Standard Security Handler"
        return out

    def _format_string(self) -> str:
        try:
            version = self._pdf.get_version()
        except Exception:
            version = None
        if not version:
            return "PDF"
        return f"PDF {version // 10}.{version % 10}"

    # -- images --------------------------------------------------------------
    def _register_image(self, digest: str) -> int:
        xref = self._images.get(digest)
        if xref is None:
            xref = len(self._images) + 1
            self._images[digest] = xref
        return xref

    def extract_image(self, xref: int) -> dict:
        """Decoded image for an xref returned by ``Page.get_images``."""
        info = self._image_cache.get(xref)
        if info is None:
            for page in self:
                page._collect_images()
                info = self._image_cache.get(xref)
                if info is not None:
                    break
        if info is None:
            raise ValueError(f"xref {xref} is not an image")
        return dict(info)

    def _replace_data(self, data: bytes) -> None:
        """Swap the underlying pdfium document for one loaded from ``data``
        (after an edit done outside pdfium), keeping Page objects usable."""
        pdf = self._require()
        self._data = data
        old_pages = dict(self._pages)
        for page in old_pages.values():
            page._drop_textpage()
            try:
                page._raw.close()
            except Exception:
                pass
        try:
            pdf.close()
        except Exception:
            pass
        self._pdf = None
        self._load(self._password)
        new_pdf = self._require()
        self._pages = {}
        for index, page in old_pages.items():
            if 0 <= index < len(new_pdf):
                page._rebind(new_pdf[index])
                self._pages[index] = page
        self._fonts = {}
        self._dirty = True

    # -- fonts ---------------------------------------------------------------
    def _standard_font(self, name: str = "Helvetica"):
        font = self._fonts.get(name)
        if font is None:
            font = c.FPDFText_LoadStandardFont(self.raw, name.encode("ascii"))
            if not font:
                raise ValueError(f"cannot load standard font {name!r}")
            self._fonts[name] = font
        return font

    def _file_font(self, path: str):
        font = self._fonts.get(path)
        if font is None:
            with builtin_open(path, "rb") as fh:
                data = fh.read()
            buf = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
            kind = c.FPDF_FONT_TRUETYPE
            if data[:4] in (b"OTTO", b"%!PS") or path.lower().endswith((".otf", ".pfb", ".cff")):
                kind = c.FPDF_FONT_TYPE1 if data[:2] == b"%!" else c.FPDF_FONT_TRUETYPE
            font = c.FPDFText_LoadFont(self.raw, buf, len(data), kind, 1)
            if not font:
                raise ValueError(f"cannot load font file {path!r}")
            self._fonts[path] = (font, buf)
            return font
        return font[0] if isinstance(font, tuple) else font

    # -- saving --------------------------------------------------------------
    def tobytes(self, garbage: int = 0, deflate: bool = False, **_ignored) -> bytes:
        """Serialise the document.  Unreferenced objects are dropped (``garbage``
        is accepted for compatibility; the output is always fully rewritten)."""
        pdf = self._require()
        for page in self._pages.values():
            page._flush()
        buf = io.BytesIO()
        pdf.save(buf, flags=c.FPDF_NO_INCREMENTAL)
        return buf.getvalue()

    def save(self, filename, garbage: int = 0, deflate: bool = False, **kwargs) -> None:
        data = self.tobytes(garbage=garbage, deflate=deflate, **kwargs)
        with builtin_open(os.fspath(filename), "wb") as fh:
            fh.write(data)

    write = tobytes


builtin_open = open


def open(*args, **kwargs) -> Document:  # noqa: A001 - mirrors the historical API
    return Document(*args, **kwargs)
