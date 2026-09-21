"""Pixmap: a rendered or loaded raster image."""

from __future__ import annotations

import io
import os

from .geometry import IRect, Rect

__all__ = ["Pixmap", "Colorspace", "csRGB", "csGRAY", "csCMYK"]


class Colorspace:
    def __init__(self, kind: int):
        self._kind = kind

    @property
    def n(self) -> int:
        return {1: 1, 2: 3, 3: 4}.get(self._kind, 3)

    @property
    def name(self) -> str:
        return {1: "DeviceGray", 2: "DeviceRGB", 3: "DeviceCMYK"}.get(self._kind, "DeviceRGB")

    def __repr__(self) -> str:
        return f"Colorspace({self.name})"

    def __eq__(self, other) -> bool:
        return isinstance(other, Colorspace) and other._kind == self._kind

    def __hash__(self) -> int:
        return hash(self._kind)


csGRAY = Colorspace(1)
csRGB = Colorspace(2)
csCMYK = Colorspace(3)


class Pixmap:
    """A raster image.

    Constructors mirror the shapes consumers use:

    * ``Pixmap(png_or_jpeg_bytes)`` / ``Pixmap(filename)`` load an image file.
    * ``Pixmap(colorspace, irect, alpha)`` creates a blank image.
    * ``Pixmap(colorspace, pixmap)`` converts colour space.
    """

    def __init__(self, *args, **kwargs):
        from PIL import Image

        self._image = None
        self._origin = (0, 0)
        self.xres = 96
        self.yres = 96
        if len(args) == 1 and isinstance(args[0], (bytes, bytearray, memoryview)):
            self._image = Image.open(io.BytesIO(bytes(args[0])))
            self._image.load()
        elif len(args) == 1 and isinstance(args[0], (str, os.PathLike)):
            self._image = Image.open(os.fspath(args[0]))
            self._image.load()
        elif len(args) >= 2 and isinstance(args[0], Colorspace) and isinstance(args[1], Pixmap):
            src = args[1]._image
            mode = {1: "L", 3: "RGB", 4: "CMYK"}[args[0].n]
            if len(args) > 2 and args[2] and args[0].n == 3:
                mode = "RGBA"
            self._image = src.convert(mode)
            self._origin = args[1]._origin
        elif len(args) >= 2 and isinstance(args[0], Colorspace):
            box = IRect(args[1])
            alpha = bool(args[2]) if len(args) > 2 else False
            mode = {1: "L", 3: "RGB", 4: "CMYK"}[args[0].n]
            if alpha and mode == "RGB":
                mode = "RGBA"
            elif alpha and mode == "L":
                mode = "LA"
            self._image = Image.new(mode, (max(1, box.width), max(1, box.height)), 0)
            self._origin = (box.x0, box.y0)
        elif len(args) == 1 and isinstance(args[0], Pixmap):
            self._image = args[0]._image.copy()
            self._origin = args[0]._origin
        else:
            raise ValueError("unsupported Pixmap constructor arguments")
        if self._image.mode not in ("RGB", "RGBA", "L", "LA", "CMYK"):
            self._image = self._image.convert("RGBA" if "A" in self._image.getbands() else "RGB")

    @classmethod
    def _from_pil(cls, image, origin=(0, 0), xres: int = 96, yres: int = 96) -> "Pixmap":
        pix = cls.__new__(cls)
        pix._image = image
        pix._origin = origin
        pix.xres = xres
        pix.yres = yres
        return pix

    # -- properties ----------------------------------------------------------
    @property
    def width(self) -> int:
        return self._image.width

    @property
    def height(self) -> int:
        return self._image.height

    w = width
    h = height

    @property
    def x(self) -> int:
        return self._origin[0]

    @property
    def y(self) -> int:
        return self._origin[1]

    @property
    def alpha(self) -> int:
        return 1 if "A" in self._image.getbands() else 0

    @property
    def n(self) -> int:
        return len(self._image.getbands())

    @property
    def stride(self) -> int:
        return self.width * self.n

    @property
    def colorspace(self) -> Colorspace | None:
        bands = self.n - self.alpha
        return {1: csGRAY, 3: csRGB, 4: csCMYK}.get(bands)

    @property
    def irect(self) -> IRect:
        return IRect(self.x, self.y, self.x + self.width, self.y + self.height)

    @property
    def size(self) -> int:
        return self.stride * self.height

    @property
    def samples(self) -> bytes:
        return self._image.tobytes()

    @property
    def samples_mv(self) -> memoryview:
        return memoryview(self.samples)

    @property
    def is_monochrome(self) -> bool:
        return self.n - self.alpha == 1

    def __repr__(self) -> str:
        return f"Pixmap({self.colorspace!r}, {self.irect!r}, {bool(self.alpha)})"

    def __len__(self) -> int:
        return self.size

    # -- pixel access --------------------------------------------------------
    def pixel(self, x: int, y: int):
        value = self._image.getpixel((x, y))
        return tuple(value) if isinstance(value, tuple) else (value,)

    def set_pixel(self, x: int, y: int, color) -> None:
        self._image.putpixel((x, y), tuple(color) if self.n > 1 else int(color[0]))

    def clear_with(self, value: int = 0, bbox=None) -> None:
        if bbox is None:
            self._image.paste(self._fill_value(value), (0, 0, self.width, self.height))
        else:
            box = IRect(bbox)
            self._image.paste(self._fill_value(value), (box.x0, box.y0, box.x1, box.y1))

    def _fill_value(self, value: int):
        n = self.n
        if n == 1:
            return int(value)
        color = [int(value)] * (n - self.alpha)
        if self.alpha:
            color.append(255)
        return tuple(color)

    def invert_irect(self, bbox=None) -> None:
        from PIL import ImageOps

        box = IRect(bbox) if bbox is not None else self.irect
        region = self._image.crop((box.x0, box.y0, box.x1, box.y1))
        if "A" in region.getbands():
            rgb = ImageOps.invert(region.convert("RGB")).convert("RGBA")
            rgb.putalpha(region.getchannel("A"))
            region = rgb
        else:
            region = ImageOps.invert(region)
        self._image.paste(region, (box.x0, box.y0))

    def tint_with(self, black: int, white: int) -> None:
        pass

    def gamma_with(self, gamma: float) -> None:
        self._image = self._image.point(lambda v: int(255 * ((v / 255.0) ** gamma)))

    def shrink(self, n: int) -> None:
        if n <= 0:
            return
        factor = 2**n
        self._image = self._image.resize((max(1, self.width // factor), max(1, self.height // factor)))

    def copy(self, source: "Pixmap", bbox=None) -> None:
        box = IRect(bbox) if bbox is not None else source.irect
        region = source._image.crop((box.x0 - source.x, box.y0 - source.y, box.x1 - source.x, box.y1 - source.y))
        self._image.paste(region.convert(self._image.mode), (box.x0 - self.x, box.y0 - self.y))

    def set_dpi(self, xres: int, yres: int) -> None:
        self.xres, self.yres = int(xres), int(yres)

    def set_origin(self, x: int, y: int) -> None:
        self._origin = (int(x), int(y))

    # -- output --------------------------------------------------------------
    def pil_image(self):
        return self._image

    def tobytes(self, output: str = "png", jpg_quality: int = 95, **_ignored) -> bytes:
        fmt = _format(output)
        image = self._image
        if fmt == "JPEG" and image.mode not in ("RGB", "L", "CMYK"):
            image = image.convert("RGB")
        if fmt == "PPM":
            image = image.convert("RGB")
        if fmt == "PGM":
            image = image.convert("L")
        buf = io.BytesIO()
        kwargs = {"dpi": (self.xres, self.yres)} if fmt in ("PNG", "JPEG") else {}
        if fmt == "JPEG":
            kwargs["quality"] = jpg_quality
        if fmt in ("PPM", "PGM"):
            fmt = "PPM"
        image.save(buf, format=fmt, **kwargs)
        return buf.getvalue()

    def pil_tobytes(self, format: str = "PNG", **kwargs) -> bytes:
        buf = io.BytesIO()
        self._image.save(buf, format=format, **kwargs)
        return buf.getvalue()

    def save(self, filename, output: str | None = None, jpg_quality: int = 95, **_ignored) -> None:
        path = os.fspath(filename)
        if output is None:
            ext = os.path.splitext(path)[1].lstrip(".").lower() or "png"
            output = ext
        with open(path, "wb") as fh:
            fh.write(self.tobytes(output, jpg_quality=jpg_quality))

    def pil_save(self, filename, **kwargs) -> None:
        self._image.save(os.fspath(filename), **kwargs)

    def tobytes_pdf(self) -> bytes:
        raise NotImplementedError

    def color_count(self, colors: bool = False, clip=None):
        image = self._image
        if clip is not None:
            box = IRect(clip)
            image = image.crop((box.x0, box.y0, box.x1, box.y1))
        counts = image.getcolors(maxcolors=1 << 24) or []
        if colors:
            return {color: count for count, color in counts}
        return len(counts)

    def color_topusage(self, clip=None):
        counts = self.color_count(colors=True, clip=clip)
        if not counts:
            return 1.0, b"\xff" * self.n
        color, count = max(counts.items(), key=lambda kv: kv[1])
        total = sum(counts.values())
        return count / total, bytes(color if isinstance(color, tuple) else (color,))


def _format(output: str) -> str:
    key = (output or "png").lower()
    return {
        "png": "PNG",
        "jpg": "JPEG",
        "jpeg": "JPEG",
        "ppm": "PPM",
        "pgm": "PGM",
        "pnm": "PPM",
        "pbm": "PPM",
        "tiff": "TIFF",
        "tif": "TIFF",
        "bmp": "BMP",
        "webp": "WEBP",
    }.get(key, "PNG")


_ = Rect
