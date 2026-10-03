import unittest

import arc_catalog


class ArcCatalogTest(unittest.TestCase):
    def test_a_search_forgives_missing_small_words_order_and_a_year(self):
        names = [{"name": "War of the Realms"}, {"name": "Realms Fall"}, {"name": "Secret Wars"},
                 {"name": "War of the Realms: Omega"}]
        for query in ("War of Realms", "war of the realms", "realms war", "War of the Realms 2019",
                      "War of the Realms event", "war realm"):
            found = [entry["name"] for entry in arc_catalog.search(names, query)]
            self.assertEqual(found[0], "War of the Realms", query)
            self.assertNotIn("Secret Wars", found, query)
        self.assertEqual(arc_catalog.search(names, "of the"), [], "nothing but small words matches nothing")
        self.assertEqual([entry["name"] for entry in arc_catalog.search(names, "realms")][:2],
                         ["Realms Fall", "War of the Realms"], "the fewest extra words first")

    def test_community_files_read_as_names_with_their_publisher_and_guide(self):
        lists = arc_catalog.community_lists([
            "Marvel/Events/Official/[Marvel] (2019-06) War of the Realms (Official).cbl",
            "Marvel/Events/LoCG/[2019] War of the Realms (Marvel Comics)(LoCG).cbl",
            "Marvel/Events/Official/Checklists/War of the Realms - Official Checklist.webp",
            "README.md",
        ])
        self.assertEqual([(item["name"], item["publisher"], item["group"], item["tags"], item["year"]) for item in lists], [
            ("War of the Realms", "Marvel", "Events", ["Official"], "2019"),
            ("War of the Realms", "Marvel", "Events", ["Marvel Comics", "LoCG"], "2019"),
        ])
        self.assertEqual(lists[0]["url"], "https://github.com/DieselTech/CBL-ReadingLists/blob/main/"
                         "Marvel/Events/Official/[Marvel] (2019-06) War of the Realms (Official).cbl")


if __name__ == "__main__":
    unittest.main()
