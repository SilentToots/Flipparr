"""Where the panels are on a comic page.

Kindle's Panel View and Comixology's Guided View step through a page one panel
at a time. Publishers author that data; nothing we ingest carries it, so the
page has to be read for it. This is the gutter finder: the same idea as Kindle
Comic Creator's auto-detect and Kumiko's contour pass, done with Pillow alone,
because Pillow is the whole image toolbox the shipped image has.

Pure functions on PIL images. Nothing here touches an archive, a cache or the
database; `app.py` renders the page and stores the answer.

Coordinates are normalised (0-1 of the page's width and height) everywhere, so
an answer computed on the 600px render applies to the 1800px one the reader
shows. A pixel value leaving this module is a bug.

What it cannot do, seen on real pages: two panels whose art abuts with no white
between them read as one, and a diagonal gutter is invisible to a cut that only
runs straight. Both come back as a larger panel, which still reads; the model
tiers and the manual pass exist for them.
"""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageOps

READING_LTR = "ltr"
READING_RTL = "rtl"

# A gutter is a run of near-empty rows or columns at least this fraction of
# the page's shorter side wide: 3px on the 600px render. Measured on real
# pages from the library, not chosen: at 1% a manga page's middle row of three
# read as one panel; at 0.5% the same. 0.7% is where thin printed gutters
# clear and a gap between letters still does not.
GUTTER_MIN_FRACTION = 0.007
# A region smaller than this fraction of the page is not a panel: a caption
# box, a page number, a bit of art that leaked across a gutter.
PANEL_MIN_AREA = 0.04
# How far from the background a pixel has to sit to count as ink.
INK_DELTA = 40
# A gutter line may carry this fraction of ink across its length: a balloon's
# outline or a speed line crossing a gutter is a few pixels wide, a panel is
# ink from edge to edge. Kindle's auto-detect wants clean gutters; real pages
# rarely have them.
GUTTER_INK_TOLERANCE = 0.03
# The share of a page's border that has to be near white for the page to be
# read as printed on white, whatever art bleeds to its edges.
WHITE_BORDER_SHARE = 0.1
# The page's panels have to account for this much of its inked area, or the
# reading was a guess: two blobs on a painted page are not a layout.
COVERAGE_MIN = 0.7
# A double-page spread: the backdrop picker's rule (app.py, _page_is_spread).
SPREAD_RATIO = 1.15


def detect_panels(image: Image.Image) -> dict[str, Any]:
    """The panels on a page, unordered, or the admission that there are none.

    Returns `{"segmented": bool, "panels": [{"x", "y", "w", "h"}, ...]}`. A page
    that could not be read -- a splash, borderless art, a spread, a page whose
    gutters the ink runs across -- comes back unsegmented with no panels, and
    the reader shows its four quadrants instead. Better a known fallback than
    a wrong grid.
    """
    width, height = image.size
    if not width or not height or width > height * SPREAD_RATIO:
        return {"segmented": False, "panels": []}
    mask = ink_mask(image)
    gutter = max(2, int(round(min(width, height) * GUTTER_MIN_FRACTION)))
    min_area = width * height * PANEL_MIN_AREA
    boxes = _cut(mask, (0, 0, width, height), gutter, min_area)
    panels = [
        {"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}
        for x0, y0, x1, y1 in boxes
    ]
    return {"segmented": _plausible(mask, boxes), "panels": panels if _plausible(mask, boxes) else []}


def ink_mask(image: Image.Image) -> Image.Image:
    """Ink as white on black: what is drawn, against whatever the page is printed on.

    The background is read off the page's own border rather than assumed
    white -- a manga scan can be off-white, an inverted page black -- and ink
    is anything far enough from it. No dilation: at 600px a printed border is
    already a solid line, and thickening the ink was closing the gutters,
    which are only a few pixels wide at that size.
    """
    grey = ImageOps.grayscale(image)
    background = _border_level(grey)
    if background > 127:
        return grey.point(lambda value: 255 if value < background - INK_DELTA else 0)
    return grey.point(lambda value: 255 if value > background + INK_DELTA else 0)


def order_panels(panels: list[dict[str, Any]], direction: str = READING_LTR) -> list[dict[str, Any]]:
    """Reading order: rows top to bottom, then across each row the way the run reads.

    Two panels share a row when their vertical spans overlap by more than half
    the shorter one -- a tall panel beside two stacked ones is one row with
    the tall panel, then the stacked pair's row continues below it. Row-major
    reads most pages right; the insets and diagonals it gets wrong are what a
    manual pass is for.
    """
    remaining = sorted(panels, key=lambda panel: (panel["y"], panel["x"]))
    rows: list[list[dict[str, Any]]] = []
    for panel in remaining:
        for row in rows:
            anchor = row[0]
            overlap = min(anchor["y"] + anchor["h"], panel["y"] + panel["h"]) - max(anchor["y"], panel["y"])
            if overlap > 0.5 * min(anchor["h"], panel["h"]):
                row.append(panel)
                break
        else:
            rows.append([panel])
    rows.sort(key=lambda row: min(panel["y"] for panel in row))
    ordered: list[dict[str, Any]] = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda panel: panel["x"], reverse=direction == READING_RTL))
    return ordered


def quadrant_panels(direction: str = READING_LTR) -> list[dict[str, Any]]:
    """Kindle's Virtual Panels: the page in four, read in order, when nothing better is known."""
    rects = [
        {"x": 0.0, "y": 0.0, "w": 0.5, "h": 0.5}, {"x": 0.5, "y": 0.0, "w": 0.5, "h": 0.5},
        {"x": 0.0, "y": 0.5, "w": 0.5, "h": 0.5}, {"x": 0.5, "y": 0.5, "w": 0.5, "h": 0.5},
    ]
    return order_panels(rects, direction)


def _border_level(grey: Image.Image) -> int:
    """The page's own background, read off a band around its edge.

    A page printed on white stays white even when a panel bleeds to three of
    its edges: any meaningful share of near-white in the band settles it.
    Only a border with no white in it -- an inverted page, a dark scan -- is
    read by its median.
    """
    width, height = grey.size
    band = max(1, int(round(min(width, height) * 0.02)))
    histogram = [0] * 256
    for box in (
        (0, 0, width, band), (0, height - band, width, height),
        (0, 0, band, height), (width - band, 0, width, height),
    ):
        for level, count in enumerate(grey.crop(box).histogram()):
            histogram[level] += count
    total = sum(histogram) or 1
    light = sum(histogram[235:])
    if light / total >= WHITE_BORDER_SHARE:
        return 255
    seen = 0
    for level, count in enumerate(histogram):
        seen += count
        if seen * 2 >= total:
            return level
    return 255


def _cut(mask: Image.Image, box: tuple[int, int, int, int], gutter: int, min_area: float) -> list[tuple[int, int, int, int]]:
    """Recursive XY-cut: split a region on its widest gutter, then split the halves.

    The projections are read a gutter's width in from the region's edge, so a
    frame drawn around the whole page -- whose lines would put ink in every
    column -- does not hide the gutters inside it. The leaves keep their full
    inked extent, frame and all.
    """
    region = mask.crop(box)
    bbox = region.getbbox()
    if bbox is None:
        return []
    box = (box[0] + bbox[0], box[1] + bbox[1], box[0] + bbox[2], box[1] + bbox[3])
    width, height = box[2] - box[0], box[3] - box[1]
    if width * height < min_area:
        return []
    inset = gutter
    if width <= 2 * inset + 1 or height <= 2 * inset + 1:
        return [box]
    inner = mask.crop((box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset))
    columns, rows = _ink_profiles(inner)
    gap_x = _widest_gap(columns, gutter)
    gap_y = _widest_gap(rows, gutter)
    if gap_x is None and gap_y is None:
        return [box]
    if gap_y is not None and (gap_x is None or (gap_y[1] - gap_y[0]) >= (gap_x[1] - gap_x[0])):
        cut_start, cut_end = box[1] + inset + gap_y[0], box[1] + inset + gap_y[1]
        parts = [(box[0], box[1], box[2], cut_start), (box[0], cut_end, box[2], box[3])]
    else:
        cut_start, cut_end = box[0] + inset + gap_x[0], box[0] + inset + gap_x[1]
        parts = [(box[0], box[1], cut_start, box[3]), (cut_end, box[1], box[2], box[3])]
    return [leaf for part in parts for leaf in _cut(mask, part, gutter, min_area)]


def _ink_profiles(mask: Image.Image) -> tuple[list[int], list[int]]:
    """How much ink each column and each row carries, 0-255.

    Box resampling to one row and to one column averages the mask along each
    line, which is the projection `getprojection` gives without the tolerance
    a real gutter needs.
    """
    width, height = mask.size
    columns = list(mask.resize((width, 1), Image.Resampling.BOX).getdata()) if width and height else []
    rows = list(mask.resize((1, height), Image.Resampling.BOX).getdata()) if width and height else []
    return columns, rows


def _widest_gap(profile, minimum: int) -> tuple[int, int] | None:
    """The widest run of near-empty lines strictly inside a profile, at least `minimum` long."""
    best: tuple[int, int] | None = None
    start: int | None = None
    values = list(profile)
    limit = 255 * GUTTER_INK_TOLERANCE
    for index, value in enumerate(values + [255]):
        if value <= limit:
            if start is None:
                start = index
            continue
        if start is not None:
            if start > 0 and index < len(values) and index - start >= minimum:
                if best is None or index - start > best[1] - best[0]:
                    best = (start, index)
            start = None
    return best


def _plausible(mask: Image.Image, boxes: list[tuple[int, int, int, int]]) -> bool:
    """Whether the cut read a layout, or merely found some ink."""
    if len(boxes) < 2:
        return False
    page = mask.getbbox()
    if page is None:
        return False
    page_area = (page[2] - page[0]) * (page[3] - page[1])
    covered = sum((x1 - x0) * (y1 - y0) for x0, y0, x1, y1 in boxes)
    return page_area > 0 and covered / page_area >= COVERAGE_MIN
