import re
from urllib.parse import urlparse

from buitre.models import PageData


ARTICLE_TYPES = {
    "article", "newsarticle", "blogposting", "report", "scholarlyarticle",
    "techarticle", "opinionnewsarticle", "liveblogposting",
}

_ERROR_TITLE_RE = re.compile(
    r"^\s*(?:error\s*)?(?:400|401|403|404|410|500|502|503)\b"
    r"|\bpage not found\b|\bnot found\b|\bno longer available\b"
    r"|\bpage (?:cannot|can't|could not) be found\b"
    r"|\baccess denied\b",
    re.IGNORECASE,
)
_LOGIN_PATH_RE = re.compile(
    r"log-?in|logon|sign-?in|/sso(?:[/._?-]|$)",
    re.IGNORECASE,
)
_POLICY_RE = re.compile(
    r"privacy|terms|cookie|disclaimer|legal|imprint|impressum",
    re.IGNORECASE,
)


def _jsonld_types(page: PageData) -> set[str]:
    types: set[str] = set()

    for node in page.structured_data:
        value = node.get("@type")

        for item in value if isinstance(value, list) else [value]:
            if isinstance(item, str):
                types.add(item.rsplit("/", 1)[-1].lower())

    return types


def classify_page(
    url: str,
    page: PageData,
    content_type: str | None = None,
) -> str:
    """
    Rule-based page type: error, login, document, product, article,
    contact, policy, listing, home or generic.

    Used to skip pages that are not content (login walls, soft 404s) and
    to rank crawl targets.
    """

    path = urlparse(url).path or "/"
    text_length = len(page.text.strip())
    headline = " ".join(filter(None, [page.title, page.headings[0] if page.headings else ""]))

    if "pdf" in (content_type or "").lower() or path.lower().endswith(".pdf"):
        return "document"

    if _ERROR_TITLE_RE.search(headline or "") and text_length < 600:
        return "error"

    has_password = any(form.get("has_password") for form in page.forms)

    if (has_password and text_length < 1500) or (
        _LOGIN_PATH_RE.search(path) and text_length < 1500
    ):
        return "login"

    types = _jsonld_types(page)

    if "product" in types:
        return "product"

    og_type = str((page.metadata.get("opengraph") or {}).get("type", ""))

    if (
        types & ARTICLE_TYPES
        or og_type.lower() == "article"
        or (page.published_at and text_length >= 500)
    ):
        return "article"

    if re.search(r"contact", path + " " + (page.title or ""), re.IGNORECASE) \
            and text_length < 3000:
        return "contact"

    if _POLICY_RE.search(path) or _POLICY_RE.search(page.title or ""):
        return "policy"

    links = len(page.links)

    if links >= 30 and text_length / max(links, 1) < 120:
        return "listing"

    if path in ("", "/") or re.fullmatch(
        r"/(?:index|default|home)\.[a-z]+", path.lower()
    ):
        return "home"

    return "generic"
