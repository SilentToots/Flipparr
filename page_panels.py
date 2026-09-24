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

# The optional detector: a YOLO26-nano fine-tuned for panels (Manga109-s),
# run through ONNX Runtime on the CPU. Only boxes this sure are allowed to
# split a region; below it the model doubles and fragments. Two boxes that
# overlap this much are one panel seen twice, and a box has to sit this far
# inside a region to count as one of its panels.
MODEL_INPUT = 1024
MODEL_CONFIDENCE = 0.5
MODEL_OVERLAP = 0.5
MODEL_INSIDE = 0.6
MODEL_CLASS_PANEL = 0
# How much of its own box the ink the model missed has to fill to be a panel,
# and how much of a row (or column) has to be inked for it to be in a band.
REMAINDER_DENSITY = 0.5
LEFTOVER_BAND_INK = 0.3


def detect_panels(image: Image.Image, session: Any = None, detail: Image.Image | None = None) -> dict[str, Any]:
    """The panels on a page, unordered, or the admission that there are none.

    Returns `{"segmented": bool, "source": "auto" | "model", "panels": [...]}`.
    A page that could not be read -- a splash, borderless art, a spread, a
    page whose gutters the ink runs across -- comes back unsegmented with no
    panels, and the reader shows its four quadrants instead. Better a known
    fallback than a wrong grid.

    With a detector `session` (see `load_model_session`) the model's boxes
    refine the cut: a region the gutters left coarse is split by the confident
    boxes inside it, and the ink those boxes leave uncovered becomes a panel
    of its own. The model reads `detail` when given -- the same page at a
    higher resolution, since it was exported at 1024px so that thin panels
    are not lost -- and its boxes are scaled into the cut's space. `source`
    says "model" whenever the model was consulted, changed or not: a reading
    made without it is redone once it is there, and one made with it is not. The model never replaces the cut outright, because a model
    can leave a panel out -- seen on a real page, where it dropped the first
    panel and doubled the fourth -- and a panel skipped is worse than two
    merged. `source` says "model" when the model changed the answer.
    """
    width, height = image.size
    if not width or not height or width > height * SPREAD_RATIO:
        return {"segmented": False, "source": "auto", "panels": []}
    mask, boxes, gutter, min_area = _read(image)
    source = "auto" if session is None else "model"
    if session is not None:
        looked_at = detail if detail is not None else image
        found = [
            (
                int(x0 * width / looked_at.width), int(y0 * height / looked_at.height),
                int(x1 * width / looked_at.width), int(y1 * height / looked_at.height),
            )
            for x0, y0, x1, y1 in model_boxes(session, looked_at)
        ]
        if found:
            refined = refine(mask, boxes or [mask.getbbox() or (0, 0, width, height)], found, min_area)
            if refined != boxes:
                boxes = refined
    plausible = _plausible(mask, boxes)
    panels = [
        {"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}
        for x0, y0, x1, y1 in boxes
    ] if plausible else []
    return {"segmented": plausible, "source": source, "panels": panels}


def ink_mask(image: Image.Image, background: int | None = None) -> Image.Image:
    """Ink as white on black: what is drawn, against whatever the page is printed on.

    The background is read off the page's own border rather than assumed
    white -- a manga scan can be off-white, an inverted page black -- and ink
    is anything far enough from it. No dilation: at 600px a printed border is
    already a solid line, and thickening the ink was closing the gutters,
    which are only a few pixels wide at that size. `background` names the
    level outright when the caller has a better idea than the border.
    """
    grey = ImageOps.grayscale(image)
    if background is None:
        background = _border_level(grey)
    if background > 127:
        return grey.point(lambda value: 255 if value < background - INK_DELTA else 0)
    return grey.point(lambda value: 255 if value > background + INK_DELTA else 0)


def _read(image: Image.Image) -> tuple[Image.Image, list[tuple[int, int, int, int]], int, float]:
    """The mask the page is best read with, and the cut it gives.

    The border says what a page is printed on, and it is usually right. A
    page whose art bleeds dark to every edge reads as printed on black, and
    its white gutters then count as ink: the cut finds nothing, or dark bands
    that are not gutters -- an eight-panel page with clean white gutters went
    to the fallback that way. So a page not read as white is tried as white
    too, and the reading that found a layout is kept; between two layouts,
    the one with more panels. A truly dark page, an inverted scan, finds
    nothing under white and keeps its own reading.
    """
    width, height = image.size
    gutter = max(2, int(round(min(width, height) * GUTTER_MIN_FRACTION)))
    min_area = width * height * PANEL_MIN_AREA
    level = _border_level(ImageOps.grayscale(image))
    readings = []
    for background in ([level] if level == 255 else [level, 255]):
        mask = ink_mask(image, background)
        boxes = _cut(mask, (0, 0, width, height), gutter, min_area)
        readings.append((mask, boxes))
    # `max` keeps the first of equals: the border's own reading, on a tie.
    mask, boxes = max(readings, key=lambda reading: (_plausible(*reading), len(reading[1])))
    return mask, boxes, gutter, min_area


def page_mask(image: Image.Image) -> Image.Image:
    """The ink mask `detect_panels` read this page with."""
    return _read(image)[0]


def order_panels(panels: list[dict[str, Any]], direction: str = READING_LTR) -> list[dict[str, Any]]:
    """Reading order: rows top to bottom, then across each row the way the run reads.

    Two panels share a row when their vertical spans overlap by more than half
    the shorter one -- a tall panel beside two stacked ones is one row with
    the tall panel, then the stacked pair's row continues below it. Row-major
    reads most pages right; the insets and diagonals it gets wrong are what a
    manual pass is for.
    """
    # An order a vision model (or a person) gave stands; the row rule is for
    # panels nobody has ordered.
    if panels and all(isinstance(panel.get("order"), int) for panel in panels):
        return sorted(panels, key=lambda panel: panel["order"])
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


def load_model_session(path: str | None) -> Any:
    """An ONNX Runtime session for the detector at `path`, or None.

    None whenever the tier is not there -- no path, no file, no runtime -- so
    the caller carries on with the gutter finder alone. The runtime is an
    optional install (requirements-panels.txt), never a requirement.
    """
    if not path:
        return None
    try:
        import onnxruntime  # noqa: PLC0415 -- optional, probed at startup
    except ImportError:
        return None
    try:
        return onnxruntime.InferenceSession(path, providers=["CPUExecutionProvider"])
    except Exception:  # noqa: BLE001 -- a broken model file is a missing tier, not an error page
        return None


def model_boxes(session: Any, image: Image.Image, confidence: float = MODEL_CONFIDENCE) -> list[tuple[int, int, int, int]]:
    """The detector's panel boxes on `image`, in its pixels, confident ones only, one per panel.

    The page is letterboxed into the model's square, the boxes mapped back
    and clipped, text boxes dropped, and boxes that are one panel seen twice
    reduced to the surer one.
    """
    import numpy  # noqa: PLC0415 -- comes with onnxruntime; not needed without it

    width, height = image.size
    scale = min(MODEL_INPUT / width, MODEL_INPUT / height)
    fitted = (max(1, round(width * scale)), max(1, round(height * scale)))
    canvas = Image.new("RGB", (MODEL_INPUT, MODEL_INPUT), (114, 114, 114))
    offset = ((MODEL_INPUT - fitted[0]) // 2, (MODEL_INPUT - fitted[1]) // 2)
    canvas.paste(image.convert("RGB").resize(fitted, Image.Resampling.BILINEAR), offset)
    tensor = numpy.asarray(canvas, dtype=numpy.float32).transpose(2, 0, 1)[None] / 255.0
    name = session.get_inputs()[0].name
    rows = session.run(None, {name: tensor})[0][0]
    scored = []
    for x1, y1, x2, y2, score, cls in rows.tolist():
        if score < confidence or int(cls) != MODEL_CLASS_PANEL:
            continue
        box = (
            int(max(0, min(width, (x1 - offset[0]) / scale))), int(max(0, min(height, (y1 - offset[1]) / scale))),
            int(max(0, min(width, (x2 - offset[0]) / scale))), int(max(0, min(height, (y2 - offset[1]) / scale))),
        )
        if box[2] > box[0] and box[3] > box[1]:
            scored.append((float(score), box))
    return suppress(scored)


def suppress(scored: list[tuple[float, tuple[int, int, int, int]]]) -> list[tuple[int, int, int, int]]:
    """One box per panel: of two that mostly overlap, the surer stays."""
    kept: list[tuple[int, int, int, int]] = []
    for _, box in sorted(scored, key=lambda item: -item[0]):
        if all(_overlap(box, other) < MODEL_OVERLAP for other in kept):
            kept.append(box)
    return kept


def refine(
    mask: Image.Image, regions: list[tuple[int, int, int, int]],
    boxes: list[tuple[int, int, int, int]], min_area: float,
) -> list[tuple[int, int, int, int]]:
    """The cut's regions, each split by the model's boxes that sit inside it.

    A region with two or more boxes inside becomes those boxes, plus whatever
    inked part of it they leave uncovered if that part is big enough to be a
    panel -- so a panel the model did not see is kept rather than lost. A
    region with one box or none is left exactly as the cut found it.
    """
    result: list[tuple[int, int, int, int]] = []
    for region in regions:
        # A confident box is a whole panel by construction, so its floor is a
        # quarter of a cut leaf's: a manga page's narrow reaction panels are
        # real, and smaller than 4% of the page.
        inside = [
            _clip(box, region) for box in boxes
            if _share(box, region) >= MODEL_INSIDE and _area(_clip(box, region)) >= min_area / 4
        ]
        if len(inside) < 2:
            result.append(region)
            continue
        result.extend(inside)
        remainder = mask.crop(region).copy()
        # Each box is painted out with a margin, so the slivers of ink beside
        # it -- a border the box cut inside of, the wedge by a slanted gutter
        # -- go with it rather than stretching what is left across the region.
        pad = max(4, int(0.05 * min(region[2] - region[0], region[3] - region[1])))
        for x0, y0, x1, y1 in inside:
            remainder.paste(0, (x0 - region[0] - pad, y0 - region[1] - pad, x1 - region[0] + pad, y1 - region[1] + pad))
        result.extend(
            (region[0] + x0, region[1] + y0, region[0] + x1, region[1] + y1)
            for x0, y0, x1, y1 in _leftover_panels(remainder, min_area)
        )
    return result


def _leftover_panels(remainder: Image.Image, min_area: float) -> list[tuple[int, int, int, int]]:
    """The panels in what the model's boxes left uncovered.

    Read as bands, the way the cut reads a page: runs of rows with a panel's
    worth of ink across them, else runs of columns. A box around everything
    left would not do -- a strip of bleed down one edge beside a box the
    model drew a little inside the art stretches such a box over the whole
    region, and the panel above it is lost in the average. Each band has to
    hold a panel's worth of ink and be dense with it.
    """
    width, height = remainder.size
    columns, rows = _ink_profiles(remainder)
    limit = 255 * LEFTOVER_BAND_INK
    for profile, along_rows in ((rows, True), (columns, False)):
        found: list[tuple[int, int, int, int]] = []
        start: int | None = None
        for index, value in enumerate(list(profile) + [0]):
            if value >= limit:
                start = index if start is None else start
                continue
            if start is None:
                continue
            band = remainder.crop((0, start, width, index) if along_rows else (start, 0, index, height))
            bbox = band.getbbox()
            ink = band.histogram()[255]
            if bbox and ink >= min_area and ink >= REMAINDER_DENSITY * _area(bbox):
                found.append(
                    (bbox[0], start + bbox[1], bbox[2], start + bbox[3]) if along_rows
                    else (start + bbox[0], bbox[1], start + bbox[2], bbox[3])
                )
            start = None
        if found:
            return found
    return []


def _clip(box, region):
    return (max(box[0], region[0]), max(box[1], region[1]), min(box[2], region[2]), min(box[3], region[3]))


def _area(box) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _share(box, region) -> float:
    """How much of `box` lies inside `region`, 0-1."""
    area = _area(box)
    return _area(_clip(box, region)) / area if area else 0.0


def _overlap(a, b) -> float:
    """Intersection over union."""
    inter = _area(_clip(a, b))
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


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
    if not width or not height:
        return [], []
    # A one-line "L" image's bytes are its values, in order.
    columns = list(mask.resize((width, 1), Image.Resampling.BOX).tobytes())
    rows = list(mask.resize((1, height), Image.Resampling.BOX).tobytes())
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


# ---- A vision model, as an optional connector ---------------------------------
#
# Claude (and, behind the same seam, any vision model) is consulted only where
# the tiers below gave up or cannot know: the boxes on a page nothing could
# read, and the reading order of a layout row-major cannot settle. Anthropic's
# own guidance is that localisation is approximate and best asked for in
# absolute pixels of the sent image, so that is how it is asked; the answer
# is normalised here and accepted only if it looks like a layout.

VISION_COVERAGE_MIN = 0.6
VISION_OVERLAP_MAX = 0.2
# What a corrected answer must account for of the page's ink, or it is not a
# reading. Sonnet 5 on a spread: six panels exact to their frames, and three
# missed -- the central figure among them -- at two thirds covered. A reader
# stepping past a third of a page without knowing is worse off than with
# the quadrants.
VISION_READ_MIN = 0.75
# How far a model's edge may sit from the gutter it meant, as a fraction of
# the page's side. Measured 2026-09-22: Sonnet 5 within 2% on a clean page's
# rows and 8.5% on its one thin column; gpt-5-mini 10-25% off on the same
# page -- the same structure, drawn in the wrong place. Past this the edge
# is not near anything it could have meant.
VISION_SNAP = 0.1
# Ink past a region's edge, along a panel that ends there: more than this
# and the panel goes on -- the model's edge ran through it. A balloon or a
# caption crossing a real gutter is a short stretch of ink, not most of one.
VISION_LINE_INK = 0.5
# The share of an answer's edges (four to a box) the page may fail to vouch
# for before the answer is a guess. Sonnet 5 on a real page: two of
# thirty-two, both on one boundary drawn by colour alone. gpt-4o-mini's grid
# through the same page: eight of twenty-four.
VISION_UNVERIFIED_MAX = 0.1
# How much ink a line may carry and still be the gutter a model's edge meant:
# the same share as VISION_LINE_INK, so that every line is one or the other.
# A caption across a gutter is a third of it; art is nearly all of a line.
VISION_GUTTER_INK = VISION_LINE_INK
# A pair of panels whose vertical spans overlap by this much of the shorter
# one -- neither clearly one row nor clearly two -- is what row-major gets
# wrong: an inset, a diagonal, a stagger.
AMBIGUOUS_OVERLAP = (0.2, 0.8)


def vision_boxes_prompt(width: int, height: int, direction: str = READING_LTR) -> str:
    reads = "right to left, like manga" if direction == READING_RTL else "left to right"
    return (
        f"This is one page of a comic, {width} pixels wide and {height} pixels tall, read {reads}. "
        "List every story panel on it, in reading order, as a JSON array and nothing else. "
        "Each item is an object with integer pixel coordinates in this image: "
        '{"x1": left, "y1": top, "x2": right, "y2": bottom}. '
        "Only the drawn panels: not speech balloons, captions, page numbers or the page border. "
        "If the page is a single image with no panels, answer []."
    )


# The page as one panel: what a splash, a cover or a pin-up is. A vision
# model that answers "no panels" has read the page, and this is its reading.
WHOLE_PAGE: dict[str, float] = {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}


def parse_vision_boxes(text: str, width: int, height: int) -> list[dict[str, Any]] | None:
    """The model's boxes as normalised rectangles; anything unusable is dropped.

    None when the answer holds no list at all -- a refusal, an apology, a
    timeout's empty body -- which is not the same as the empty list the
    prompt asks for on a page with no panels. One means the model did not
    answer; the other is an answer.
    """
    items = _first_json_array(text)
    if items is None:
        return None
    boxes: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            if all(key in item for key in ("x1", "y1", "x2", "y2")):
                x0, y0, x1, y1 = (float(item["x1"]), float(item["y1"]), float(item["x2"]), float(item["y2"]))
            elif all(key in item for key in ("x", "y", "w", "h")):
                x0, y0 = float(item["x"]), float(item["y"])
                x1, y1 = x0 + float(item["w"]), y0 + float(item["h"])
            else:
                continue
        except (TypeError, ValueError):
            continue
        x0, x1 = sorted((min(max(x0, 0.0), width), min(max(x1, 0.0), width)))
        y0, y1 = sorted((min(max(y0, 0.0), height), min(max(y1, 0.0), height)))
        if x1 - x0 < 1 or y1 - y0 < 1 or not width or not height:
            continue
        boxes.append({"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height})
    return boxes


def accept_vision_boxes(boxes: list[dict[str, Any]]) -> bool:
    """Whether the model's boxes read as a layout: enough of the page, and not on top of each other."""
    if len(boxes) < 2:
        return False
    if sum(box["w"] * box["h"] for box in boxes) < VISION_COVERAGE_MIN:
        return False
    for index, a in enumerate(boxes):
        for b in boxes[index + 1:]:
            if _norm_overlap(a, b) > VISION_OVERLAP_MAX:
                return False
    return True


def refine_vision_boxes(mask: Image.Image, boxes: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """A model's boxes, made exact by the page itself -- or refused.

    A vision model reads a layout's structure well and its coordinates
    approximately (its makers say as much), and coverage and overlap alone
    let a grid guessed over the art through. So the page corrects the
    model's edges and the model's structure stands: one box is one panel.
    The page cannot tell a gutter from a band of cloud -- cutting inside a
    box carved a patch of sky out of a painted page, the very page a model
    is for -- so it never splits what the model drew as one. Each side of a
    box is read for the barest line near where the model put it: a gutter,
    thin or crossed by a caption, and the panel's ink then settles the edge
    exactly. A side with nothing bare near it and ink going on past it is a
    boundary the art alone draws, or an edge the model made up; one or two
    in an answer are the first and stand where the model put them, an
    answer full of them is a grid guessed over the art, and is refused
    whole. Better the quadrants than a confident wrong grid.
    """
    width, height = mask.size
    min_area = width * height * PANEL_MIN_AREA
    reach = (int(width * VISION_SNAP), int(height * VISION_SNAP))
    panels: list[tuple[int, int, int, int]] = []
    unverified = 0
    for box in boxes:
        drawn = (
            int(round(box["x"] * width)), int(round(box["y"] * height)),
            int(round((box["x"] + box["w"]) * width)), int(round((box["y"] + box["h"]) * height)),
        )
        region = (
            max(0, drawn[0] - reach[0]), max(0, drawn[1] - reach[1]),
            min(width, drawn[2] + reach[0]), min(height, drawn[3] + reach[1]),
        )
        if region[2] - region[0] < 2 or region[3] - region[1] < 2:
            return None
        settled, doubted = _settle(mask, drawn, region, reach)
        unverified += doubted
        if _area(settled) < min_area:
            continue
        if all(_iou(settled, kept) < 0.5 for kept in panels):
            panels.append(settled)
    # One doubt is always allowed: a two-box answer whose shared edge a
    # caption runs across is not a guess.
    if not panels or unverified > max(1, VISION_UNVERIFIED_MAX * 4 * len(boxes)):
        return None
    page = mask.getbbox() or (0, 0, width, height)
    page_area = (page[2] - page[0]) * (page[3] - page[1])
    if page_area <= 0 or sum(_area(panel) for panel in panels) / page_area < VISION_READ_MIN:
        return None
    return [
        {"x": x0 / width, "y": y0 / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}
        for x0, y0, x1, y1 in panels
    ]


def _settle(
    mask: Image.Image, drawn: tuple[int, int, int, int], region: tuple[int, int, int, int], reach: tuple[int, int],
) -> tuple[tuple[int, int, int, int], int]:
    """One box's panel, each side accounted for, and how many sides the page could not vouch for.

    A side at the page's edge is the page's edge. Any other is read along
    the model's edge for the barest line within reach and settles there --
    then the ink inside settles it exactly. Nothing bare near it: if the ink
    ends within reach anyway the panel simply ends there; if it goes on, the
    side is the model's word alone, takes the model's edge, and is counted.
    """
    width, height = mask.size
    edges = list(region)
    doubted = 0
    for side in range(4):
        vertical = side % 2 == 0
        low = side < 2
        limit = width if vertical else height
        if region[side] == (0 if low else limit):
            continue
        span = (drawn[1], drawn[3]) if vertical else (drawn[0], drawn[2])
        line = _bare_line_near(mask, side, drawn[side], span, reach[0] if vertical else reach[1])
        if line is not None:
            edges[side] = line
            continue
        if _ink_along(mask, side, region[side] - (1 if low else 0), span) <= VISION_LINE_INK:
            continue
        edges[side] = max(0, min(limit, drawn[side]))
        doubted += 1
    x0, y0, x1, y1 = edges
    if x1 - x0 < 2 or y1 - y0 < 2:
        return (0, 0, 0, 0), doubted
    bbox = mask.crop((x0, y0, x1, y1)).getbbox()
    if bbox is None:
        return (0, 0, 0, 0), doubted
    return (x0 + bbox[0], y0 + bbox[1], x0 + bbox[2], y0 + bbox[3]), doubted


def _ink_along(mask: Image.Image, side: int, line: int, span: tuple[int, int]) -> float:
    """The share of one line, across a span, that is ink."""
    width, height = mask.size
    vertical = side % 2 == 0
    if not (0 <= line < (width if vertical else height)) or span[1] - span[0] < 1:
        return 0.0
    strip = mask.crop((line, span[0], line + 1, span[1]) if vertical else (span[0], line, span[1], line + 1))
    return strip.histogram()[255] / max(1, strip.size[0] * strip.size[1])


def _bare_line_near(mask: Image.Image, side: int, edge: int, span: tuple[int, int], reach: int) -> int | None:
    """The barest line within reach of the model's edge, across the middle of the span -- the thin gutter it meant."""
    width, height = mask.size
    vertical = side % 2 == 0
    limit = width if vertical else height
    inset = int((span[1] - span[0]) * 0.1)
    lo, hi = span[0] + inset, span[1] - inset
    if hi - lo < 1:
        return None
    strip = mask.crop((0, lo, width, hi) if vertical else (lo, 0, hi, height))
    profile = _ink_profiles(strip)[0 if vertical else 1]
    best: tuple[int, int, int] | None = None
    for line in range(max(0, edge - reach), min(limit, edge + reach + 1)):
        if profile[line] <= 255 * VISION_GUTTER_INK:
            score = (profile[line], abs(line - edge), line)
            if best is None or score < best:
                best = score
    return None if best is None else best[2]


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    inter = _area(_clip(a, b))
    union = _area(a) + _area(b) - inter
    return inter / union if union else 0.0


def ambiguous_layout(panels: list[dict[str, Any]]) -> bool:
    """Whether row-major ordering is a guess on this page."""
    low, high = AMBIGUOUS_OVERLAP
    for index, a in enumerate(panels):
        for b in panels[index + 1:]:
            shorter = min(a["h"], b["h"])
            if not shorter:
                continue
            overlap = (min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])) / shorter
            if low < overlap < high:
                return True
    return False


def vision_order_prompt(panels: list[dict[str, Any]], direction: str = READING_LTR) -> str:
    reads = "right to left, like manga" if direction == READING_RTL else "left to right"
    listed = "; ".join(
        f"panel {index + 1}: left {panel['x']:.2f}, top {panel['y']:.2f}, width {panel['w']:.2f}, height {panel['h']:.2f}"
        for index, panel in enumerate(panels)
    )
    return (
        f"This is one page of a comic, read {reads}. Its panels have been found and numbered; "
        f"positions are fractions of the page's width and height: {listed}. "
        "In what order should they be read? Answer with a JSON array of the panel numbers "
        "in reading order and nothing else."
    )


def parse_vision_order(text: str, count: int) -> list[int] | None:
    """A permutation of 1..count from the model's answer, or None."""
    items = _first_json_array(text) or []
    try:
        order = [int(item) for item in items]
    except (TypeError, ValueError):
        return None
    return order if sorted(order) == list(range(1, count + 1)) else None


def _first_json_array(text: str) -> list[Any] | None:
    """The first JSON array in the text, or None when there is not one."""
    import json  # noqa: PLC0415

    start = text.find("[")
    if start == -1:
        return None
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "[":
            depth += 1
        elif text[index] == "]":
            depth -= 1
            if depth == 0:
                try:
                    parsed = json.loads(text[start:index + 1])
                except ValueError:
                    return None
                return parsed if isinstance(parsed, list) else None
    return None


def _norm_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Intersection over union of two normalised rectangles."""
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0


# ---- Spreads: two pages, read as two -----------------------------------------
#
# A double-page spread is two pages side by side, and every tier reads it
# badly as one: the cut refuses it outright, a detector trained on pages
# halves its resolution, and a vision model asked about the whole gets the
# structure and loses a third of the panels (Sonnet 5, 2026-09-22: six exact,
# three missed). So a spread is read as its two halves, each through the
# same tiers as a page, and the halves put back together: a panel that
# meets the fold from both sides with the same top and bottom is one panel
# across it, and the order is the first-read page whole, then the other.

# How far from the fold a panel's edge may sit and still meet it, as a
# share of the spread's width: a page's inner margin is wider than this,
# so two facing panels each inside their own margin are not joined.
FOLD_REACH = 0.015
# How much of the taller of two panels meeting at the fold the shorter one
# has to span for them to be one panel: the same top and bottom, near enough.
FOLD_MATCH = 0.9


def is_spread(width: int, height: int) -> bool:
    return width > height * SPREAD_RATIO


def spread_halves(image: Image.Image) -> list[Image.Image]:
    """The left and right pages of a spread, as two images."""
    width, height = image.size
    middle = width // 2
    return [image.crop((0, 0, middle, height)), image.crop((middle, 0, width, height))]


def place_half(panels: list[dict[str, Any]], side: int) -> list[dict[str, Any]]:
    """Panels read in one half's own space, placed on the spread (side 0 left, 1 right)."""
    return [
        {**panel, "x": panel["x"] * 0.5 + side * 0.5, "w": panel["w"] * 0.5}
        for panel in panels
    ]


def join_across_fold(panels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Two panels that meet at the fold with the same top and bottom, made one."""
    joined: list[dict[str, Any]] = []
    used: set[int] = set()
    for index, left in enumerate(panels):
        if abs(left["x"] + left["w"] - 0.5) > FOLD_REACH:
            continue
        for other, right in enumerate(panels):
            if other in used or other == index or abs(right["x"] - 0.5) > FOLD_REACH:
                continue
            overlap = min(left["y"] + left["h"], right["y"] + right["h"]) - max(left["y"], right["y"])
            if overlap >= FOLD_MATCH * max(left["h"], right["h"]):
                top = min(left["y"], right["y"])
                bottom = max(left["y"] + left["h"], right["y"] + right["h"])
                joined.append({"x": left["x"], "y": top, "w": right["x"] + right["w"] - left["x"], "h": bottom - top})
                used.update((index, other))
                break
    return [panel for index, panel in enumerate(panels) if index not in used] + joined


def order_spread(panels: list[dict[str, Any]], direction: str = READING_LTR) -> list[dict[str, Any]]:
    """Reading order across a spread, stamped as `order`.

    The page read first is read whole, then the other -- row-major across
    the whole spread would weave the two pages' rows together. A panel
    across the fold belongs to the page read first, at its row there.
    """
    first = 1 if direction == READING_RTL else 0
    groups: dict[int, list[dict[str, Any]]] = {0: [], 1: []}
    for panel in panels:
        crosses = panel["x"] < 0.5 - FOLD_REACH and panel["x"] + panel["w"] > 0.5 + FOLD_REACH
        side = first if crosses else (0 if panel["x"] + panel["w"] / 2 < 0.5 else 1)
        groups[side].append({key: value for key, value in panel.items() if key != "order"})
    ordered: list[dict[str, Any]] = []
    for side in ((1, 0) if first else (0, 1)):
        ordered.extend(order_panels(groups[side], direction))
    return [{**panel, "order": position} for position, panel in enumerate(ordered)]


def combine_halves(readings: list[dict[str, Any]], direction: str = READING_LTR) -> dict[str, Any]:
    """The two halves' readings as one spread's.

    A half nothing could read stands as its own quadrants beside a half that
    was read; two unread halves are an unread spread, and the reader shows
    its quadrants. The source is the highest tier that answered on either
    half, so the spread is redone on the same terms as a page.
    """
    tiers = ("auto", "model", "vlm")
    source = max((reading["source"] for reading in readings), key=tiers.index)
    if not any(reading["segmented"] for reading in readings):
        return {"segmented": False, "source": source, "panels": []}
    panels: list[dict[str, Any]] = []
    for side, reading in enumerate(readings):
        half = reading["panels"] if reading["segmented"] else quadrant_panels(direction)
        panels.extend(place_half(half, side))
    return {"segmented": True, "source": source, "panels": order_spread(join_across_fold(panels), direction)}
