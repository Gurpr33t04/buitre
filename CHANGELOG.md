# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-25

First public beta.

### Added
- **Extraction**
  - Block-aware DOM walk that handles legacy `<td>`/`<font>`/`<center>`/`<br>`
    layouts and ASP.NET form-wrapped pages.
  - Noise-region removal by tag, ARIA role and class token (comments, cookie
    banners, ads, related boxes, sidebars) and multilingual boilerplate phrases.
  - Per-host template learning: blocks repeated across a site are dropped in
    any language.
  - Structure-based block scoring (no topic keywords), returned in reading
    order; optional trafilatura agreement bonus.
  - Metadata: OpenGraph, canonical, JSON-LD (including `@graph` and list
    types), dates, authors, hreflang, feeds, framework detection.
  - Contacts with provenance (`mailto`, `tel`, `cfemail`, `content`, `page`,
    `obfuscated`); Cloudflare and `[at]`/`[dot]` de-obfuscation.
  - ISO-8601, region-aware dates; script-based language detection; page-type
    classification; real 0-1 quality score with reasons.
  - Content recovery from embedded application state (`__NEXT_DATA__`,
    `window.__INITIAL_STATE__`) and captured API JSON before using a browser.
  - Optional PDF text extraction (`--documents`, needs `pypdf`).
- **Fetching**
  - Shared HTTP client, streamed size cap, binaries never downloaded.
  - Encoding detection (BOM, header, meta, bytes).
  - Response classification: HTTP errors, 429 with `Retry-After`, bot-challenge
    pages, soft 404, non-text payloads.
- **Browser and Tor**
  - Persistent Chromium pool with resource blocking, DOM-stability wait,
    lazy-content scrolling and API capture.
  - Per-host memory of URL shapes that need a browser.
  - Tor ControlPort circuit rotation, WebRTC leak guard, no downloads.
- **Crawler**
  - Best-first frontier with link scoring; URL-shape caps and trap detection.
  - robots.txt (longest-match, wildcards, Crawl-delay) and sitemap seeding.
  - Per-host throttle, retries with backoff, SimHash near-duplicate detection,
    login and soft-404 skipping, worker threads.
  - SQLite persistence with resume, JSONL streaming, optional `--deep`
    enumeration of safe GET forms.
- **Quality tooling**
  - Evaluation harness (`python -m buitre.eval`) with a synthetic corpus and
    regression gate.
  - GitHub Actions CI (Python 3.10-3.14, both HTML parsers).

### Changed
- `important_content` is returned in page reading order instead of score order.
- `dates` are ISO-8601 strings. `domains` now means domains mentioned in the
  content; the new `outbound_domains` lists every external link.
- HTTP errors, rate limits, bot challenges, login pages and soft 404s are
  reported as failures or skipped instead of being extracted as content.
- robots.txt is obeyed by default.
- `page.text` is newline-separated (one line per paragraph or heading).
- Requirements are consolidated into a single `requirements.txt`.

## [0.1.0]

- Initial release.
