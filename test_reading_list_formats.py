import unittest

from reading_list_formats import MAX_ITEMS, parse_reading_list

CBL = b"""<?xml version="1.0" encoding="utf-8"?>
<ReadingList xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <Name>[DC Comics] Absolute Power (WEB-CBRO)</Name>
  <NumIssues>3</NumIssues>
  <Books>
    <Book Series="Absolute Power: Ground Zero" Number="1" Volume="2024" Year="2024">
      <Database Name="cv" Series="158622" Issue="1061067" />
    </Book>
    <Book Series="Batman" Number="150" Volume="2016" Year="2024">
      <Database Name="cv" Series="91273" Issue="1061834" />
      <Database Name="metron" Series="4321" Issue="98765" />
    </Book>
    <Book Series="Green Lantern" Number="13" Volume="1" Year="2024" />
    <Book Series="" Number="1" />
  </Books>
  <Matchers />
</ReadingList>
"""

JSON = b"""{
  "fileDetails": {"version": 1.0, "UUID": "9b2f"},
  "listDetails": {"name": "Hush", "description": "A mystery villain.", "publisher": "DC", "type": "story",
                  "coverImageURLs": ["https://example.invalid/hush.jpg", "not a url"]},
  "issueList": [
    {"seriesName": "Batman", "seriesStartYear": 1940, "issueNumber": "608", "issueCoverDate": "2002-12-01", "issueType": "Event Core",
     "id": [{"name": "comicvine", "series": "796", "issue": "1"}, {"name": "grandComicsDatabase", "series": "77", "issue": "88"}]},
    {"seriesName": "Batman: Gotham Knights", "seriesStartYear": 2000, "issueNumber": "1", "issueCoverDate": "2003-06-01", "issueType": "Event Tie-In"},
    "not an entry"
  ]
}
"""


class ReadingListFormatTests(unittest.TestCase):
    def test_a_cbl_list_is_read_in_order_with_its_catalog_ids(self):
        parsed = parse_reading_list(CBL)
        self.assertEqual((parsed["format"], parsed["name"]), ("cbl", "[DC Comics] Absolute Power (WEB-CBRO)"))
        self.assertEqual([(i["seriesTitle"], i["number"], i["seriesYear"]) for i in parsed["items"]],
                         [("Absolute Power: Ground Zero", "1", 2024), ("Batman", "150", 2016), ("Green Lantern", "13", None)],
                         "Volume is the start year; a volume number is no year; a book with no series is dropped")
        self.assertEqual((parsed["items"][0]["provider"], parsed["items"][0]["providerSeriesId"], parsed["items"][0]["providerIssueId"]),
                         ("comic_vine", "158622", "1061067"))
        self.assertEqual((parsed["items"][1]["provider"], parsed["items"][1]["providerIssueId"]), ("metron", "98765"), "Metron first when both are named")
        self.assertEqual((parsed["items"][2]["provider"], parsed["items"][2]["providerIssueId"]), (None, None))

    def test_a_json_standard_list_is_read_with_its_details(self):
        parsed = parse_reading_list(JSON)
        self.assertEqual((parsed["format"], parsed["name"], parsed["description"], parsed["publisher"]),
                         ("json", "Hush", "A mystery villain.", "DC"))
        self.assertEqual(parsed["coverUrls"], ["https://example.invalid/hush.jpg"])
        self.assertEqual([(i["seriesTitle"], i["seriesYear"], i["number"], i["coverDate"], i["issueType"]) for i in parsed["items"]],
                         [("Batman", 1940, "608", "2002-12-01", "Event Core"), ("Batman: Gotham Knights", 2000, "1", "2003-06-01", "Event Tie-In")])
        self.assertEqual((parsed["items"][0]["provider"], parsed["items"][0]["providerSeriesId"]), ("comic_vine", "796"))
        self.assertEqual(parsed["items"][1]["provider"], None)

    def test_what_is_not_a_reading_list_is_refused_plainly(self):
        for bad in (b"", b"   ", b"hello", b"<html><body/></html>", b"<ReadingList><Books/></ReadingList>",
                    b'{"listDetails": {"name": "x"}}', b"{not json", b"<ReadingList><Books><Book Series=", 
                    b'<!DOCTYPE x [<!ENTITY a "b">]><ReadingList><Books><Book Series="&a;" Number="1"/></Books></ReadingList>'):
            with self.assertRaises(ValueError, msg=repr(bad)):
                parse_reading_list(bad)
        big = {"issueList": [{"seriesName": "X", "seriesStartYear": 2000, "issueNumber": str(n), "issueCoverDate": "2000-01-01"} for n in range(MAX_ITEMS + 1)]}
        import json
        with self.assertRaisesRegex(ValueError, "up to"):
            parse_reading_list(json.dumps(big).encode())
        with_bom = b"\xef\xbb\xbf" + CBL
        self.assertEqual(len(parse_reading_list(with_bom)["items"]), 3, "a BOM is not a problem")


if __name__ == "__main__":
    unittest.main()
