"""Raster images: enumerate placements, decode, and insert."""

from __future__ import annotations

import ctypes
import hashlib
import io

import pypdfium2 as pdfium
import pypdfium2.raw as c

from . import _bridge
from .geometry import Matrix, Rect

__all__ = ["collect_images", "insert_image"]

_CS_NAMES = {
    c.FPDF_COLORSPACE_DEVICEGRAY: ("DeviceGray", 1),
    c.FPDF_COLORSPACE_DEVICERGB: ("DeviceRGB", 3),
    c.FPDF_COLORSPACE_DEVICECMYK: ("DeviceCMYK", 4),
    c.FPDF_COLORSPACE_CALGRAY: ("CalGray", 1),
    c.FPDF_COLORSPACE_CALRGB: ("CalRGB", 3),
    c.FPDF_COLORSPACE_LAB: ("Lab", 3),
    c.FPDF_COLORSPACE_ICCBASED: ("ICCBased", 3),
    c.FPDF_COLORSPACE_SEPARATION: ("Separation", 1),
    c.FPDF_COLORSPACE_DEVICEN: ("DeviceN", 3),
    c.FPDF_COLORSPACE_INDEXED: ("Indexed", 3),
    c.FPDF_COLORSPACE_PATTERN: ("Pattern", 3),
}


def _decode_to_png(handle, page) -> tuple[bytes, int, int, str]:
    """Decode the image through pdfium and encode it as PNG."""

    bitmap_raw = c.FPDFImageObj_GetBitmap(handle)
    if not bitmap_raw:
        bitmap_raw = c.FPDFImageObj_GetRenderedBitmap(page._doc.raw, page.raw, handle)
    if not bitmap_raw:
        return b"", 0, 0, "png"
    bitmap = pdfium.PdfBitmap.from_raw(bitmap_raw)
    try:
        image = bitmap.to_pil()
        width, height = image.size
        if image.mode not in ("RGB", "RGBA", "L", "1"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue(), width, height, "png"
    finally:
        bitmap.close()


def _describe(handle, page) -> dict:
    meta = _bridge.image_metadata(handle, page.raw)
    filters = _bridge.image_filters(handle)
    width = meta.width if meta else 0
    height = meta.height if meta else 0
    bpc = meta.bits_per_pixel if meta else 8
    cs_name, cs_n = _CS_NAMES.get(meta.colorspace if meta else -1, ("DeviceRGB", 3))
    xres = int(meta.horizontal_dpi) if meta and meta.horizontal_dpi else 96
    yres = int(meta.vertical_dpi) if meta and meta.vertical_dpi else 96
    ext = None
    data = b""
    if filters and filters[-1] == "DCTDecode" and len(filters) == 1:
        data = _bridge.image_raw_data(handle)
        ext = "jpeg"
    elif filters and filters[-1] == "JPXDecode" and len(filters) == 1:
        data = _bridge.image_raw_data(handle)
        ext = "jpx"
    if not data:
        data, w, h, ext = _decode_to_png(handle, page)
        if w and h:
            width, height = w, h
    if bpc and cs_n:
        bpc = max(1, bpc // cs_n) if bpc >= cs_n else bpc
    return {
        "ext": ext or "png",
        "smask": 0,
        "width": width,
        "height": height,
        "colorspace": cs_n,
        "bpc": bpc,
        "xres": xres,
        "yres": yres,
        "cs-name": cs_name,
        "filters": filters,
        "image": data,
    }


def collect_images(page) -> list[dict]:
    """One entry per image placement on the page, registering each distinct
    image on the document so ``extract_image`` can find it."""
    doc = page._doc
    entries = []
    for obj in page._walk():
        if obj.type != _bridge.OBJ_IMAGE:
            continue
        raw = _bridge.image_raw_data(obj.handle)
        filters = _bridge.image_filters(obj.handle)
        digest_src = raw if raw else _bridge.image_decoded_data(obj.handle)
        digest = hashlib.md5(b"|".join(f.encode() for f in filters) + b"#" + digest_src).hexdigest()
        xref = doc._register_image(digest)
        if xref not in doc._image_cache:
            doc._image_cache[xref] = _describe(obj.handle, page)
        info = doc._image_cache[xref]
        matrix = obj.matrix * page._dm
        bbox = Rect(0, 0, 1, 1).transform(matrix).normalize()
        entries.append(
            {
                "xref": xref,
                "digest": digest,
                "bbox": bbox,
                "transform": Matrix(matrix),
                "width": info.get("width", 0),
                "height": info.get("height", 0),
                "seq": obj.seq,
                "handle": obj.handle,
                "parent": obj.parent,
            }
        )
    return entries


def insert_image(
    page, rect: Rect, filename=None, pixmap=None, stream=None, rotate: int = 0, keep_proportion: bool = True
) -> None:
    from PIL import Image

    doc = page._doc
    if pixmap is not None:
        pil = pixmap._image
        data = None
    else:
        if stream is not None:
            if hasattr(stream, "read"):
                stream = stream.read()
            data = bytes(stream)
        elif filename is not None:
            with open(filename, "rb") as fh:
                data = fh.read()
        else:
            raise ValueError("exactly one of filename, pixmap, stream must be given")
        pil = Image.open(io.BytesIO(data))
        pil.load()
    if rotate % 360:
        pil = pil.rotate(-rotate, expand=True)
    width, height = pil.size
    if keep_proportion and width and height:
        scale = min(rect.width / width, rect.height / height)
        box_w, box_h = width * scale, height * scale
        x0 = rect.x0 + (rect.width - box_w) / 2
        y0 = rect.y0 + (rect.height - box_h) / 2
        rect = Rect(x0, y0, x0 + box_w, y0 + box_h)
    image = pdfium.PdfImage.new(doc._pdf)
    if data is not None and data[:2] == b"\xff\xd8" and pil.mode in ("RGB", "L", "CMYK"):
        image.load_jpeg(io.BytesIO(data), inline=False, autoclose=False)
    else:
        if pil.mode not in ("RGB", "RGBA", "L"):
            pil = pil.convert("RGBA" if "A" in pil.getbands() else "RGB")
        bitmap = pdfium.PdfBitmap.from_pil(pil)
        image.set_bitmap(bitmap)
    user = page._to_user(rect)
    image.set_matrix(pdfium.PdfMatrix(user.width, 0, 0, user.height, user.x0, user.y0))
    page._raw.insert_obj(image)


_ = ctypes
