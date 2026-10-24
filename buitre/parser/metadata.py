import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from buitre.extractor.dates import to_iso_datetime


_META_REFRESH_RE = re.compile(r"url\s*=\s*['\"]?\s*([^'\";\s]+)", re.IGNORECASE)
_VUE_ATTR_RE = re.compile(r"\bdata-v-[0-9a-f]{6,}")


def _meta_map(soup: BeautifulSoup) -> dict[str, str]:
    """name / property / itemprop / http-equiv -> content (first wins)."""

    values: dict[str, str] = {}

    for meta in soup.find_all("meta"):
        content = meta.get("content")

        if content is None:
            continue

        for attribute in ("name", "property", "itemprop", "http-equiv"):
            key = meta.get(attribute)

            if key:
                values.setdefault(key.strip().lower(), content.strip())

    return values


def extract_json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """
    All JSON-LD objects on the page, with @graph containers flattened.
    Malformed blocks are skipped.
    """

    nodes: list[dict[str, Any]] = []

    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.get_text()

        if not raw or not raw.strip():
            continue

        try:
            data = json.loads(raw)

        except (json.JSONDecodeError, TypeError):
            continue

        stack = data if isinstance(data, list) else [data]

        for item in stack:
            if not isinstance(item, dict):
                continue

            graph = item.get("@graph")

            if isinstance(graph, list):
                nodes.extend(n for n in graph if isinstance(n, dict))

            nodes.append(item)

    return nodes[:100]


def _first(nodes: list[dict[str, Any]], key: str) -> str | None:
    for node in nodes:
        value = node.get(key)

        if isinstance(value, str) and value.strip():
            return value

    return None


def _names(value: Any) -> list[str]:
    result: list[str] = []

    for item in value if isinstance(value, list) else [value]:
        if isinstance(item, str) and item.strip():
            result.append(" ".join(item.split()))

        elif isinstance(item, dict) and isinstance(item.get("name"), str):
            result.append(" ".join(item["name"].split()))

    return result


def detect_frameworks(soup: BeautifulSoup, html: str) -> list[str]:
    """Technology fingerprints; useful for choosing HTTP vs browser."""

    found: list[str] = []

    def has_id(value: str) -> bool:
        return soup.find(id=value) is not None

    if "__NEXT_DATA__" in html or has_id("__next"):
        found.append("nextjs")

    if "__NUXT__" in html or has_id("__nuxt"):
        found.append("nuxt")

    if has_id("___gatsby"):
        found.append("gatsby")

    if soup.find(attrs={"ng-version": True}) or soup.find("app-root"):
        found.append("angular")

    if soup.find(attrs={"data-reactroot": True}):
        found.append("react")

    if _VUE_ATTR_RE.search(html):
        found.append("vue")

    generator = ""
    tag = soup.find("meta", attrs={"name": re.compile("^generator$", re.I)})

    if tag:
        generator = (tag.get("content") or "").lower()

    if "wordpress" in generator or "/wp-content/" in html:
        found.append("wordpress")

    if "drupal" in generator:
        found.append("drupal")

    if "joomla" in generator:
        found.append("joomla")

    if "__VIEWSTATE" in html:
        found.append("aspnet-webforms")

    return found


def extract_metadata(
    soup: BeautifulSoup,
    base_url: str,
    html: str = "",
) -> dict[str, Any]:
    """
    Collect page metadata and structured data.

    Returns a dict with keys: metadata, structured_data, published_at,
    modified_at, author, canonical_url.
    """

    meta = _meta_map(soup)
    nodes = extract_json_ld(soup)

    opengraph = {k[3:]: v for k, v in meta.items() if k.startswith("og:")}
    twitter = {k[8:]: v for k, v in meta.items() if k.startswith("twitter:")}

    # ---- canonical / alternates / feeds / pagination ----
    canonical = None
    feeds: list[str] = []
    hreflang: list[dict[str, str]] = []
    rel_links: dict[str, str] = {}

    for link in soup.find_all("link"):
        rel = [r.lower() for r in (link.get("rel") or [])]
        href = link.get("href")

        if not href:
            continue

        absolute = urljoin(base_url, href)

        if "canonical" in rel:
            canonical = absolute

        if "alternate" in rel and link.get("hreflang"):
            hreflang.append({"lang": link["hreflang"], "url": absolute})

        if "alternate" in rel and any(
            t in (link.get("type") or "").lower() for t in ("rss", "atom")
        ):
            feeds.append(absolute)

        for name in ("next", "prev"):
            if name in rel:
                rel_links[name] = absolute

    # ---- meta refresh ----
    meta_refresh = None
    refresh = meta.get("refresh")

    if refresh:
        match = _META_REFRESH_RE.search(refresh)

        if match:
            meta_refresh = urljoin(base_url, match.group(1))

    # ---- dates: JSON-LD > meta > <time> > microdata ----
    published = (
        to_iso_datetime(_first(nodes, "datePublished"))
        or to_iso_datetime(meta.get("article:published_time"))
        or to_iso_datetime(meta.get("og:published_time"))
        or to_iso_datetime(meta.get("datepublished"))
        or to_iso_datetime(meta.get("date"))
        or to_iso_datetime(meta.get("dc.date"))
        or to_iso_datetime(meta.get("pubdate"))
    )

    modified = (
        to_iso_datetime(_first(nodes, "dateModified"))
        or to_iso_datetime(meta.get("article:modified_time"))
        or to_iso_datetime(meta.get("og:updated_time"))
        or to_iso_datetime(meta.get("datemodified"))
        or to_iso_datetime(meta.get("last-modified"))
    )

    if not published:
        time_tag = soup.find(
            "time",
            attrs={"datetime": True, "itemprop": "datePublished"},
        ) or soup.find(
            "time",
            attrs={"datetime": True, "pubdate": True},
        )

        if time_tag is None:
            container = soup.find(["article", "main"])
            time_tag = (
                container.find("time", attrs={"datetime": True})
                if container
                else None
            )

        if time_tag is not None:
            published = to_iso_datetime(time_tag.get("datetime"))

    if not published:
        tag = soup.find(attrs={"itemprop": "datePublished"})

        if tag is not None:
            published = to_iso_datetime(
                tag.get("content") or tag.get("datetime")
            )

    # ---- author ----
    author = None

    for node in nodes:
        names = _names(node.get("author"))

        if names:
            author = ", ".join(names)
            break

    if not author:
        author = meta.get("author") or meta.get("article:author")

    if not author:
        tag = soup.find(attrs={"rel": "author"}) or soup.find(
            attrs={"itemprop": "author"}
        )

        if tag is not None:
            author = " ".join(tag.get_text(" ", strip=True).split()) or None

    metadata: dict[str, Any] = {
        "frameworks": detect_frameworks(soup, html or str(soup)),
    }

    for key, value in (
        ("opengraph", opengraph),
        ("twitter", twitter),
        ("hreflang", hreflang),
        ("feeds", feeds),
        ("rel_links", rel_links),
    ):
        if value:
            metadata[key] = value

    for key, value in (
        ("canonical", canonical),
        ("robots", meta.get("robots")),
        ("generator", meta.get("generator")),
        ("meta_refresh", meta_refresh),
        ("keywords", meta.get("keywords")),
        ("charset_declared", None),
    ):
        if value:
            metadata[key] = value

    return {
        "metadata": metadata,
        "structured_data": nodes,
        "published_at": published,
        "modified_at": modified,
        "author": author,
        "canonical_url": canonical,
    }
