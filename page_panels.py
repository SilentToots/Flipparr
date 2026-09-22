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
    mask = ink_mask(image)
    gutter = max(2, int(round(min(width, height) * GUTTER_MIN_FRACTION)))
    min_area = width * height * PANEL_MIN_AREA
    boxes = _cut(mask, (0, 0, width, height), gutter, min_area)
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


def parse_vision_boxes(text: str, width: int, height: int) -> list[dict[str, Any]]:
    """The model's boxes as normalised rectangles; anything unusable is dropped."""
    items = _first_json_array(text)
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
    items = _first_json_array(text)
    try:
        order = [int(item) for item in items]
    except (TypeError, ValueError):
        return None
    return order if sorted(order) == list(range(1, count + 1)) else None


def _first_json_array(text: str) -> list[Any]:
    import json  # noqa: PLC0415

    start = text.find("[")
    if start == -1:
        return []
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
                    return []
                return parsed if isinstance(parsed, list) else []
    return []


def _norm_overlap(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Intersection over union of two normalised rectangles."""
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union else 0.0
