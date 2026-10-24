# Buitre

[![CI](https://github.com/Gurpr33t04/buitre/actions/workflows/ci.yml/badge.svg)](https://github.com/Gurpr33t04/buitre/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Status: beta](https://img.shields.io/badge/status-beta-orange)

An adaptive web crawler and information extractor for **old and modern
pages**: table layouts, ASP.NET, WordPress, JavaScript apps and PDFs, on the
surface web and over Tor. It keeps the useful content, contacts and metadata
and leaves the navigation, banners, comments and template text behind.

## Contents

[Install](#install) · [Quick start](#quick-start) · [Command line](#command-line) ·
[Python API](#python-api) · [How it works](#how-it-works) ·
[Measuring quality](#measuring-quality) · [Tor](#tor-and-onion-services) ·
[Limits](#limits) · [Responsible use](#responsible-use) ·
[Contributing](#contributing) · [License](#license)

## Install

Requires Python 3.10 or newer.

```bash
git clone https://github.com/Gurpr33t04/buitre.git
cd buitre
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements.txt      # everything: core, optional features, pytest
pip install -e .                     # also installs the `buitre` command
playwright install chromium          # only needed for JavaScript-heavy pages
```

`requirements.txt` is the single dependency file. A minimal install without the
optional extras (`trafilatura`, `pypdf`, `pytest`) is `pip install .`. Without
installing the package, run the tool as `python run.py <url>`.

## Quick start

Works offline, using a bundled sample page:

```bash
python examples/quickstart.py
```

Against a live site:

```bash
buitre https://example.com
```

## Command line

```bash
buitre https://example.com                          # one page
buitre https://example.com --json --text            # JSON, including full text
buitre https://example.com --crawl --depth 2 --max-pages 50 \
       --workers 4 --delay 1 --jsonl out.jsonl --store crawl.db
buitre https://example.com --crawl --store crawl.db --resume    # continue a crawl
buitre https://uni.example.edu --crawl --documents --focus admission --focus circular
buitre https://example.com --crawl --deep           # also enumerate safe GET forms
```

CLI crawls are polite by default: 1 second between requests to a host, 2
retries, robots.txt obeyed, sitemap used. Run `buitre --help` for every option.

## Python API

```python
from buitre.pipeline import run_pipeline, process_html
from buitre.crawler.crawler import crawl
from buitre.context import PipelineContext

result = run_pipeline("https://example.com")                    # fetch + extract
result = process_html(open("page.html").read(),
                      "https://example.com/page")               # offline, saved HTML

print(result.page.title, result.statistics.extraction_score)
print(result.extracted.important_content)
print(result.extracted.contacts)       # [{"type", "value", "source"}, ...]

with PipelineContext(documents=True) as ctx:    # shares client, browser, site memory
    crawled = crawl("https://example.com", max_depth=2, max_pages=100,
                    workers=4, delay=1.0, max_retries=2, context=ctx,
                    jsonl_path="out.jsonl", store_path="crawl.db")
```

Library defaults are backwards compatible (no delay, no retries).
`respect_robots=True` is on by default; set `delay` and `max_retries` yourself.

## How it works

**Fetch.** One shared HTTP client (keep-alive, cookies). Bodies are streamed
with a size cap and binaries are never downloaded. Encoding is detected from the
BOM, headers, `<meta>` and the bytes, so legacy windows-1252, Shift-JIS and GBK
pages decode correctly. Responses are classified: HTTP errors, 429 (honouring
`Retry-After`), 5xx, bot-challenge pages, soft 404s and non-text payloads are
failures, not content.

**Render only when it helps.** A short static page is complete. The browser runs
only when a page says it needs JavaScript or is an empty application shell, and
embedded state (`__NEXT_DATA__`, `window.__INITIAL_STATE__`, JSON scripts) is
read first, often making the browser unnecessary. When it runs, one persistent
Chromium blocks images, fonts and trackers, waits for the DOM to settle,
scrolls for lazy content and captures the page's JSON API calls. A failed or
emptier render never replaces a good HTTP result, and URL shapes that always
need a browser are remembered per host.

**Extract.** A block-aware DOM walk handles `<td>`/`<font>`/`<center>`/`<br>`
layouts and ASP.NET form wrappers. Noise regions (comments, cookie banners, ads,
related boxes, sidebars) are removed by tag, ARIA role and class token, and
multilingual boilerplate phrases are filtered. Per-host **template learning**
drops blocks repeated across a site's pages in any language. Blocks are ranked
by structure and text statistics (no topic keywords) and returned in reading
order. Metadata includes OpenGraph, canonical, JSON-LD, dates, authors, hreflang
and feeds. Contacts record their source (`mailto`, `tel`, `cfemail`, `content`,
`page`, `obfuscated`), and Cloudflare and `[at]`/`[dot]` obfuscation is decoded.
Each page gets a type (article, listing, contact, login, error, document, ...)
and a 0-1 quality score with reasons.

**Crawl.** Best-first priority queue (link position, anchor text, URL, parent
quality, novelty); URL-shape caps and trap detection for calendars, facet
filters, session IDs and directory cycles; robots.txt (longest-match, wildcards,
Crawl-delay); sitemap seeding; per-host throttling; retries with backoff;
near-duplicate detection (SimHash); skipping of login and soft-404 pages; worker
threads; SQLite persistence with resume; JSONL streaming.

## Measuring quality

```bash
python -m buitre.eval                                   # built-in corpus
python -m buitre.eval ./my_pages --min-f1 0.9 --max-junk 0.05
```

Each `NAME.html` needs a `NAME.json` gold file next to it (see
`buitre/eval/corpus`). **The built-in corpus is synthetic**: it guards against
regressions in the failure classes found so far and does not prove real-world
accuracy. For meaningful numbers, save 30-50 real pages from the sites you care
about and evaluate on those. If `trafilatura` is installed, the report also
shows it as a baseline.

## Tor and onion services

Buitre sends `.onion` targets through Tor's SOCKS proxy (`127.0.0.1:9050`; set
`TOR_PROXY` in `buitre/config.py` for Tor Browser's `9150`). Clearnet URLs are
fetched directly.

```bash
curl --socks5-hostname 127.0.0.1:9050 -s https://check.torproject.org/api/ip   # {"IsTor":true,...}
buitre "http://<address>.onion/" --json
```

Onion crawls use longer timeouts and retries. With a Tor ControlPort and
password configured (`--tor-control-password`, or `BUITRE_TOR_PASSWORD`),
circuits are rotated after repeated failures. The Tor browser blocks WebRTC
leaks and downloads. Tor-related values found on pages are recorded in
`page.metadata` (`onion_location`, `onion_links`).

## Limits

- Not implemented: OCR for scanned PDFs, `__doPostBack` pagination (counted in
  `page.metadata["postback_links"]`), CAPTCHA solving, Shadow DOM piercing.
- `--deep` only follows GET forms that contain a `<select>`. It never submits
  POST forms, touches password or file forms, or bypasses authentication.
- Documents nested deeper than 600 levels are rejected (`MAX_NESTING`).
- Template learning needs about four pages per host before it filters. `crawl()`
  re-selects content at the end (`refine=True`), but JSONL written during the
  crawl reflects the live state.
- Extraction is heuristic. Expect some misses on unusual layouts, and use the
  evaluation harness to measure your own sites.

## Responsible use

Buitre can crawl the surface web and Tor onion services. Use it only on content
you are allowed to access and collect.

- It identifies itself (`Buitre/x.y`), obeys robots.txt and rate-limits by
  default. Do not disable these for sites you do not own or have permission to
  crawl.
- Respect each site's terms and applicable law, including data-protection rules
  for personal data (emails, phone numbers, names) you extract.
- Crawl output is scraped third-party data. Keep it out of version control (the
  provided `.gitignore` does this) and store it securely.
- Do not use it to access private, authenticated or illegal content. For unknown
  onion services, run the browser in a container and do not store media.

The authors accept no liability for how the software is used.

## Contributing

```bash
git clone https://github.com/Gurpr33t04/buitre.git && cd buitre
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
pytest -q && python -m buitre.eval --min-f1 0.95 --max-junk 0.02
```

Bug reports are most useful with the URL (or a saved page) and the output of
`buitre <url> --json --text`. Pull requests that change extraction should add a
page and gold file to `buitre/eval/corpus`, so the improvement stays measured.

See [CHANGELOG.md](CHANGELOG.md) for release history.

## License

[MIT](LICENSE)
