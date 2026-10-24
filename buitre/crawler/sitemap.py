import re
from typing import Callable
from urllib.parse import urlparse


_LOC_RE = re.compile(r"<loc>\s*(?:<!\[CDATA\[)?\s*(.*?)\s*(?:\]\]>)?\s*</loc>", re.S | re.I)

MAX_SITEMAP_FILES = 5
MAX_SITEMAP_URLS = 500


def parse_sitemap(xml: str) -> tuple[list[str], list[str]]:
    """
    Return (page_urls, child_sitemap_urls) from a sitemap or sitemap index.

    A regex over <loc> is used instead of an XML parser: it is immune to
    XML entity-expansion attacks and tolerant of the broken XML real
    sitemaps often contain.
    """

    locations = [m.strip() for m in _LOC_RE.findall(xml or "")]
    is_index = "<sitemapindex" in (xml or "").lower()

    if is_index:
        return [], locations

    return locations, []


def discover_sitemap_urls(
    start_url: str,
    declared: list[str],
    fetch_text: Callable[[str], tuple[int | None, str]],
    limit: int = MAX_SITEMAP_URLS,
) -> list[str]:
    """
    URLs listed in the site's sitemap(s): those declared in robots.txt,
    otherwise /sitemap.xml. Follows at most a few child sitemaps.
    """

    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"

    pending = list(declared) or [origin + "/sitemap.xml"]
    seen: set[str] = set()
    urls: list[str] = []
    fetched = 0

    while pending and fetched < MAX_SITEMAP_FILES and len(urls) < limit:
        sitemap_url = pending.pop(0)

        if sitemap_url in seen or sitemap_url.lower().endswith(".gz"):
            continue

        seen.add(sitemap_url)
        fetched += 1

        status, body = fetch_text(sitemap_url)

        if status != 200 or not body:
            continue

        pages, children = parse_sitemap(body)
        urls.extend(pages)
        pending.extend(children)

    return urls[:limit]
