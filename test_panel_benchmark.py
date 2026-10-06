"""The panel benchmark's comparison of a reader's boxes with the machine's."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))

from panel_benchmark import compare_page, summarise  # noqa: E402


def box(x: float, y: float, w: float, h: float, **extra: object) -> dict[str, object]:
    return {"x": x, "y": y, "w": w, "h": h, **extra}


TOP, BOTTOM = box(0, 0, 1, 0.5), box(0, 0.5, 1, 0.5)
LEFT, RIGHT = box(0, 0, 0.5, 1), box(0.5, 0, 0.5, 1)
QUARTERS = [box(0, 0, 0.5, 0.5), box(0.5, 0, 0.5, 0.5), box(0, 0.5, 0.5, 0.5), box(0.5, 0.5, 0.5, 0.5)]


class ComparePageTests(unittest.TestCase):
    def test_the_same_layout_is_exact_at_both_levels(self):
        result = compare_page([TOP, BOTTOM], [box(0, 0.01, 1, 0.49), box(0, 0.5, 1, 0.5)])
        self.assertTrue(result["exactStructure"])
        self.assertTrue(result["boxAgreement"] and result["groupAgreement"])
        self.assertEqual((result["merges"], result["splits"], result["orderInversions"]), (0, 0, 0))
        self.assertAlmostEqual(result["edgeDeviation"], 0.00125, places=4, msg="one side of one box moved by 0.01, over four sides and two boxes")

    def test_a_machine_box_holding_two_of_the_readers_is_a_merge(self):
        result = compare_page([TOP, BOTTOM], [box(0, 0, 1, 1)])
        self.assertEqual((result["merges"], result["splits"]), (1, 0))
        self.assertEqual(result["manualUnmatched"], 2, "neither of the reader's boxes has a match")
        self.assertFalse(result["boxAgreement"] or result["groupAgreement"])

    def test_a_readers_box_the_machine_cut_in_two_is_a_split_but_a_group_agreement(self):
        # The reader grouped what the machine split: a choice about reading,
        # reported as agreement at the group level and a split at the box level.
        result = compare_page([TOP, BOTTOM], [box(0, 0, 0.5, 0.5), box(0.5, 0, 0.5, 0.5), BOTTOM])
        self.assertEqual((result["merges"], result["splits"]), (0, 1))
        self.assertFalse(result["boxAgreement"])
        self.assertTrue(result["groupAgreement"])

    def test_an_extra_machine_box_nowhere_near_the_reader_breaks_group_agreement(self):
        result = compare_page([LEFT], [box(0, 0, 0.5, 1), box(0.6, 0.6, 0.3, 0.3)])
        self.assertFalse(result["groupAgreement"], "a phantom panel is not a grouping")
        self.assertEqual(result["autoUnmatched"], 1)

    def test_order_inversions_count_pairs_read_the_other_way_round(self):
        manual = [box(0, 0, 0.5, 1, order=0), box(0.5, 0, 0.5, 1, order=1)]
        auto = [box(0, 0, 0.5, 1, order=1), box(0.5, 0, 0.5, 1, order=0)]
        result = compare_page(manual, auto)
        self.assertEqual(result["orderInversions"], 1)
        self.assertTrue(result["boxAgreement"], "the boxes are right")
        self.assertFalse(result["exactStructure"], "but the order is not")

    def test_no_machine_reading_is_unsegmented(self):
        result = compare_page(QUARTERS, [])
        self.assertEqual((result["auto"], result["matched"], result["manualUnmatched"]), (0, 0, 4))
        self.assertIsNone(result["edgeDeviation"])

    def test_summary_counts_pages_by_what_went_wrong(self):
        pages = [
            {"local": compare_page([TOP, BOTTOM], [TOP, BOTTOM])},
            {"local": compare_page([TOP, BOTTOM], [box(0, 0, 1, 1)])},
            {"local": compare_page(QUARTERS, [])},
            {"local": compare_page([TOP, BOTTOM], [box(0, 0, 0.5, 0.5), box(0.5, 0, 0.5, 0.5), BOTTOM])},
            {"spread": True},
        ]
        summary = summarise(pages, "local")
        self.assertEqual(summary["pages"], 4)
        self.assertEqual((summary["exactStructure"], summary["boxAgreement"], summary["groupAgreement"]), (1, 1, 2))
        self.assertEqual((summary["unsegmented"], summary["autoFewer"], summary["autoMore"]), (1, 1, 1))
        self.assertEqual((summary["pagesWithMerges"], summary["pagesWithSplits"]), (1, 1))
        self.assertEqual(summarise([{"spread": True}], "local"), {"pages": 0})


if __name__ == "__main__":
    unittest.main()
