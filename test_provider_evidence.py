import json
import unittest

from provider_evidence import (
    COMIC_VINE_ISSUE_FIELDS, COMIC_VINE_VOLUME_FIELDS,
    metron_reprint_evidence, native_issue_evidence,
)


class NativeProviderEvidenceTests(unittest.TestCase):
    def test_native_metron_shape_retains_every_id_and_era(self):
        rows = [{"id": 100 + n, "issue": f"Synthetic Run (2013) #{n}"} for n in range(1, 16)]
        evidence = metron_reprint_evidence(rows)
        self.assertEqual([r["targetProviderId"] for r in evidence], [str(100 + n) for n in range(1, 16)])
        self.assertEqual([r["issueNumber"] for r in evidence], [str(n) for n in range(1, 16)])
        self.assertTrue(all(r["seriesLabel"] == "Synthetic Run (2013)" for r in evidence))
        self.assertTrue(all(r["relationKind"] == "unknown" for r in evidence))
        self.assertEqual([r["raw"] for r in evidence], rows)

    def test_qualifiers_cannot_be_promoted_to_full_issues(self):
        rows = [
            {"id": 1, "issue": "Synthetic (2014) #1", "notes": "excerpt from a story"},
            {"id": 2, "issue": "Synthetic (2014) #2", "partial": True},
            {"id": 3, "issue": "Synthetic (2014) #3", "relation_type": "full_issue"},
            {"id": 4, "issue": "Synthetic (2014) #4", "notes": "material from original"},
        ]
        self.assertEqual([r["relationKind"] for r in metron_reprint_evidence(rows)],
                         ["excerpt", "partial_story", "unknown", "partial_story"])

    def test_nested_shape_and_duplicate_labels_preserve_distinct_ids(self):
        rows = [{"issue": {"id": 5, "series": {"name": "Synthetic"}, "number": "1"}},
                {"id": 6, "issue": "Synthetic #1"}]
        evidence = metron_reprint_evidence(rows)
        self.assertEqual([r["targetProviderId"] for r in evidence], ["5", "6"])
        self.assertEqual([r["issueNumber"] for r in evidence], ["1", "1"])

    def test_malformed_elements_survive_without_fabricated_targets(self):
        rows = [{"id": 5, "issue": "Unnumbered supplement"}, {"id": True}, None, 7]
        evidence = metron_reprint_evidence(rows)
        self.assertEqual([r["raw"] for r in evidence], rows)
        self.assertTrue(all(r["status"] == "unparsed" for r in evidence))
        self.assertIsNone(evidence[1]["targetProviderId"])
        with self.assertRaisesRegex(ValueError, "array"):
            metron_reprint_evidence({"unexpected": "shape"})

    def test_normalization_does_not_mutate_or_alias_input(self):
        row = {"id": 9, "series": {"id": 2, "series_type": {"name": "Trade Paperback"}},
               "reprints": [{"id": 1, "issue": "Synthetic #1"}]}
        before = json.dumps(row)
        evidence = native_issue_evidence("metron", row)
        evidence["publication"]["series_type"]["name"] = "changed"
        evidence["reprints"][0]["raw"]["id"] = 999
        self.assertEqual(json.dumps(row), before)

    def test_comic_vine_description_and_publication_are_lossless(self):
        description = '<p>Collects <a href="/synthetic/4050-7/">Synthetic</a> #1-5.</p><p>Except the backup stories.</p>'
        row = {"id": 101, "issue_number": "1", "volume": {"id": 20, "name": "Synthetic: Book Two"},
               "description": description}
        evidence = native_issue_evidence("comic_vine", row)
        self.assertEqual(evidence["description"], description)
        self.assertEqual(evidence["descriptionFormat"], "html")
        self.assertEqual(evidence["providerPublicationId"], "20")
        self.assertEqual(evidence["providerNumber"], "1")
        self.assertNotIn("volumeNumber", evidence)
        self.assertEqual(evidence["reprints"], [])
        self.assertIn("description", COMIC_VINE_ISSUE_FIELDS)
        self.assertIn("volume", COMIC_VINE_ISSUE_FIELDS)
        self.assertIn("description", COMIC_VINE_VOLUME_FIELDS)

    def test_absent_fields_are_distinct_from_known_empty(self):
        absent = native_issue_evidence("metron", {"id": 1})
        empty = native_issue_evidence("metron", {"id": 1, "desc": "", "reprints": []})
        self.assertFalse(absent["descriptionSupplied"])
        self.assertFalse(absent["reprintsSupplied"])
        self.assertTrue(empty["descriptionSupplied"])
        self.assertTrue(empty["reprintsSupplied"])
        self.assertEqual(empty["description"], "")
        with self.assertRaisesRegex(ValueError, "description"):
            native_issue_evidence("comic_vine", {"description": {"bad": "shape"}})


if __name__ == "__main__":
    unittest.main()
