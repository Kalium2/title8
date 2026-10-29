#!/usr/bin/env python3
"""Scrape California Code of Regulations, Title 8, from the DIR website.

Title 8 is published section by section at https://www.dir.ca.gov/title8/,
one HTML page per section (for example 3203.html), with table-of-contents
pages linking chapters, subchapters, groups and articles down to the
sections. This crawler walks those pages and writes one record per section.

Standard library only, so it runs anywhere Python 3.9+ is installed.

Usage:
    python title8_scraper.py                    # crawl all of Title 8
    python title8_scraper.py --section 3203 5144   # just these sections
    python title8_scraper.py --max-pages 50        # a short trial run

Output (in --out, default ./data):
    title8_sections.jsonl   one JSON record per line, in section order
    title8_sections.json    the same records keyed by section number
    crawl_report.json       pages fetched, failures, timings
"""

import argparse
import collections
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

BASE = "https://www.dir.ca.gov/title8/"
DEFAULT_SEEDS = ["https://www.dir.ca.gov/samples/search/query.htm"]
USER_AGENT = (
    "Title8Scraper/1.0 (+https://github.com/kalium2/title8; "
    "regulatory reference lookup; polite crawl)"
)

# A section page is named after its section number: 3203.html, 1532.1.html,
# 5155.html, 342.html, 3314a.html. Index pages have letters first (sb7g2.html).
SECTION_FILE_RE = re.compile(r"^(\d+(?:\.\d+)*[a-z]?)\.html?$", re.I)
HIERARCHY_RE = re.compile(
    r"^(Division|Chapter|Subchapter|Group|Article|Appendix)\s+([\w.\-]+?)\.?\s+(\S.*)$",
    re.I,
)
# "§3203. Injury and Illness Prevention Program." or "Section 3203. ..."
HEADING_RE = re.compile(
    r"^(?:§+|Section)\s*(\d+(?:\.\d+)*[a-z]?)\.?\s*(.*)$", re.I
)

BLOCK_TAGS = {
    "p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3",
    "h4", "h5", "h6", "pre", "blockquote", "dd", "dt", "hr", "section",
}
SKIP_TAGS = {"script", "style", "noscript"}


class PageParser(HTMLParser):
    """Collects visible text (with line breaks at block elements), links, and title."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lines = [""]
        self.links = []
        self.title = ""
        self._in_title = False
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self._skip += 1
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in BLOCK_TAGS:
            self.lines.append("")

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False
        if tag in BLOCK_TAGS:
            self.lines.append("")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip:
            return
        self.lines[-1] += data

    def text_lines(self):
        out = []
        for line in self.lines:
            line = re.sub(r"[\s\xa0]+", " ", line).strip()
            if line:
                out.append(line)
        return out


def parse_page(raw_html):
    parser = PageParser()
    parser.feed(raw_html)
    parser.close()
    return parser


def section_from_url(url):
    name = urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
    m = SECTION_FILE_RE.match(name)
    return m.group(1) if m else None


def extract_section(url, raw_html):
    """Turn one section page into a record. Returns None if it is not a section."""
    page = parse_page(raw_html)
    lines = page.text_lines()
    section = section_from_url(url)
    if not section:
        return None

    # Find the heading line for this section; everything after it is the body.
    heading_idx, heading = None, ""
    for i, line in enumerate(lines):
        m = HEADING_RE.match(line)
        if m and m.group(1).lower() == section.lower():
            heading_idx, heading = i, m.group(2).strip()
            break
    if heading_idx is None:
        # Fall back to the <title>, which DIR fills as
        # "California Code of Regulations, Title 8, Section 3203. <heading>".
        t = re.sub(r"\s+", " ", html.unescape(page.title)).strip()
        m = re.search(r"Section\s*" + re.escape(section) + r"\.?\s*(.*)$", t, re.I)
        heading = m.group(1).strip() if m else ""
        # No heading in the page body: the text starts after the last hierarchy
        # line, which also leaves the site banner and disclaimer behind.
        heading_idx = max((i for i, line in enumerate(lines) if HIERARCHY_RE.match(line)),
                          default=-1)

    # Hierarchy lines (Chapter 4. ..., Subchapter 7. ..., Article 1. ...) sit
    # above the heading.
    hierarchy = []
    for line in lines[: heading_idx + 1]:
        m = HIERARCHY_RE.match(line)
        if m:
            hierarchy.append({
                "level": m.group(1).title(),
                "number": m.group(2).rstrip("."),
                "name": m.group(3).strip().rstrip("."),
            })

    body = lines[heading_idx + 1:]
    # Trim site navigation that trails the regulation text.
    for i, line in enumerate(body):
        if re.match(r"^(Go Back to|Back to top|Return to)\b", line, re.I):
            body = body[:i]
            break

    # Split off the authority note and history, which DIR prints after the text.
    # Only "Note: Authority/Reference cited" is the authority note; other notes
    # ("Note: The construction of ...") are part of the regulation text.
    note, history = "", []
    text_lines = body
    for i, line in enumerate(body):
        m = re.match(r"^NOTE:\s*((?:Authority|Reference)\b.*)$", line, re.I)
        if m:
            note = m.group(1).strip()
            text_lines = body[:i]
            rest = body[i + 1:]
            if rest and re.match(r"^HISTORY\b", rest[0], re.I):
                rest = rest[1:]
            history = rest
            break
        if re.match(r"^HISTORY\b", line, re.I):
            text_lines = body[:i]
            history = body[i + 1:]
            break

    text = "\n".join(text_lines).strip()
    if not text and not heading:
        return None
    return {
        "section": section,
        "heading": heading.rstrip("."),
        "citation": f"8 CCR § {section}",
        "hierarchy": hierarchy,
        "text": text,
        "authority_note": note,
        "history": history,
        "url": url,
    }


def section_sort_key(section):
    parts = re.findall(r"\d+|[a-z]+", section.lower())
    return [(0, int(p)) if p.isdigit() else (1, p) for p in parts]


def decode(body, charset=None):
    """DIR serves UTF-8 without always saying so; older pages are Windows-1252."""
    for enc in ("utf-8", charset):
        if enc:
            try:
                return body.decode(enc)
            except (UnicodeDecodeError, LookupError):
                pass
    return body.decode("windows-1252", errors="replace")


class Fetcher:
    """Polite HTTP: robots.txt, a delay between requests, retries, a disk cache."""

    def __init__(self, cache_dir, delay, retries, timeout, refresh):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.retries = retries
        self.timeout = timeout
        self.refresh = refresh
        self._last = 0.0
        self.network_calls = 0
        self.robots = urllib.robotparser.RobotFileParser()
        try:
            self.robots.set_url("https://www.dir.ca.gov/robots.txt")
            self.robots.read()
        except Exception:
            self.robots = None  # unreadable robots.txt: proceed, slowly

    def _cache_path(self, url):
        p = urllib.parse.urlparse(url)
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", (p.path + "_" + p.query).strip("_"))
        return self.cache_dir / (safe or "index")

    def get(self, url):
        cached = self._cache_path(url)
        if cached.exists() and not self.refresh:
            return decode(cached.read_bytes())
        if self.robots and not self.robots.can_fetch(USER_AGENT, url):
            raise PermissionError(f"robots.txt disallows {url}")

        wait = max(self.delay, 1.0)
        for attempt in range(1, self.retries + 1):
            pause = self.delay - (time.monotonic() - self._last)
            if pause > 0:
                time.sleep(pause)
            self._last = time.monotonic()
            self.network_calls += 1
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    charset = resp.headers.get_content_charset()
                cached.write_bytes(body)
                return decode(body, charset)
            except urllib.error.HTTPError as e:
                if e.code == 404 or attempt == self.retries:
                    raise
                if e.code in (429, 503):
                    retry_after = e.headers.get("Retry-After", "")
                    if retry_after.isdigit():
                        wait = max(wait, int(retry_after))
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise
            time.sleep(wait)
            wait *= 2
        raise RuntimeError("unreachable")


def in_scope(url):
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or p.netloc.lower() not in (
        "www.dir.ca.gov", "dir.ca.gov"
    ):
        return False
    path = p.path.lower()
    return path.startswith("/title8/") and path.endswith((".html", ".htm"))


def normalise(url, base):
    """Absolute URL without fragment, DIR host and /title8/ folded to one form
    (the site links both /Title8/ and /title8/). Offsite URLs kept as-is."""
    url = urllib.parse.urljoin(base, url.strip())
    url, _ = urllib.parse.urldefrag(url)
    p = urllib.parse.urlparse(url)
    if p.netloc.lower() not in ("www.dir.ca.gov", "dir.ca.gov"):
        return url
    path = re.sub(r"^/title8/", "/title8/", p.path, flags=re.I)
    return urllib.parse.urlunparse(("https", "www.dir.ca.gov", path, "", p.query, ""))


def crawl(args):
    fetcher = Fetcher(args.cache, args.delay, args.retries, args.timeout, args.refresh)
    records, failures = {}, []
    started = time.monotonic()

    # Section pages are fetched before further index pages, so a short
    # --max-pages trial still returns sections rather than only tables of contents.
    sections_q, index_q = collections.deque(), collections.deque()

    def enqueue(url):
        (sections_q if section_from_url(url) else index_q).append(url)

    if args.section:
        start = [BASE + f"{s}.html" for s in args.section]
        follow = False
    else:
        start = list(args.seed or DEFAULT_SEEDS)
        follow = True
    for url in start:
        enqueue(url)
    seen = set(start)
    fetched = 0

    while sections_q or index_q:
        if args.max_pages and fetched >= args.max_pages:
            print(f"Stopped at --max-pages {args.max_pages}", file=sys.stderr)
            break
        url = (sections_q or index_q).popleft()
        try:
            raw = fetcher.get(url)
        except Exception as e:
            failures.append({"url": url, "error": f"{type(e).__name__}: {e}"})
            print(f"  FAILED {url}: {e}", file=sys.stderr)
            continue
        fetched += 1

        rec = extract_section(url, raw) if section_from_url(url) else None
        if rec:
            records[rec["section"]] = rec
        if fetched % 25 == 0 or args.verbose:
            queued = len(sections_q) + len(index_q)
            print(f"[{fetched} pages, {len(records)} sections, {queued} queued] {url}",
                  file=sys.stderr)

        if follow:
            for href in parse_page(raw).links:
                if href.lower().startswith(("mailto:", "javascript:")):
                    continue
                nxt = normalise(href, url)
                if nxt not in seen and in_scope(nxt):
                    seen.add(nxt)
                    enqueue(nxt)

    return records, failures, {
        "pages_fetched": fetched,
        "network_calls": fetcher.network_calls,
        "seconds": round(time.monotonic() - started, 1),
    }


def write_output(out_dir, records, failures, stats, merge=False):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    existing = out / "title8_sections.json"
    if merge and existing.exists():
        # A --section run updates those sections and keeps the rest.
        records = {**json.loads(existing.read_text(encoding="utf-8")), **records}
    ordered = [records[k] for k in sorted(records, key=section_sort_key)]
    with open(out / "title8_sections.jsonl", "w", encoding="utf-8") as f:
        for rec in ordered:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    with open(out / "title8_sections.json", "w", encoding="utf-8") as f:
        json.dump({r["section"]: r for r in ordered}, f, ensure_ascii=False, indent=1)
    report = {
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": BASE,
        "sections": len(ordered),
        "sections_without_text": [r["section"] for r in ordered if not r.get("text")],
        **stats,
        "failures": failures,
    }
    with open(out / "crawl_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--section", nargs="+", help="fetch only these section numbers")
    ap.add_argument("--seed", nargs="+", help="start crawling from these URLs")
    ap.add_argument("--out", default="data", help="output folder (default: data)")
    ap.add_argument("--cache", default=".cache/title8", help="raw HTML cache folder")
    ap.add_argument("--refresh", action="store_true", help="ignore the cache and refetch")
    ap.add_argument("--delay", type=float, default=1.0, help="seconds between requests")
    ap.add_argument("--retries", type=int, default=4)
    ap.add_argument("--timeout", type=float, default=30)
    ap.add_argument("--max-pages", type=int, default=0, help="stop after N pages (0 = no limit)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    records, failures, stats = crawl(args)
    if not records:
        # Never overwrite good data with an empty run (site down, layout changed).
        print(f"No sections found; {len(failures)} failures. Existing output left as it was.",
              file=sys.stderr)
        for f in failures[:10]:
            print(f"  {f['url']}: {f['error']}", file=sys.stderr)
        return 1
    report = write_output(args.out, records, failures, stats, merge=bool(args.section))
    print(f"Done: {report['sections']} sections from {report['pages_fetched']} pages, "
          f"{len(failures)} failures, {report['seconds']}s. Output in {args.out}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
