import re
from urllib.parse import parse_qsl, urlparse


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_HEX_RE = re.compile(r"^[0-9a-f]{8,}$", re.I)
_DATE_RE = re.compile(r"^\d{4}(?:-\d{2}){0,2}$")

SESSION_PARAMS = {
    "phpsessid", "jsessionid", "sessionid", "sessid", "session_id", "sid",
    "aspsessionid", "cfid", "cftoken",
}


def _segment_template(segment: str) -> str:
    if segment.isdigit():
        return "{n}"

    if _UUID_RE.match(segment):
        return "{uuid}"

    if _HEX_RE.match(segment):
        return "{hex}"

    if _DATE_RE.match(segment):
        return "{date}"

    stem, dot, extension = segment.rpartition(".")
    base = stem if dot else segment

    # Long hyphenated slugs ("first-look-at-the-new-model-2026").
    if base.count("-") >= 3 or len(base) > 40:
        return "{slug}" + (f".{extension}" if dot else "")

    return segment


def url_template(url: str) -> str:
    """
    Collapse a URL to its *shape*: ids, dates and slugs become
    placeholders and query values are dropped, keeping parameter names.

    /blog/2026/10/my-long-post-title  ->  host/blog/{date}/{n}/{slug}
    /laptops/filter?Series=ProArt     ->  host/laptops/filter?Series

    The crawler caps how many URLs of one shape it will queue, which is
    what stops calendars, facet filters and endless pagination.
    """

    parsed = urlparse(url)
    segments = [_segment_template(s) for s in parsed.path.split("/") if s]

    # Strip ;jsessionid=... path parameters.
    segments = [s.split(";")[0] for s in segments]

    keys = sorted(
        {k.lower() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        - SESSION_PARAMS
    )

    template = f"{(parsed.hostname or '').lower()}/{'/'.join(segments)}"

    if keys:
        template += "?" + "&".join(keys)

    return template


def has_query(url: str) -> bool:
    return bool(urlparse(url).query)


def looks_like_trap(url: str) -> str | None:
    """
    Reason string when a URL looks like a crawler trap, else None.

    Traps generate unlimited distinct URLs: relative links that keep
    appending path segments, repeating directories, giant query strings.
    """

    parsed = urlparse(url)
    segments = [s for s in parsed.path.split("/") if s]

    if len(segments) > 10:
        return "path too deep"

    for segment in set(segments):
        if segments.count(segment) >= 3:
            return f"segment '{segment}' repeats"

    for size in (2, 3):
        for i in range(len(segments) - size * 3 + 1):
            chunk = segments[i:i + size]

            if segments[i + size:i + 2 * size] == chunk \
                    and segments[i + 2 * size:i + 3 * size] == chunk:
                return "directory cycle"

    params = parse_qsl(parsed.query, keep_blank_values=True)

    if len(params) > 6:
        return "too many query parameters"

    if len(parsed.query) > 200:
        return "query string too long"

    return None
