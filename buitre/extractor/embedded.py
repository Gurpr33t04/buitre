import json
import re
from typing import Any

from buitre.cleaner.text import (
    _CJK_RE,
    _word_count,
    extract_blocks,
    normalize_text,
)
from buitre.utils.soup import make_soup


# Assignments such as  window.__NUXT__ = {...};  window.__INITIAL_STATE__ = ...
_STATE_ASSIGN_RE = re.compile(
    r"(?:window\.)?(__[A-Z0-9_]{3,}__)\s*=\s*",
)

# Keys whose string values are plumbing, never prose.
SKIP_KEYS = {
    "classname", "class", "style", "href", "src", "url", "slug", "id",
    "token", "hash", "buildid", "assetprefix", "runtimeconfig", "locale",
    "locales", "path", "aspath", "query", "image", "imageurl", "thumbnail",
    "icon", "color", "canonical", "csrf", "nonce", "sitekey", "apikey",
}

MAX_BLOCKS = 150
MIN_STRING = 60

_HTMLISH_RE = re.compile(r"<(?:p|div|br|li|ul|ol|h[1-6]|span|a|table)\b", re.I)


def _json_from(text: str, start: int) -> Any | None:
    """Decode one JSON value beginning at `start`, ignoring trailing code."""

    while start < len(text) and text[start] in " \t\r\n":
        start += 1

    if start >= len(text) or text[start] not in "{[":
        return None

    try:
        value, _ = json.JSONDecoder().raw_decode(text, start)
        return value

    except ValueError:
        return None


def extract_embedded_json(html: str) -> list[Any]:
    """
    Structured data a framework ships inside the HTML so the page can
    hydrate: Next.js __NEXT_DATA__, <script type="application/json">,
    and window.__NUXT__ / __INITIAL_STATE__ / __APOLLO_STATE__ style
    assignments. Often holds the full article text, so no browser is
    needed to read a "JavaScript-only" page.
    """

    if not html:
        return []

    soup = make_soup(html)
    found: list[Any] = []

    for script in soup.find_all("script"):
        kind = (script.get("type") or "").lower()
        raw = script.get_text()

        if not raw or not raw.strip():
            continue

        if kind == "application/json" or script.get("id") == "__NEXT_DATA__":
            try:
                found.append(json.loads(raw))
            except ValueError:
                pass

            continue

        if kind in ("", "text/javascript", "module") and "__" in raw:
            for match in _STATE_ASSIGN_RE.finditer(raw):
                value = _json_from(raw, match.end())

                if value is not None:
                    found.append(value)

    return found


def _prose(value: str) -> bool:
    value = value.strip()

    if len(value) < MIN_STRING and not (
        len(_CJK_RE.findall(value)) >= 20
    ):
        return False

    lowered = value[:12].lower()

    if lowered.startswith(("http://", "https://", "/", "data:", "{", "[")):
        return False

    if "function(" in value or "=>" in value:
        return False

    letters = sum(c.isalpha() for c in value)

    if letters / max(len(value), 1) < 0.55:
        return False

    return _word_count(value) >= 8


def text_from_json(data: Any) -> list[str]:
    """All prose-like strings in a JSON structure, in order, de-duplicated."""

    blocks: list[str] = []
    seen: set[str] = set()
    stack: list[tuple[Any, str]] = [(data, "")]

    while stack and len(blocks) < MAX_BLOCKS:
        node, key = stack.pop()

        if isinstance(node, dict):
            for k, v in reversed(list(node.items())):
                stack.append((v, str(k).lower()))

        elif isinstance(node, list):
            for item in reversed(node):
                stack.append((item, key))

        elif isinstance(node, str):
            if key in SKIP_KEYS or not _prose(node):
                continue

            if _HTMLISH_RE.search(node):
                texts = [b.text for b in extract_blocks(make_soup(node))]
            else:
                texts = [normalize_text(node)]

            for text in texts:
                if text and text not in seen:
                    seen.add(text)
                    blocks.append(text)

    return blocks


def embedded_text_blocks(
    html: str,
    api_responses: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Prose recovered from embedded app state and captured API JSON."""

    blocks: list[str] = []
    seen: set[str] = set()

    sources: list[Any] = list(extract_embedded_json(html))

    for response in api_responses or []:
        try:
            sources.append(json.loads(response.get("text", "")))
        except (ValueError, TypeError):
            continue

    for source in sources:
        for text in text_from_json(source):
            if text not in seen:
                seen.add(text)
                blocks.append(text)

        if len(blocks) >= MAX_BLOCKS:
            break

    return blocks
