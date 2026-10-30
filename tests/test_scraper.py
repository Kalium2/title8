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


class RealLayoutTests(unittest.TestCase):
    """Shapes seen in the first real crawl (sections 331, 338, 341)."""

    def test_uppercase_note_and_in_text_note(self):
        page = """<p>Article 2. Permits</p><p>&sect;341. Permit Requirements.</p>
        <p>(6) Excavation for swimming pools. Note: The construction of stages does not
        require a permit.</p>
        <p>NOTE: Authority cited: Section 6501, Labor Code.</p><p>HISTORY</p><p>1. Filed.</p>"""
        rec = t8.extract_section("https://www.dir.ca.gov/title8/341.html", page)
        self.assertIn("Note: The construction of stages does not require a permit.", rec["text"])
        self.assertEqual(rec["authority_note"], "Authority cited: Section 6501, Labor Code.")
        self.assertEqual(rec["history"], ["1. Filed."])

    def test_note_on_its_own_line(self):
        page = """<p>&sect;3469. Forklifts.</p><p>(a) Text.</p>
        <p>Note: References to federal OSHA regulations correspond as follows.</p>
        <p>NOTE</p><p>Authority cited: Section 142.3, Labor Code.</p>
        <p>HISTORY</p><p>1. Filed.</p>"""
        rec = t8.extract_section("https://www.dir.ca.gov/title8/3469.html", page)
        self.assertTrue(rec["text"].endswith("correspond as follows."))
        self.assertEqual(rec["authority_note"], "Authority cited: Section 142.3, Labor Code.")
        self.assertEqual(rec["history"], ["1. Filed."])

    def test_appendix_page_heading(self):
        page = """<p>Article 107. Dusts, Fumes, Mists, Vapors and Gases</p>
        <p>Appendix A to Section 5144: Fit Testing Procedures (Mandatory)</p>
        <p>Part I. OSHA-Accepted Fit Test Protocols</p>"""
        rec = t8.extract_section("https://www.dir.ca.gov/title8/5144a.html", page)
        self.assertEqual(rec["heading"],
                         "Appendix A to Section 5144: Fit Testing Procedures (Mandatory)")
        self.assertEqual([h["level"] for h in rec["hierarchy"]], ["Article"])
        self.assertEqual(rec["text"], "Part I. OSHA-Accepted Fit Test Protocols")

    def test_page_without_heading_or_text(self):
        page = """<html><head><title>California Code of Regulations, Title 8, Section 338.
        Special Procedures</title></head><body><a href="#c">Skip to Main Content</a>
        <p>This information is provided free of charge by the Department of Industrial
        Relations</p><p>Chapter 3.2. California Occupational Safety and Health Regulations</p>
        <p>Article 5. Hazardous Substances Information and Training</p></body></html>"""
        rec = t8.extract_section("https://www.dir.ca.gov/title8/338.html", page)
        self.assertEqual(rec["heading"], "Special Procedures")
        self.assertEqual(rec["text"], "")
        self.assertEqual([h["level"] for h in rec["hierarchy"]], ["Chapter", "Article"])

    def test_utf8_without_charset_header(self):
        body = "<p>(a) \u201cChief\u201d means</p>".encode("utf-8")
        self.assertIn("\u201cChief\u201d", t8.decode(body, None))
        self.assertIn("\u201cChief\u201d", t8.decode(body, "windows-1252"))
        self.assertEqual(t8.decode("caf\xe9".encode("windows-1252")), "caf\xe9")


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


class OrderTests(unittest.TestCase):
    def test_sections_before_more_index_pages_and_case_folded(self):
        index = """<a href="/Title8/sub7.html">a</a><a href="/title8/sub8.html">b</a>
        <a href="/title8/sub7.html">dup</a><a href="/Title8/3203.html">s</a>"""
        pages = {"https://www.dir.ca.gov/samples/search/query.htm": index,
                 "https://www.dir.ca.gov/title8/3203.html": SECTION_PAGE,
                 "https://www.dir.ca.gov/title8/sub7.html": "",
                 "https://www.dir.ca.gov/title8/sub8.html": ""}
        order = []

        def fake_get(_self, url):
            order.append(url)
            return pages[url]

        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(t8.Fetcher, "__init__", lambda s, *a: setattr(s, "network_calls", 0)), \
                mock.patch.object(t8.Fetcher, "get", fake_get):
            self.assertEqual(t8.main(["--out", d, "--max-pages", "2"]), 0)
        self.assertEqual(order, ["https://www.dir.ca.gov/samples/search/query.htm",
                                 "https://www.dir.ca.gov/title8/3203.html"])


if __name__ == "__main__":
    unittest.main()
