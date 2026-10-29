"""Offline tests. The HTML below is hand-written in the shape of DIR's pages;
it exercises the parser and crawler, it is not real regulation text."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import title8_scraper as t8  # noqa: E402

SECTION_PAGE = """<html><head><title>California Code of Regulations, Title 8, Section 3203.
Injury and Illness Prevention Program.</title><script>var x = "Section 9999.";</script></head>
<body><div id="nav"><a href="/title8/sb7g1a1.html">Article 1</a></div>
<p><b>Subchapter 7. General Industry Safety Orders</b></p>
<p>Group 1. General Physical Conditions and Structures</p>
<p>Article 1. Definitions</p>
<h3>&sect;3203. Injury and Illness Prevention Program.</h3>
<p>(a) Effective July 1, 1991, every employer shall establish a written program.</p>
<p>(1) Identify the person&nbsp;or persons with authority.</p>
<p>Note: Authority cited: Section 142.3, Labor Code. Reference: Section 6401.7, Labor Code.</p>
<p>HISTORY</p>
<p>1. New section filed 1-2-91.</p>
<p>2. Amendment filed 3-4-05.</p>
<p><a href="/title8/sb7g1a1.html">Go Back to Article 1 Table of Contents</a></p>
</body></html>"""

INDEX_PAGE = """<html><body>
<a href="/title8/sb7g1a1.html">Article 1</a>
<a href="3203.html">&sect;3203</a>
<a href="https://www.dir.ca.gov/title8/1532.1.html#top">&sect;1532.1</a>
<a href="https://example.com/title8/1.html">offsite</a>
<a href="/dosh/index.html">DOSH</a>
<a href="mailto:x@example.com">mail</a>
</body></html>"""

SUB_SECTION_PAGE = """<html><body><p>Article 4. Lead</p>
<p>Section 1532.1. Lead.</p><p>(a) Scope.</p></body></html>"""


class ExtractTests(unittest.TestCase):
    def test_section_page(self):
        rec = t8.extract_section("https://www.dir.ca.gov/title8/3203.html", SECTION_PAGE)
        self.assertEqual(rec["section"], "3203")
        self.assertEqual(rec["heading"], "Injury and Illness Prevention Program")
        self.assertEqual(rec["citation"], "8 CCR § 3203")
        self.assertEqual([h["level"] for h in rec["hierarchy"]],
                         ["Subchapter", "Group", "Article"])
        self.assertEqual(rec["hierarchy"][0]["name"], "General Industry Safety Orders")
        self.assertTrue(rec["text"].startswith("(a) Effective July 1, 1991"))
        self.assertIn("person or persons", rec["text"])  # &nbsp; normalised
        self.assertNotIn("Note:", rec["text"])
        self.assertNotIn("9999", rec["text"])  # script content dropped
        self.assertTrue(rec["authority_note"].startswith("Authority cited"))
        self.assertEqual(rec["history"], ["1. New section filed 1-2-91.",
                                          "2. Amendment filed 3-4-05."])

    def test_dotted_section_number(self):
        rec = t8.extract_section("https://www.dir.ca.gov/title8/1532.1.html", SUB_SECTION_PAGE)
        self.assertEqual(rec["section"], "1532.1")
        self.assertEqual(rec["heading"], "Lead")
        self.assertEqual(rec["text"], "(a) Scope.")

    def test_index_page_is_not_a_section(self):
        self.assertIsNone(t8.section_from_url("https://www.dir.ca.gov/title8/sb7g2.html"))
        self.assertEqual(t8.section_from_url("https://www.dir.ca.gov/title8/3314a.html"), "3314a")

    def test_sort_order(self):
        got = sorted(["3203", "342", "1532.1", "1532", "3314a", "3314"], key=t8.section_sort_key)
        self.assertEqual(got, ["342", "1532", "1532.1", "3203", "3314", "3314a"])


class CrawlTests(unittest.TestCase):
    PAGES = {
        "https://www.dir.ca.gov/samples/search/query.htm": INDEX_PAGE,
        "https://www.dir.ca.gov/title8/sb7g1a1.html": INDEX_PAGE,
        "https://www.dir.ca.gov/title8/3203.html": SECTION_PAGE,
        "https://www.dir.ca.gov/title8/1532.1.html": SUB_SECTION_PAGE,
    }

    def run_crawl(self, argv):
        def fake_get(_self, url):
            if url not in self.PAGES:
                raise FileNotFoundError(url)
            return self.PAGES[url]

        with mock.patch.object(t8.Fetcher, "__init__", lambda s, *a: setattr(s, "network_calls", 0)), \
                mock.patch.object(t8.Fetcher, "get", fake_get):
            return t8.main(argv)

    def test_full_crawl_follows_only_title8_links(self):
        with tempfile.TemporaryDirectory() as d:
            code = self.run_crawl(["--out", d, "--seed",
                                   "https://www.dir.ca.gov/samples/search/query.htm"])
            self.assertEqual(code, 0)
            data = json.loads(Path(d, "title8_sections.json").read_text())
            self.assertEqual(list(data), ["1532.1", "3203"])
            report = json.loads(Path(d, "crawl_report.json").read_text())
            self.assertEqual(report["pages_fetched"], 4)
            self.assertEqual(report["failures"], [])

    def test_section_mode_merges_and_empty_run_keeps_data(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "title8_sections.json").write_text(json.dumps({"342": {"section": "342"}}))
            self.assertEqual(self.run_crawl(["--out", d, "--section", "3203"]), 0)
            data = json.loads(Path(d, "title8_sections.json").read_text())
            self.assertEqual(list(data), ["342", "3203"])

            before = Path(d, "title8_sections.json").read_text()
            self.assertEqual(self.run_crawl(["--out", d, "--section", "0000"]), 1)
            self.assertEqual(Path(d, "title8_sections.json").read_text(), before)


if __name__ == "__main__":
    unittest.main()
