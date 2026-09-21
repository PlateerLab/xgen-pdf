"""Development aid: compare xgen_pdf output with another fitz-shaped engine.

Usage::

    python scripts/compare_engines.py other_module file1.pdf [file2.pdf ...]

``other_module`` is imported by name (for example ``pymupdf``); it must not be
a runtime dependency of anything shipped.  Prints text similarity, block /
line / span counts, drawing / image / table counts and timings per file.
"""

from __future__ import annotations

import difflib
import importlib
import re
import sys
import time

import xgen_pdf


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def compare(other, path: str) -> None:
    raw = open(path, "rb").read()
    t0 = time.perf_counter()
    theirs = other.open(stream=raw, filetype="pdf")
    their_text = [p.get_text("text") for p in theirs]
    t_theirs = time.perf_counter() - t0
    t0 = time.perf_counter()
    ours = xgen_pdf.open(stream=raw, filetype="pdf")
    our_text = [p.get_text("text") for p in ours]
    t_ours = time.perf_counter() - t0
    sim = difflib.SequenceMatcher(None, _norm("".join(their_text)), _norm("".join(our_text))).ratio()
    print(
        f"== {path.rsplit('/', 1)[-1]} pages={len(theirs)}/{len(ours)} text-sim={sim:.4f} time other={t_theirs:.2f}s ours={t_ours:.2f}s"
    )
    for i in range(min(2, len(ours))):
        a, b = theirs[i].get_text("dict"), ours[i].get_text("dict")
        ta = [blk for blk in a["blocks"] if blk["type"] == 0]
        tb = [blk for blk in b["blocks"] if blk["type"] == 0]
        la = sum(len(blk["lines"]) for blk in ta)
        lb = sum(len(blk["lines"]) for blk in tb)
        print(
            f"   p{i} blocks {len(ta)}/{len(tb)} lines {la}/{lb} drawings {len(theirs[i].get_drawings())}/{len(ours[i].get_drawings())} "
            f"images {len(theirs[i].get_images())}/{len(ours[i].get_images())} tables {len(theirs[i].find_tables().tables)}/{len(ours[i].find_tables().tables)}"
        )


def main() -> None:
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    other = importlib.import_module(sys.argv[1])
    for path in sys.argv[2:]:
        compare(other, path)


if __name__ == "__main__":
    main()
