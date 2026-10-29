# Title 8 scraper

Downloads California Code of Regulations, Title 8 (the Cal/OSHA safety
orders, plus the other Title 8 chapters such as the Division of Workers'
Compensation regulations), from the official DIR site at
`https://www.dir.ca.gov/title8/`, and saves it as data files: one record per
section.

The Violations Desk Worker currently fetches Title 8 text live as its third
fallback for cited standards. A local copy of Title 8 lets it look the text
up without depending on the DIR site being reachable.

## What you get

Everything lands in the `data/` folder:

| File | What it is |
|---|---|
| `title8_sections.json` | Every section, keyed by section number (`"3203"`, `"1532.1"`), for lookups |
| `title8_sections.jsonl` | The same records, one per line, in section order, for spreadsheets and bulk loading |
| `crawl_report.json` | When it ran, how many pages and sections, and any page that failed |

Each record looks like this:

```json
{
  "section": "3203",
  "heading": "Injury and Illness Prevention Program",
  "citation": "8 CCR § 3203",
  "hierarchy": [
    {"level": "Subchapter", "number": "7", "name": "General Industry Safety Orders"},
    {"level": "Group", "number": "1", "name": "..."},
    {"level": "Article", "number": "1", "name": "..."}
  ],
  "text": "(a) ...",
  "authority_note": "Authority cited: ...",
  "history": ["1. New section filed ..."],
  "url": "https://www.dir.ca.gov/title8/3203.html"
}
```

## Running it on GitHub (no setup needed)

1. Open the repository on GitHub and click the **Actions** tab.
2. Click **Scrape Title 8** in the list on the left.
3. Click **Run workflow**. The options are all optional:
   - *Only these section numbers*: e.g. `3203 5144 1509`. Leave it blank for all of Title 8.
   - *Stop after this many pages*: put `50` for a quick trial run first.
   - *Re-read the pages saved by the last run*: tick this to rebuild the
     data from the last run's downloaded pages in a minute or two, without
     contacting the DIR site. Useful after a fix to the scraper. Leave it
     unticked to fetch the current regulations.
4. Click the green **Run workflow** button.

A full run fetches several thousand pages at one per second, so expect it
to take an hour or two. When it finishes, the data is committed into the
`data/` folder, and you can also download it as a zip from the run's
summary page under **Artifacts**.

## Running it on your own computer

You need Python 3.9 or newer. Nothing else to install.

```
python title8_scraper.py --max-pages 50      # trial run
python title8_scraper.py                     # all of Title 8
python title8_scraper.py --section 3203 5144 # just these sections, merged into existing data
```

Pages are saved in `.cache/title8/` as they arrive, so if a run is stopped
partway, running it again picks up from the stored pages instead of
fetching them again. Add `--refresh` to ignore the stored pages and fetch
everything fresh.

## How it behaves toward the DIR site

- One request per second (`--delay` to change), identified by its own
  user agent, and it respects the site's `robots.txt`.
- On a refusal or timeout it waits and retries, doubling the wait each
  time, and honours a `Retry-After` the site sends.
- It only follows links inside `/title8/` on `www.dir.ca.gov`.
- If a run finds no sections at all (site down, page layout changed), it
  stops with an error and leaves the existing data untouched.

## Known limits

- The parser reads the page text, not a fixed page layout, so it should
  survive small changes to DIR's markup. A large redesign would need the
  patterns at the top of `title8_scraper.py` updating. The tests in
  `tests/` show what it expects.
- Tables and diagrams inside a section come through as plain lines of text.
- The DIR site is the unofficial online version; the official text is the
  one published by the Office of Administrative Law.

## Tests

```
python -m unittest discover -s tests -v
```
