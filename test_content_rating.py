"""The rating scale and its wording: pure, no app, no I/O."""

import unittest

import content_rating as ratings


class RatingWordingTests(unittest.TestCase):
    def test_every_source_lands_on_the_one_scale(self):
        cases = {
            # Metron's names.
            "Everyone": "everyone", "Teen": "teen", "Teen Plus": "teen_plus", "Mature": "mature",
            "Explicit": "mature", "CCA": "everyone", "Unknown": None,
            # What covers print (checked on real covers, 2026-09-27).
            "13+ TEEN": "teen", "RATED T+": "teen_plus", "T+": "teen_plus", "T": "teen",
            "suggested for mature readers": "mature", "17+ MATURE": "mature", "ALL AGES": "everyone",
            "E": "everyone", "PARENTAL ADVISORY": "mature", "EXPLICIT CONTENT": "mature",
            # Nothing said.
            "NONE": None, "": None, None: None, "Issue 16": None,
        }
        for text, expected in cases.items():
            self.assertEqual(ratings.normalize_rating(text), expected, text)

    def test_the_stricter_of_two_readings_wins(self):
        self.assertEqual(ratings.strictest("teen", None, "mature"), "mature")
        self.assertIsNone(ratings.strictest(None, "bogus"))

    def test_a_limit_hides_what_is_above_it_and_unrated_as_asked(self):
        self.assertTrue(ratings.allows("mature", None, False), "no limit reads everything")
        self.assertTrue(ratings.allows(None, None, False))
        self.assertTrue(ratings.allows("everyone", "everyone", False))
        self.assertFalse(ratings.allows("teen", "everyone", False))
        self.assertTrue(ratings.allows("teen", "teen_plus", False))
        self.assertFalse(ratings.allows(None, "teen", False), "unrated is hidden unless allowed")
        self.assertTrue(ratings.allows(None, "teen", True))

    def test_a_cover_answer_keeps_what_was_printed(self):
        self.assertEqual(ratings.rating_from_cover_answer(" '13+ TEEN' "), ("teen", "13+ TEEN"))
        self.assertEqual(ratings.rating_from_cover_answer("NONE"), (None, None))
        self.assertEqual(ratings.rating_from_cover_answer("I cannot see a rating on this cover."), (None, None))


if __name__ == "__main__":
    unittest.main()
