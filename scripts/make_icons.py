#!/usr/bin/env python3
"""Generate the extension's icon set from AICodeQuestLogo.png.

    python extension/scripts/make_icons.py

Writes extension/icons/. Run it after changing the logo; the outputs are
committed, so a normal build does not need this and the extension has no
image-processing dependency of its own.

WHY THIS IS A SCRIPT AND NOT A ONE-OFF CROP. The source logo is a LOCKUP:
an emblem (shield, two hands, code brackets, a check badge) above a
wordmark ("AICODEQUEST / UNDERSTAND IT. | DEFEND IT. | MERGE IT."). Only
the emblem can be an icon -- the wordmark is illegible below about 200px
and reduces to a grey smear. Two things have to happen to get from one to
the other, and both are easy to do slightly wrong by hand:

  1. CROP TO THE EMBLEM ALONE. The emblem's bounding box is TALLER than it
     is wide, so squaring it by expanding around the centre reaches down
     into the wordmark and clips the tops of the letters into the icon.
     (That is exactly what the first attempt here did.) The emblem is
     therefore cropped TIGHT and then centred on a square transparent
     canvas, which cannot pull in anything below it.

  2. MAKE THE BACKGROUND TRANSPARENT. The source is RGB with a near-white
     (253-255) background and no alpha, so an untouched icon is a white
     tile -- fine on Chrome's light toolbar, obviously wrong on its dark
     one. It is done by FLOOD-FILLING inward from the border rather than
     by "make every near-white pixel transparent", because the artwork's
     own whites (the human hand, the robot hand's segments) would be
     punched full of holes by the latter. The flood reaches the shield's
     interior too -- the shield is an outline, open at the top -- which is
     wanted: it lets the emblem sit on either theme.

KNOWN LIMIT, not fixable here: at 16px the emblem is a coloured blob. The
hands and brackets carry the meaning and neither survives that size. The
honest options are to accept it (what this does) or to draw a separate
small-size mark, which is a design decision and not one to make in a
build script. See reports/phase-41-review.md.
"""

from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "AICodeQuestLogo.png"
OUT_DIR = REPO / "extension" / "icons"

# Chrome uses 16/32 for the toolbar (by DPI), 48 on chrome://extensions and
# 128 for the Web Store and installation dialog. All four are declared, so
# Chrome never has to downscale one of ours badly.
SIZES = (16, 32, 48, 128)

# How close to white counts as background. Generous, because the source is
# a lossy-looking RGB whose "white" ranges 253-255 and whose edges are
# antialiased into it.
WHITE_TOLERANCE = 26


def flood_background_to_transparent(img: Image.Image, tol: int = WHITE_TOLERANCE) -> Image.Image:
    """Clear the near-white region CONNECTED TO THE BORDER, and only that.

    A global "near-white -> transparent" pass would delete the human hand,
    which is white by design. This walks inward from every border pixel
    instead, so an enclosed white area is only cleared when it is actually
    reachable from outside the artwork.
    """
    img = img.copy()
    width, height = img.size
    px = img.load()

    def is_background(pixel) -> bool:
        r, g, b, _a = pixel
        return r > 255 - tol and g > 255 - tol and b > 255 - tol

    seen = bytearray(width * height)
    queue: deque[tuple[int, int]] = deque()

    def push(x: int, y: int) -> None:
        if not seen[y * width + x] and is_background(px[x, y]):
            seen[y * width + x] = 1
            queue.append((x, y))

    for x in range(width):
        push(x, 0)
        push(x, height - 1)
    for y in range(height):
        push(0, y)
        push(width - 1, y)

    while queue:
        x, y = queue.popleft()
        px[x, y] = (255, 255, 255, 0)
        if x > 0:
            push(x - 1, y)
        if x < width - 1:
            push(x + 1, y)
        if y > 0:
            push(x, y - 1)
        if y < height - 1:
            push(x, y + 1)
    return img


def content_rows(img: Image.Image, tol: int = WHITE_TOLERANCE) -> list[int]:
    """Row indices holding any non-background pixel."""
    width, height = img.size
    px = img.load()
    rows = []
    for y in range(height):
        for x in range(width):
            r, g, b = px[x, y][:3]
            if not (r > 255 - tol and g > 255 - tol and b > 255 - tol):
                rows.append(y)
                break
    return rows


def emblem_split_row(img: Image.Image) -> int:
    """The blank row band separating the emblem from the wordmark below it.

    FOUND, NOT HARDCODED, so a re-exported logo with slightly different
    spacing still splits in the right place. The widest all-blank band in
    the middle half of the image is the gap between the two blocks.
    """
    _width, height = img.size
    rows = set(content_rows(img))
    best: tuple[int, int] | None = None
    run_start = None
    for y in range(height + 1):
        blank = y < height and y not in rows
        if blank and run_start is None:
            run_start = y
        elif not blank and run_start is not None:
            length = y - run_start
            middle = height * 0.3 < run_start < height * 0.85
            if middle and (best is None or length > best[1] - best[0]):
                best = (run_start, y)
            run_start = None
    if best is None:
        raise SystemExit(
            "could not find a blank band between the emblem and the wordmark; "
            "the logo's layout has changed and this script needs revisiting"
        )
    return (best[0] + best[1]) // 2


def main() -> int:
    if not SOURCE.exists():
        print(f"no logo at {SOURCE}", file=sys.stderr)
        return 1

    source = Image.open(SOURCE).convert("RGBA")
    split = emblem_split_row(source)

    # Crop to everything ABOVE the split, then flood, then trim to the
    # artwork's true alpha bounds. Cropping before the flood keeps the
    # wordmark from being reached at all.
    upper = flood_background_to_transparent(source.crop((0, 0, source.width, split)))
    bounds = upper.getbbox()
    if bounds is None:
        raise SystemExit("the emblem region came out empty")
    emblem = upper.crop(bounds)

    # Centre on a square canvas with a little breathing room. Squaring by
    # PADDING rather than by expanding the crop is the whole point -- see
    # this module's docstring.
    side = int(max(emblem.size) * 1.10)
    canvas = Image.new("RGBA", (side, side), (255, 255, 255, 0))
    canvas.alpha_composite(
        emblem, ((side - emblem.width) // 2, (side - emblem.height) // 2)
    )

    # ONLY the four declared sizes are written. The full-resolution square
    # (389KB) and the transparent lockup (280KB) were both produced by an
    # earlier version of this script and neither had a caller: the panel and
    # the popup render the mark at 18-20px, so icon-48 is already more than
    # they need, and the one surface with room for the wordmark (the .docx
    # report) reads AICodeQuestLogo.png straight from the repo root. Nothing
    # unused goes into a bundle that already warns about its size on every
    # build.
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for size in SIZES:
        canvas.resize((size, size), Image.LANCZOS).save(OUT_DIR / f"icon-{size}.png")

    print(f"emblem split at y={split}; emblem {emblem.size}, square {canvas.size}")
    for path in sorted(OUT_DIR.iterdir()):
        print(f"  {path.relative_to(REPO)}  {path.stat().st_size // 1024}KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
