import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from buitre.cleaner.text import extract_readable_text
from buitre.detector.language import detect_language
from buitre.errors import FetchStatusError
from buitre.models import FetchResult, LinkInfo, PageData
from buitre.parser.guard import MAX_NESTING, max_nesting
from buitre.parser.forms import extract_forms
from buitre.parser.metadata import extract_metadata
from buitre.utils.soup import make_soup


_ONCLICK_URL_RE = re.compile(
    r"""(?:window\.open|location(?:\.href)?\s*=|document\.location\s*=)"""
    r"""\s*\(?\s*['"]([^'"]+)['"]""",
    re.IGNORECASE,
)

_REGION_BY_TAG = {
    "nav": "nav",
    "header": "header",
    "footer": "footer",
    "aside": "aside",
    "main": "main",
    "article": "main",
}

_REGION_BY_ROLE = {
    "navigation": "nav",
    "banner": "header",
    "contentinfo": "footer",
    "complementary": "aside",
    "main": "main",
}


def _region(tag: Tag) -> str:
    """Nearest structural ancestor that tells us where a link lives."""

    for parent in tag.parents:
        name = getattr(parent, "name", None)

        if not name:
            continue

        role = str((parent.attrs or {}).get("role", "")).lower()

        if role in _REGION_BY_ROLE:
            return _REGION_BY_ROLE[role]

        if name in _REGION_BY_TAG:
            return _REGION_BY_TAG[name]

    return "body"


def _anchor_text(tag: Tag) -> str:
    text = " ".join(tag.get_text(" ", strip=True).split())

    if not text:
        image = tag.find("img")

        if image is not None:
            text = (image.get("alt") or "").strip()

    if not text:
        text = (tag.get("title") or tag.get("aria-label") or "").strip()

    return text[:200]


def _extract_links(
    soup: BeautifulSoup,
    final_url: str,
) -> tuple[list[LinkInfo], int]:
    """
    Every followable reference on the page, with anchor text, rel and
    region: <a>, <area> (image maps), <frame>/<iframe> (framesets),
    onclick="window.open(...)", and <meta http-equiv=refresh>.

    Returns (links, count_of___doPostBack_links).
    """

    base_tag = soup.find("base", href=True)
    base_href = urljoin(final_url, base_tag["href"]) if base_tag else None

    def absolute(href: str) -> str:
        # With <base href> present, relative links must resolve against
        # it, not against the page URL.
        return urljoin(base_href, href) if base_href else href

    links: list[LinkInfo] = []
    postbacks = 0

    for tag in soup.find_all(["a", "area"]):
        href = tag.get("href")

        if href:
            href = href.strip()

            if "__doPostBack" in href:
                postbacks += 1
                continue

            if href and not href.lower().startswith("javascript:"):
                rel = [r.lower() for r in (tag.get("rel") or [])]

                links.append(
                    LinkInfo(
                        url=absolute(href),
                        text=_anchor_text(tag),
                        rel=rel,
                        region=_region(tag),
                    )
                )

                continue

        onclick = tag.get("onclick") or ""
        match = _ONCLICK_URL_RE.search(onclick)

        if match:
            links.append(
                LinkInfo(
                    url=absolute(match.group(1)),
                    text=_anchor_text(tag),
                    rel=["onclick"],
                    region=_region(tag),
                )
            )

        elif "__doPostBack" in onclick:
            postbacks += 1

    for tag in soup.find_all(["frame", "iframe"]):
        src = tag.get("src")

        if src and not src.lower().startswith(("javascript:", "about:")):
            links.append(
                LinkInfo(
                    url=absolute(src.strip()),
                    text=(tag.get("title") or tag.get("name") or "")[:200],
                    rel=["frame"],
                    region=_region(tag),
                )
            )

    return links, postbacks


def parse_html(fetch_result: FetchResult) -> PageData:
    """
    Parse an HTML FetchResult into Buitre's normalized PageData model.
    """

    body = fetch_result.body

    depth = max_nesting(body)

    if depth > MAX_NESTING:
        raise FetchStatusError(
            f"Document nesting is {depth} levels deep (limit "
            f"{MAX_NESTING}); refusing to parse a pathological page.",
            status_code=fetch_result.status_code,
            verdict="malformed",
        )

    soup = make_soup(body)

    extracted = extract_metadata(
        soup,
        fetch_result.final_url,
        html=body,
    )

    metadata = extracted["metadata"]
    meta_refresh = metadata.get("meta_refresh")

    # -------------------------
    # Title: <title> > og:title > first <h1>
    # -------------------------

    title = None

    if soup.title:
        title = soup.title.get_text(" ", strip=True) or None

    if not title:
        title = (metadata.get("opengraph") or {}).get("title")

    if not title:
        h1 = soup.find("h1")

        if h1 is not None:
            title = h1.get_text(" ", strip=True) or None

    # -------------------------
    # Description
    # -------------------------

    description = None

    description_tag = soup.find(
        "meta",
        attrs={"name": re.compile(r"^description$", re.IGNORECASE)},
    )

    if description_tag:
        description = description_tag.get("content")

    if not description:
        description = (metadata.get("opengraph") or {}).get("description")

    # -------------------------
    # Declared language
    # -------------------------

    declared_language = None

    if soup.html:
        declared_language = soup.html.get("lang") or soup.html.get(
            "xml:lang"
        )

    if not declared_language:
        tag = soup.find(
            "meta",
            attrs={"http-equiv": re.compile(r"content-language", re.I)},
        )

        if tag:
            declared_language = tag.get("content")

    # -------------------------
    # Headings
    # -------------------------

    headings = []

    for heading in soup.find_all(["h1", "h2", "h3"]):
        text = heading.get_text(" ", strip=True)

        if text:
            headings.append(text)

    # -------------------------
    # Forms, links (before noise is removed)
    # -------------------------

    forms = extract_forms(soup, fetch_result.final_url)

    link_details, postbacks = _extract_links(
        soup,
        fetch_result.final_url,
    )

    if meta_refresh:
        link_details.append(
            LinkInfo(url=meta_refresh, text="", rel=["refresh"], region="main")
        )

    if postbacks:
        metadata["postback_links"] = postbacks

    # -------------------------
    # Images (lazy-loaded ones keep the URL in data-src)
    # -------------------------

    images = []

    for image in soup.find_all("img"):
        src = (
            image.get("src")
            or image.get("data-src")
            or image.get("data-lazy-src")
        )

        if src and not src.startswith("data:"):
            images.append(src)

    # -------------------------
    # Remove obvious noise, then extract readable text
    # -------------------------

    for element in soup(["script", "style", "noscript"]):
        element.decompose()

    text = extract_readable_text(soup)

    language = detect_language(text, declared_language)

    return PageData(
        title=title,
        description=description,
        language=language,
        headings=headings,
        text=text,
        links=[link.url for link in link_details],
        images=images,
        metadata=metadata,
        structured_data=extracted["structured_data"],
        link_details=link_details,
        canonical_url=extracted["canonical_url"],
        published_at=extracted["published_at"],
        modified_at=extracted["modified_at"],
        author=extracted["author"],
        forms=forms,
    )
