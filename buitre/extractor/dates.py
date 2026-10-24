import re
from datetime import date, datetime


MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_MONTH = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
    r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|"
    r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|"
    r"Dec(?:ember)?)"
)

# Regions that write month before day. Everywhere else is day-first.
MONTH_FIRST_REGIONS = {"US", "PH", "CA"}

ISO_RE = re.compile(r"\b(\d{4})\s*[-/.]\s*(\d{1,2})\s*[-/.]\s*(\d{1,2})\b")

NUMERIC_RE = re.compile(
    r"\b(\d{1,2})\s*([/-])\s*(\d{1,2})\s*\2\s*(\d{2,4})\b"
    r"|\b(\d{1,2})\s*\.\s*(\d{1,2})\s*\.\s*(\d{4})\b"
)

MDY_RE = re.compile(
    rf"\b({_MONTH})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,\s*|\s+)(\d{{4}})\b",
    re.IGNORECASE,
)

DMY_RE = re.compile(
    rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MONTH})\.?,?\s+(\d{{4}})\b",
    re.IGNORECASE,
)

# Kept for backwards compatibility with code that imported the old list.
DATE_PATTERNS = [ISO_RE, NUMERIC_RE, MDY_RE, DMY_RE]


def _valid(year: int, month: int, day: int) -> str | None:
    if not 1900 <= year <= 2100:
        return None

    try:
        return date(year, month, day).isoformat()

    except ValueError:
        return None


def _year(value: str) -> int:
    year = int(value)

    if len(value) == 2:
        return 2000 + year if year <= 69 else 1900 + year

    return year


def _month_number(name: str) -> int:
    return MONTHS[name[:3].lower()]


def normalize_date(value: str) -> str:
    """Normalize whitespace around date separators (kept for compat)."""

    value = re.sub(r"\s*-\s*", "-", value)
    value = re.sub(r"\s*/\s*", "/", value)

    return " ".join(value.split())


def _day_first(region: str | None) -> bool:
    return (region or "").upper() not in MONTH_FIRST_REGIONS


def extract_dates(
    text: str,
    region: str | None = None,
) -> list[str]:
    """
    Extract dates from text and return them as ISO-8601 (YYYY-MM-DD).

    Ambiguous numeric dates (03/04/2026) follow the page's region:
    day-first almost everywhere, month-first for US/PH/CA. If one part is
    greater than 12 there is no ambiguity and it is resolved directly.
    Impossible dates (31/02/2026) are dropped, not guessed.
    """

    found: set[str] = set()
    day_first = _day_first(region)

    for match in ISO_RE.finditer(text):
        iso = _valid(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
        )

        if iso:
            found.add(iso)

    for match in NUMERIC_RE.finditer(text):
        if match.group(1):
            a, b, year = (
                int(match.group(1)),
                int(match.group(3)),
                _year(match.group(4)),
            )
        else:
            a, b, year = (
                int(match.group(5)),
                int(match.group(6)),
                int(match.group(7)),
            )

        if a > 12 and b <= 12:
            day, month = a, b
        elif b > 12 and a <= 12:
            day, month = b, a
        elif day_first:
            day, month = a, b
        else:
            day, month = b, a

        iso = _valid(year, month, day)

        if iso:
            found.add(iso)

    for match in MDY_RE.finditer(text):
        iso = _valid(
            int(match.group(3)),
            _month_number(match.group(1)),
            int(match.group(2)),
        )

        if iso:
            found.add(iso)

    for match in DMY_RE.finditer(text):
        iso = _valid(
            int(match.group(3)),
            _month_number(match.group(2)),
            int(match.group(1)),
        )

        if iso:
            found.add(iso)

    return sorted(found)


def to_iso_datetime(value: str | None) -> str | None:
    """
    Normalize a machine-readable timestamp (meta tags, <time datetime>,
    JSON-LD) to ISO-8601. Accepts '2026-10-01', '2026-10-01T09:30:00Z',
    '2026-10-01T09:30:00+05:30'. Returns None if it cannot be parsed.
    """

    if not value:
        return None

    value = value.strip()

    if not value:
        return None

    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return _valid(*(int(p) for p in value.split("-")))

    candidate = value.replace("Z", "+00:00").replace("z", "+00:00")

    try:
        return datetime.fromisoformat(candidate).isoformat()

    except ValueError:
        pass

    dates = extract_dates(value)

    return dates[0] if len(dates) == 1 else None
