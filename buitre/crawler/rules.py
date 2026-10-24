import re
from urllib.parse import parse_qsl, urlencode, urldefrag, urljoin, urlparse, urlunparse


ALLOWED_SCHEMES = {"http", "https"}

IGNORED_EXTENSIONS = {
    # Images
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".bmp",
    ".tiff",

    # Audio / video
    ".mp3",
    ".wav",
    ".ogg",
    ".flac",
    ".mp4",
    ".avi",
    ".mov",
    ".mkv",
    ".webm",

    # Archives
    ".zip",
    ".rar",
    ".7z",
    ".tar",
    ".gz",
    ".bz2",
    ".xz",

    # Documents / binaries not currently parsed by Buitre
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",

    # Web assets
    ".css",
    ".js",
    ".map",

    # Fonts
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
}

TRACKING_PARAMETERS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
    "mc_cid",
    "mc_eid",
    "msclkid",
    "yclid",
    "igshid",
    "mkt_tok",
    "_ga",
    # Session identifiers: same page, endless distinct URLs.
    "phpsessid",
    "jsessionid",
    "sessionid",
    "sessid",
    "aspsessionid",
    "cfid",
    "cftoken",
}

_PATH_SESSION_RE = re.compile(r";jsessionid=[^/?#]*", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """
    Normalize a URL for crawl deduplication.

    Removes fragments and common tracking parameters.
    """

    url = url.strip()

    if not url:
        return ""

    url, _ = urldefrag(url)

    parsed = urlparse(url)

    if parsed.scheme.lower() not in ALLOWED_SCHEMES:
        return ""

    if not parsed.hostname:
        return ""

    scheme = parsed.scheme.lower()
    hostname = parsed.hostname.lower()

    # Preserve non-default ports.
    netloc = hostname

    if parsed.port is not None:
        if not (
            (scheme == "http" and parsed.port == 80)
            or (scheme == "https" and parsed.port == 443)
        ):
            netloc = f"{hostname}:{parsed.port}"

    path = _PATH_SESSION_RE.sub("", parsed.path or "/")

    if not path.startswith("/"):
        path = f"/{path}"

    query_items = []

    for key, value in parse_qsl(
        parsed.query,
        keep_blank_values=True,
    ):
        if key.lower() in TRACKING_PARAMETERS:
            continue

        query_items.append((key, value))

    # Sorted, so ?a=1&b=2 and ?b=2&a=1 are the same page.
    query = urlencode(
        sorted(query_items),
        doseq=True,
    )

    return urlunparse(
        (
            scheme,
            netloc,
            path,
            "",
            query,
            "",
        )
    )


def resolve_url(
    base_url: str,
    link: str) -> str | None:
    """
    Convert relative links into absolute URLs.
    """

    if not link:
        return None

    link = link.strip()

    if not link:
        return None

    if link.startswith(
        (
            "#",
            "mailto:",
            "javascript:",
            "tel:",
            "data:"
        )
    ):
        return None

    absolute = urljoin(
        base_url,
        link,
    )

    return normalize_url(absolute)

def is_same_host(url: str, target_url: str) -> bool:
    current = urlparse(url).hostname
    target = urlparse(target_url).hostname

    return bool(
        current
        and target
        and current.lower() == target.lower()
    )

def is_allowed_host(
    url: str,
    target_url: str,
    same_host: bool = True,
    allow_subdomains: bool = False,
) -> bool:
    current = urlparse(url).hostname
    target = urlparse(target_url).hostname

    if not current or not target:
        return False

    current = current.lower().rstrip(".")
    target = target.lower().rstrip(".")

    if current == target:
        return True

    if allow_subdomains and current.endswith("." + target):
        return True

    return not same_host

def is_crawlable_url(
    url: str,
    target_url: str,
    same_host: bool = True,
    allow_subdomains: bool = False,
    include_documents: bool = False,
) -> bool:
    normalized = normalize_url(url)

    if not normalized:
        return False

    parsed = urlparse(normalized)

    if parsed.scheme not in ALLOWED_SCHEMES:
        return False

    if not is_allowed_host(
        normalized,
        target_url,
        same_host=same_host,
        allow_subdomains=allow_subdomains,
    ):
        return False

    path_lower = parsed.path.lower()

    for extension in IGNORED_EXTENSIONS:
        if extension == ".pdf" and include_documents:
            continue

        if path_lower.endswith(extension):
            return False

    return True

def is_path_allowed(
    url: str,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
) -> bool:
    """Check whether a URL path satisfies include/exclude rules."""
    path = urlparse(url).path or "/"

    def matches_prefix(path: str, prefix: str) -> bool:
        prefix = prefix.strip()

        if not prefix:
            return False

        if not prefix.startswith("/"):
            prefix = "/" + prefix

        prefix = prefix.rstrip("/") or "/"

        if prefix == "/":
            return True

        return path == prefix or path.startswith(prefix + "/")

    # Exclusions always take priority.
    if any(
        matches_prefix(path, prefix)
        for prefix in (exclude_paths or [])
    ):
        return False

    # If include rules exist, at least one must match.
    if include_paths and not any(
        matches_prefix(path, prefix)
        for prefix in include_paths
    ):
        return False

    return True
