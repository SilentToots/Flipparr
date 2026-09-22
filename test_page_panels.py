"""Where the panels are on a page: drawn fixtures, not shipped ones."""

import unittest

from PIL import Image, ImageDraw

from page_panels import (
    READING_LTR, READING_RTL, detect_panels, ink_mask, order_panels, quadrant_panels,
)


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
        self.assertEqual(detect_panels(image), {"segmented": False, "panels": []})

    def test_a_spread_is_not_cut(self):
        image, draw = _page(width=1200, height=900)
        _grid(draw, 4, 3, width=1200)
        self.assertEqual(detect_panels(image), {"segmented": False, "panels": []})

    def test_a_blank_page_has_nothing(self):
        image, _ = _page()
        self.assertEqual(detect_panels(image), {"segmented": False, "panels": []})

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


if __name__ == "__main__":
    unittest.main()
