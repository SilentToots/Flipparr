"""Where the panels are on a page: drawn fixtures, not shipped ones."""

import unittest

from PIL import Image, ImageDraw

from page_panels import (
    INK_UNCOVERED_MAX, add_leftover_panels, combine_halves, ink_outside, merge_overlapping,
    READING_LTR, READING_RTL, accept_vision_boxes, ambiguous_layout, detect_panels, ink_mask,
    load_model_session, order_panels, page_mask, parse_vision_boxes, parse_vision_order, quadrant_panels,
    refine, refine_vision_boxes, suppress, vision_boxes_prompt, vision_order_prompt,
)

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _page(width=600, height=900, background="white"):
    image = Image.new("RGB", (width, height), background)
    return image, ImageDraw.Draw(image)


def _frame(draw, box, fill="#555555", outline="black", width=3):
    # Art to the border, as on a printed page: a frame left white inside has
    # bare lines that are not gutters, and no page reads that way.
    draw.rectangle(box, fill=fill, outline=outline, width=width)
    x0, y0, x1, y1 = box
    draw.ellipse((x0 + 20, y0 + 20, x1 - 20, y1 - 20), fill="#333333")


def _grid(draw, columns, rows, width=600, height=900, margin=30, gutter=20):
    boxes = []
    cell_w = (width - 2 * margin - (columns - 1) * gutter) / columns
    cell_h = (height - 2 * margin - (rows - 1) * gutter) / rows
    for row in range(rows):
        for column in range(columns):
            x0 = margin + column * (cell_w + gutter)
            y0 = margin + row * (cell_h + gutter)
            box = (int(x0), int(y0), int(x0 + cell_w), int(y0 + cell_h))
            _frame(draw, box)
            boxes.append(box)
    return boxes


def _centres(panels):
    return [(round(panel["x"] + panel["w"] / 2, 2), round(panel["y"] + panel["h"] / 2, 2)) for panel in panels]


class DetectionTests(unittest.TestCase):
    def test_a_gridded_page_yields_its_panels_normalised(self):
        image, draw = _page()
        _grid(draw, 2, 3)
        result = detect_panels(image)
        self.assertTrue(result["segmented"])
        self.assertEqual(len(result["panels"]), 6)
        for panel in result["panels"]:
            for key in ("x", "y", "w", "h"):
                self.assertGreaterEqual(panel[key], 0.0)
                self.assertLessEqual(panel[key], 1.0)
            self.assertGreater(panel["w"], 0.3, "each panel is about half the page wide")
            self.assertGreater(panel["h"], 0.2)

    def test_a_frame_around_the_whole_page_does_not_hide_the_gutters(self):
        image, draw = _page()
        _grid(draw, 2, 3)
        draw.rectangle((10, 10, 589, 889), outline="black", width=3)
        self.assertEqual(len(detect_panels(image)["panels"]), 6)

    def test_an_off_white_scan_reads_the_same(self):
        image, draw = _page(background="#ece8dc")
        _grid(draw, 2, 2)
        self.assertEqual(len(detect_panels(image)["panels"]), 4)

    def test_a_tall_panel_beside_two_stacked_ones(self):
        image, draw = _page()
        _frame(draw, (30, 30, 290, 870))
        _frame(draw, (310, 30, 570, 440))
        _frame(draw, (310, 460, 570, 870))
        result = detect_panels(image)
        self.assertTrue(result["segmented"])
        self.assertEqual(len(result["panels"]), 3)

    def test_borderless_art_is_admitted_rather_than_guessed(self):
        """A painted page with no gutters: one blob of ink, which is not a layout."""
        image, draw = _page()
        draw.rectangle((0, 0, 599, 899), fill="#3a3a3a")
        self.assertEqual(detect_panels(image), {"segmented": False, "source": "auto", "panels": []})

    def test_a_page_bleeding_dark_to_its_edges_is_still_read_by_its_white_gutters(self):
        # Absolute Batman #2, page 3: eight panels of dark red art running to
        # the page's edges, clean white gutters between them. The border read
        # as dark, the gutters counted as ink, and the page went to quadrants.
        image, draw = _page()
        for box in _grid(draw, 2, 3, margin=0, gutter=16):
            draw.rectangle(box, fill="#3a0a0a")
        result = detect_panels(image)
        self.assertTrue(result["segmented"])
        self.assertEqual(len(result["panels"]), 6)

    def test_a_spread_is_not_cut(self):
        image, draw = _page(width=1200, height=900)
        _grid(draw, 4, 3, width=1200)
        self.assertEqual(detect_panels(image), {"segmented": False, "source": "auto", "panels": []})

    def test_a_spread_is_read_as_two_pages_and_put_back_together(self):
        left = {"segmented": True, "source": "auto", "panels": [
            {"x": 0.05, "y": 0.05, "w": 0.4, "h": 0.4}, {"x": 0.55, "y": 0.05, "w": 0.4, "h": 0.4},
            {"x": 0.05, "y": 0.55, "w": 0.9, "h": 0.4}]}
        right = {"segmented": True, "source": "model", "panels": [
            {"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.4}, {"x": 0.05, "y": 0.55, "w": 0.9, "h": 0.4}]}
        spread = combine_halves([left, right], "ltr")
        self.assertEqual((spread["segmented"], spread["source"], len(spread["panels"])), (True, "model", 5))
        ordered = order_panels(spread["panels"], "ltr")
        self.assertEqual([round(panel["x"], 3) for panel in ordered], [0.025, 0.275, 0.025, 0.525, 0.525], "the left page whole, then the right")
        self.assertEqual([round(panel["w"], 3) for panel in ordered][:2], [0.2, 0.2], "placed at half width")
        manga = order_panels(combine_halves([left, right], "rtl")["panels"], "rtl")
        self.assertEqual([round(panel["x"], 3) for panel in manga][:2], [0.525, 0.525], "a manga spread starts on the right page")
        # A half nothing could read stands as its own quadrants beside the other.
        one_sided = combine_halves([left, {"segmented": False, "source": "auto", "panels": []}], "ltr")
        self.assertEqual(len(one_sided["panels"]), 7)
        self.assertEqual(combine_halves([{"segmented": False, "source": "vlm", "panels": []}] * 2, "ltr"),
                         {"segmented": False, "source": "vlm", "panels": []})

    def test_a_panel_across_the_fold_is_one_panel_read_first(self):
        # A wide panel across the top of both pages, and a panel under it on each.
        left = {"segmented": True, "source": "auto", "panels": [
            {"x": 0.05, "y": 0.05, "w": 0.95, "h": 0.4}, {"x": 0.05, "y": 0.55, "w": 0.9, "h": 0.4}]}
        right = {"segmented": True, "source": "auto", "panels": [
            {"x": 0.0, "y": 0.05, "w": 0.95, "h": 0.4}, {"x": 0.05, "y": 0.55, "w": 0.9, "h": 0.4}]}
        ordered = order_panels(combine_halves([left, right], "ltr")["panels"], "ltr")
        self.assertEqual(len(ordered), 3)
        self.assertEqual((round(ordered[0]["x"], 3), round(ordered[0]["w"], 3)), (0.025, 0.95), "one panel, fold to fold")
        self.assertLess(ordered[1]["x"], 0.5)
        self.assertGreater(ordered[2]["x"], 0.5)
        # Two facing panels each inside their page's margin are not joined.
        apart = combine_halves([
            {"segmented": True, "source": "auto", "panels": [{"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9}]},
            {"segmented": True, "source": "auto", "panels": [{"x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9}]}], "ltr")
        self.assertEqual(len(apart["panels"]), 2)

    def test_what_no_panel_covers_comes_back_as_panels(self):
        # A page of two rows: one wide panel above a row of three. A reading
        # that has only the top panel gets the row back, cut into its three.
        image, draw = _page()
        _frame(draw, (30, 30, 570, 420))
        for box in _grid(draw, 3, 1, height=900, margin=30, gutter=20):
            _frame(draw, (box[0], 450, box[2], 870))
        mask = page_mask(image)
        top_only = [{"x": 0.05, "y": 30 / 900, "w": 0.9, "h": 390 / 900}]
        self.assertGreater(ink_outside(mask, top_only), 0.4)
        whole = add_leftover_panels(mask, top_only)
        self.assertEqual(len(whole), 4)
        self.assertEqual([round(panel["x"], 2) for panel in whole[1:]], [0.05, 0.36, 0.67])
        self.assertLess(ink_outside(mask, whole), 0.02)
        self.assertEqual(add_leftover_panels(mask, whole), whole, "nothing left, nothing added")
        # A caption's worth of ink outside the panels is not a panel.
        image, draw = _page()
        _frame(draw, (30, 30, 570, 420))
        _frame(draw, (30, 450, 570, 840))
        draw.rectangle((200, 860, 400, 880), fill="black")
        both = [{"x": 0.05, "y": 30 / 900, "w": 0.9, "h": 390 / 900}, {"x": 0.05, "y": 0.5, "w": 0.9, "h": 390 / 900}]
        self.assertEqual(len(add_leftover_panels(page_mask(image), both)), 2)

    def test_a_blank_page_has_nothing(self):
        image, _ = _page()
        self.assertEqual(detect_panels(image), {"segmented": False, "source": "auto", "panels": []})

    def test_ink_is_what_differs_from_the_background(self):
        image, draw = _page(background="black")
        draw.rectangle((100, 100, 300, 300), fill="white")
        mask = ink_mask(image)
        self.assertEqual(mask.getpixel((200, 200)), 255)
        self.assertEqual(mask.getpixel((20, 20)), 0)


class OrderTests(unittest.TestCase):
    def test_a_grid_reads_rows_top_to_bottom_and_across(self):
        image, draw = _page()
        _grid(draw, 2, 3)
        panels = detect_panels(image)["panels"]
        ltr = _centres(order_panels(panels, READING_LTR))
        self.assertEqual([c[0] < 0.5 for c in ltr], [True, False] * 3, "left then right, three rows")
        self.assertEqual(sorted(c[1] for c in ltr), [c[1] for c in ltr], "never back up a row")
        rtl = _centres(order_panels(panels, READING_RTL))
        self.assertEqual([c[0] < 0.5 for c in rtl], [False, True] * 3, "manga reads right then left")

    def test_a_tall_panel_comes_before_the_pair_beside_it(self):
        panels = [
            {"x": 0.52, "y": 0.5, "w": 0.45, "h": 0.45},   # bottom right
            {"x": 0.52, "y": 0.03, "w": 0.45, "h": 0.45},  # top right
            {"x": 0.03, "y": 0.03, "w": 0.45, "h": 0.94},  # tall left
        ]
        ordered = order_panels(panels, READING_LTR)
        self.assertEqual([p["x"] for p in ordered], [0.03, 0.52, 0.52])
        self.assertEqual([p["y"] for p in ordered], [0.03, 0.03, 0.5])

    def test_quadrants_read_in_order(self):
        self.assertEqual(_centres(quadrant_panels(READING_LTR)), [(0.25, 0.25), (0.75, 0.25), (0.25, 0.75), (0.75, 0.75)])
        self.assertEqual(_centres(quadrant_panels(READING_RTL)), [(0.75, 0.25), (0.25, 0.25), (0.75, 0.75), (0.25, 0.75)])


class ModelTierTests(unittest.TestCase):
    """The detector refines the cut; it never replaces it."""

    def test_no_model_is_the_ordinary_case(self):
        self.assertIsNone(load_model_session(None))
        self.assertIsNone(load_model_session("/nowhere/panels.onnx"))

    def test_the_runtime_loads_only_with_a_model_and_never_with_telemetry(self):
        """ONNX Runtime's official builds send usage telemetry to Microsoft
        unless told not to before they start; production was doing so until
        2026-10-05. It is switched off first, and without a model file the
        runtime is not imported at all."""
        import os, sys, tempfile, types
        from unittest.mock import patch
        seen = {}
        fake = types.ModuleType("onnxruntime")
        fake.disable_telemetry_events = lambda: seen.setdefault("switched_off", True)

        def session(path, providers):
            seen["telemetry_env_at_load"] = os.environ.get("ORT_DISABLE_TELEMETRY")
            return "session"

        fake.InferenceSession = session
        with tempfile.NamedTemporaryFile(suffix=".onnx") as model, patch.dict(sys.modules, {"onnxruntime": fake}), \
                patch.dict(os.environ, {"ORT_DISABLE_TELEMETRY": "0"}):
            self.assertIsNone(load_model_session(model.name + ".missing"))
            self.assertEqual(seen, {}, "no model file: the runtime is never touched")
            self.assertEqual(load_model_session(model.name), "session")
        self.assertEqual(seen, {"switched_off": True, "telemetry_env_at_load": "1"})

    def test_two_boxes_that_mostly_overlap_are_one_panel_seen_twice(self):
        kept = suppress([(0.9, (0, 0, 100, 100)), (0.7, (5, 5, 100, 100)), (0.8, (200, 0, 300, 100))])
        self.assertEqual(kept, [(0, 0, 100, 100), (200, 0, 300, 100)], "the surer of the pair, and the other panel")

    def test_a_coarse_region_is_split_by_the_boxes_inside_it_and_keeps_what_they_miss(self):
        # One region 300 wide by 300 tall with ink everywhere; the model saw
        # two panels in its lower two thirds and nothing in the top third.
        mask = Image.new("L", (300, 300), 255)
        region = (0, 0, 300, 300)
        boxes = [(0, 100, 300, 200), (0, 200, 300, 300)]
        result = refine(mask, [region], boxes, min_area=300 * 300 * 0.04)
        self.assertEqual(result[:2], [(0, 100, 300, 200), (0, 200, 300, 300)])
        self.assertEqual(len(result), 3, "the two boxes, then the top third the model did not see")
        top = result[2]
        # The boxes are painted out with a margin, so the band above them
        # ends a little short of the first box's edge.
        self.assertEqual((top[0], top[1], top[2]), (0, 0, 300))
        self.assertTrue(80 <= top[3] <= 100, str(top))

    def test_a_region_with_one_box_or_none_is_left_as_the_cut_found_it(self):
        mask = Image.new("L", (300, 300), 255)
        self.assertEqual(refine(mask, [(0, 0, 300, 300)], [(0, 0, 300, 150)], 100), [(0, 0, 300, 300)])
        self.assertEqual(refine(mask, [(0, 0, 300, 300)], [], 100), [(0, 0, 300, 300)])

    def test_a_box_mostly_outside_a_region_is_not_one_of_its_panels(self):
        mask = Image.new("L", (300, 300), 255)
        regions = [(0, 0, 300, 150), (0, 150, 300, 300)]
        # Two boxes in the lower region, one straddling the gutter: the upper
        # region keeps its shape, the lower splits.
        boxes = [(0, 160, 150, 300), (150, 160, 300, 300), (0, 100, 300, 200)]
        result = refine(mask, regions, boxes, 100)
        self.assertEqual(result[0], (0, 0, 300, 150))
        self.assertIn((0, 160, 150, 300), result)
        self.assertIn((150, 160, 300, 300), result)

    @unittest.skipUnless(HAS_NUMPY, "the detector's runtime is an optional install")
    def test_a_stubbed_detector_splits_what_the_gutters_merged(self):
        import numpy

        # A page whose two panels abut with no gutter: the cut sees one
        # region. A detector that reports both, letterbox coordinates and all,
        # turns it into two.
        image, draw = _page(width=600, height=600)
        draw.rectangle((30, 30, 570, 300), fill="#444444")
        draw.rectangle((30, 300, 570, 570), fill="#666666")
        self.assertFalse(detect_panels(image)["segmented"], "one block of ink is not a layout")
        scale = 1024 / 600

        class Session:
            def get_inputs(self):
                return [type("Input", (), {"name": "images"})()]

            def run(self, _outputs, feeds):
                self.shape = feeds["images"].shape
                rows = numpy.zeros((1, 300, 6), dtype=numpy.float32)
                rows[0, 0] = [30 * scale, 30 * scale, 570 * scale, 300 * scale, 0.9, 0]
                rows[0, 1] = [30 * scale, 300 * scale, 570 * scale, 570 * scale, 0.8, 0]
                rows[0, 2] = [100 * scale, 100 * scale, 200 * scale, 150 * scale, 0.95, 1]  # text, ignored
                rows[0, 3] = [30 * scale, 30 * scale, 570 * scale, 300 * scale, 0.3, 0]      # too unsure
                return [rows]

        session = Session()
        result = detect_panels(image, session)
        self.assertEqual(session.shape, (1, 3, 1024, 1024))
        self.assertEqual((result["segmented"], result["source"], len(result["panels"])), (True, "model", 2))
        tops = sorted(round(panel["y"], 2) for panel in result["panels"])
        self.assertEqual(tops, [0.05, 0.5])


class VisionTierTests(unittest.TestCase):
    """What is asked of a vision model, and what of its answer is believed."""

    def test_the_page_is_described_in_its_own_pixels_and_direction(self):
        prompt = vision_boxes_prompt(780, 1200, READING_RTL)
        self.assertIn("780 pixels wide and 1200 pixels tall", prompt)
        self.assertIn("right to left", prompt)
        self.assertIn("x1", prompt, "absolute pixel corners, which is what the model localises best in")

    def test_boxes_come_back_normalised_and_clipped_whatever_form_they_took(self):
        text = 'Here you go: [{"x1": 10, "y1": 20, "x2": 400, "y2": 620}, {"x": 400, "y": 620, "w": 380, "h": 900}, {"x1": "no"}, 7]'
        boxes = parse_vision_boxes(text, 780, 1200)
        self.assertEqual(len(boxes), 2)
        self.assertEqual({round(v, 3) for v in (boxes[0]["x"], boxes[0]["y"])}, {round(10 / 780, 3), round(20 / 1200, 3)})
        self.assertLessEqual(boxes[1]["y"] + boxes[1]["h"], 1.0, "a box past the page's edge is clipped to it")
        self.assertIsNone(parse_vision_boxes("I cannot see any panels.", 780, 1200), "no list is no answer")
        self.assertEqual(parse_vision_boxes("[]", 780, 1200), [], "an empty list is the answer the prompt asks for")

    def test_an_answer_is_believed_only_if_it_reads_as_a_layout(self):
        two = [{"x": 0, "y": 0, "w": 1, "h": 0.5}, {"x": 0, "y": 0.5, "w": 1, "h": 0.5}]
        self.assertTrue(accept_vision_boxes(two))
        self.assertFalse(accept_vision_boxes(two[:1]), "one box is a page, not a layout")
        self.assertFalse(accept_vision_boxes([{"x": 0, "y": 0, "w": 1, "h": 0.6}, {"x": 0, "y": 0.3, "w": 1, "h": 0.7}]),
                         "boxes on top of each other are one panel seen twice, and one is not a layout")

    def test_boxes_that_lie_over_each_other_become_the_field_around_them(self):
        # A diagonal pair: two panels split by a slanted gutter, whose
        # rectangles overlap in the middle of the page. Read as one field.
        diagonal = [{"x": 0.05, "y": 0.05, "w": 0.55, "h": 0.45}, {"x": 0.4, "y": 0.05, "w": 0.55, "h": 0.45},
                    {"x": 0.05, "y": 0.55, "w": 0.9, "h": 0.4}]
        merged = merge_overlapping(diagonal)
        self.assertEqual(len(merged), 2)
        field = min(merged, key=lambda box: box["y"])
        self.assertEqual([round(field[key], 2) for key in ("x", "y", "w", "h")], [0.05, 0.05, 0.9, 0.45])
        self.assertTrue(accept_vision_boxes(diagonal))
        # Panels that merely touch, or overlap by a hair, stay themselves.
        grid = [{"x": 0.05, "y": 0.05, "w": 0.4, "h": 0.4}, {"x": 0.45, "y": 0.05, "w": 0.45, "h": 0.4}]
        self.assertEqual(merge_overlapping(grid), grid)
        # Merging carries on: a chain of three overlapping boxes is one field.
        chain = [{"x": 0.0, "y": 0.0, "w": 0.4, "h": 1.0}, {"x": 0.3, "y": 0.0, "w": 0.4, "h": 1.0}, {"x": 0.6, "y": 0.0, "w": 0.4, "h": 1.0}]
        self.assertEqual(len(merge_overlapping(chain)), 1)

    def test_a_sliver_of_leftover_ink_grows_the_panel_beside_it(self):
        # A panel whose right side is a slant: the rectangle read for it stops
        # where the slant starts, and the triangle beyond is left out. It is
        # no panel of its own (a triangle is half its rectangle); the panel
        # grows to hold it. A dense island beside it is a panel of its own.
        image, draw = _page()
        draw.rectangle((30, 30, 400, 420), fill="#444444")
        draw.polygon([(400, 30), (560, 30), (400, 420)], fill="#444444")
        _frame(draw, (30, 450, 570, 870))
        mask = page_mask(image)
        read = [{"x": 30 / 600, "y": 30 / 900, "w": 370 / 600, "h": 390 / 900}]
        whole = add_leftover_panels(mask, read)
        self.assertEqual(len(whole), 2, "the triangle grew the panel; the framed panel below is its own")
        top = min(whole, key=lambda panel: panel["y"])
        self.assertGreaterEqual(round((top["x"] + top["w"]) * 600), 555, "the panel now holds the slant")

    def _nudged(self, box, dx, dy, width=600, height=900):
        x0, y0, x1, y1 = box
        return {"x": (x0 + dx) / width, "y": (y0 + dy) / height, "w": (x1 - x0) / width, "h": (y1 - y0) / height}

    def _pixels(self, panel, width=600, height=900):
        return (round(panel["x"] * width), round(panel["y"] * height),
                round((panel["x"] + panel["w"]) * width), round((panel["y"] + panel["h"]) * height))

    def test_a_models_rough_boxes_are_moved_onto_the_gutters(self):
        image, draw = _page()
        cells = _grid(draw, 2, 3)
        mask = page_mask(image)
        # Every edge a few percent off, the way a model draws them.
        rough = [self._nudged(box, dx, dy) for box, (dx, dy) in zip(cells, [(-14, 9), (11, -12), (8, 8), (-9, -15), (13, 6), (-12, 12)])]
        refined = refine_vision_boxes(mask, rough)
        self.assertEqual(len(refined), 6)
        for panel, cell in zip(sorted(refined, key=lambda p: (p["y"], p["x"])), cells):
            for got, want in zip(self._pixels(panel), cell):
                self.assertLessEqual(abs(got - want), 2, f"{self._pixels(panel)} should sit on {cell}")

    def test_what_a_model_drew_as_one_panel_stays_one(self):
        # The model's structure is the prior. Cutting inside its box let a
        # band of cloud on a painted page pass for a gutter and carved a
        # patch of sky out as a panel -- the local finder's own mistake,
        # repeated on the answer that was meant to correct it.
        image, draw = _page()
        cells = _grid(draw, 2, 3)
        mask = page_mask(image)
        top_pair = (cells[0][0], cells[0][1], cells[1][2], cells[1][3])
        rough = [self._nudged(top_pair, 5, -6)] + [self._nudged(box, 0, 0) for box in cells[2:]]
        refined = refine_vision_boxes(mask, rough)
        self.assertEqual(len(refined), 5)
        pair = max(refined, key=lambda panel: panel["w"])
        for got, want in zip(self._pixels(pair), top_pair):
            self.assertLessEqual(abs(got - want), 2, "one panel, on the ink of both frames")

    def test_a_gutter_too_thin_for_the_cut_is_found_along_the_models_edge(self):
        # Absolute Batman #2, page 3: the top pair's gutter is two pixels at
        # 600px and the cut reads the row as one panel; Sonnet drew the pair
        # as two, its split nine percent off. The page finds the thin gutter
        # near the model's edge and parts them there.
        image, draw = _page()
        left, right, below = (30, 30, 288, 296), (290, 30, 570, 296), (30, 316, 570, 870)
        for box in (left, right, below):
            # Art to the border, as on a printed page: a page with white
            # inside its frames has bare lines that are not gutters.
            draw.rectangle(box, fill="#555555", outline="black", width=3)
        mask = page_mask(image)
        rough = [self._nudged(left, 0, 0), self._nudged(right, 0, 0), self._nudged(below, 4, -6)]
        rough[0]["w"] = (288 - 30 + 40) / 600  # the model put the split 40px too far right
        rough[1]["x"] = (291 + 40) / 600
        rough[1]["w"] = (570 - 291 - 40) / 600
        refined = refine_vision_boxes(mask, rough)
        self.assertEqual(len(refined), 3)
        got = sorted(self._pixels(panel) for panel in refined)
        for panel, want in zip(got, sorted([left, right, below])):
            for a, b in zip(panel, want):
                self.assertLessEqual(abs(a - b), 3, f"{got} should sit on the frames")

    def test_the_panels_an_answer_left_out_are_read_from_the_ink_it_left(self):
        # A spread, 2026-09-22: six panels exact to their frames and three
        # missed, the central figure among them. Refusing the answer whole
        # threw the six away; the page gives the missing row back instead,
        # and only what is still uncovered after that counts against it.
        image, draw = _page()
        cells = _grid(draw, 2, 3)
        mask = page_mask(image)
        four = refine_vision_boxes(mask, [self._nudged(box, 0, 0) for box in cells[:4]])
        self.assertEqual(len(four), 4)
        self.assertGreater(ink_outside(mask, four), INK_UNCOVERED_MAX)
        whole = add_leftover_panels(mask, four)
        self.assertEqual(len(whole), 6)
        self.assertLess(ink_outside(mask, whole), INK_UNCOVERED_MAX)
        for panel, cell in zip(sorted(whole[4:], key=lambda p: p["x"]), cells[4:]):
            for got, want in zip(self._pixels(panel), cell):
                self.assertLessEqual(abs(got - want), 3, f"{self._pixels(panel)} should sit on {cell}")

    def test_a_grid_guessed_through_the_art_is_refused(self):
        # gpt-4o-mini on the eight-panel page: a uniform 2x3 grid whose lines
        # run through panels. Coverage and overlap passed it; the page does not.
        image, draw = _page()
        _grid(draw, 2, 3)
        mask = page_mask(image)
        # A 3x2 guess over a 2x3 page: the column line at a third of the width
        # crosses every panel in the left column.
        guess = [{"x": c / 3, "y": r / 2, "w": 1 / 3, "h": 1 / 2} for r in range(2) for c in range(3)]
        self.assertIsNone(refine_vision_boxes(mask, guess))

    def test_a_layout_is_ambiguous_when_panels_half_share_a_row(self):
        grid = [{"x": 0, "y": 0, "w": 0.5, "h": 0.5}, {"x": 0.5, "y": 0, "w": 0.5, "h": 0.5}, {"x": 0, "y": 0.5, "w": 1, "h": 0.5}]
        self.assertFalse(ambiguous_layout(grid), "clean rows are not a question")
        stagger = [{"x": 0, "y": 0, "w": 0.5, "h": 0.5}, {"x": 0.5, "y": 0.25, "w": 0.5, "h": 0.5}]
        self.assertTrue(ambiguous_layout(stagger), "half a row's overlap is what row-major gets wrong")

    def test_an_order_is_believed_only_as_a_permutation(self):
        self.assertEqual(parse_vision_order("Reading order: [3, 1, 2]", 3), [3, 1, 2])
        self.assertIsNone(parse_vision_order("[1, 1, 2]", 3), "a panel read twice")
        self.assertIsNone(parse_vision_order("[1, 2]", 3), "a panel left out")
        self.assertIsNone(parse_vision_order("no idea", 3))
        self.assertIn("panel 2:", vision_order_prompt([{"x": 0, "y": 0, "w": 1, "h": 0.5}, {"x": 0, "y": 0.5, "w": 1, "h": 0.5}]))

    def test_an_order_that_was_given_stands_over_the_row_rule(self):
        panels = [
            {"x": 0, "y": 0, "w": 0.5, "h": 0.5, "order": 1},
            {"x": 0.5, "y": 0, "w": 0.5, "h": 0.5, "order": 0},
        ]
        self.assertEqual([p["x"] for p in order_panels(panels, READING_LTR)], [0.5, 0], "as ordered, not left to right")


if __name__ == "__main__":
    unittest.main()
