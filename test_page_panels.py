"""Where the panels are on a page: drawn fixtures, not shipped ones."""

import unittest

from PIL import Image, ImageDraw

from page_panels import (
    READING_LTR, READING_RTL, accept_vision_boxes, ambiguous_layout, detect_panels, ink_mask,
    load_model_session, order_panels, parse_vision_boxes, parse_vision_order, quadrant_panels,
    refine, suppress, vision_boxes_prompt, vision_order_prompt,
)

try:
    import numpy  # noqa: F401
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _page(width=600, height=900, background="white"):
    image = Image.new("RGB", (width, height), background)
    return image, ImageDraw.Draw(image)


def _frame(draw, box, fill="white", outline="black", width=3):
    draw.rectangle(box, fill=fill, outline=outline, width=width)
    # Some art inside, so a panel is not just its border.
    x0, y0, x1, y1 = box
    draw.ellipse((x0 + 20, y0 + 20, x1 - 20, y1 - 20), fill="#555555")


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

    def test_a_spread_is_not_cut(self):
        image, draw = _page(width=1200, height=900)
        _grid(draw, 4, 3, width=1200)
        self.assertEqual(detect_panels(image), {"segmented": False, "source": "auto", "panels": []})

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
        self.assertFalse(accept_vision_boxes([{"x": 0, "y": 0, "w": 0.3, "h": 0.3}, {"x": 0.5, "y": 0.5, "w": 0.3, "h": 0.3}]),
                         "two small boxes leave most of the page unread")
        self.assertFalse(accept_vision_boxes([{"x": 0, "y": 0, "w": 1, "h": 0.6}, {"x": 0, "y": 0.3, "w": 1, "h": 0.7}]),
                         "boxes on top of each other are one panel seen twice")

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
