import re
from urllib.parse import unquote, urlparse

import phonenumbers
import tldextract
from buitre.utils.cfemail import decode_cfemail  # noqa: F401
from buitre.utils.soup import make_soup
from phonenumbers import NumberParseException


EMAIL_PATTERN = re.compile(
    r"\b[A-Za-z0-9._%+-]+@"
    r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)

URL_PATTERN = re.compile(
    r"\bhttps?://[^\s<>'\"]+",
    re.IGNORECASE,
)

BARE_DOMAIN_PATTERN = re.compile(
    r"(?<![@\w.-])"
    r"(?:www\.)?"
    r"[A-Za-z0-9]"
    r"[A-Za-z0-9.-]*"
    r"\.[A-Za-z]{2,}"
    r"(?![\w.-])"
)

TLD_EXTRACT = tldextract.TLDExtract(
    suffix_list_urls=(),
)

MONTH_PATTERN = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|"
    r"Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|"
    r"Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|"
    r"Nov(?:ember)?|Dec(?:ember)?)"
)


# File extensions that look like a TLD to the regex but are really
# assets: "logo@2x.png", "banner@3x.webp", "sprite@2x.svg".
# Some (zip, mov) are also real TLDs, so the public-suffix check alone
# is not enough.
ASSET_EXTENSIONS = {
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "ico", "bmp",
    "tif", "tiff", "css", "js", "map", "json", "woff", "woff2", "ttf",
    "otf", "eot", "mp3", "mp4", "mov", "webm", "zip", "gz", "pdf",
}


def _is_valid_email(email: str) -> bool:
    """
    Reject regex matches that are not real mailbox addresses.

    The domain must end in a public suffix known to the Public Suffix
    List (the same data already used for domain extraction), must not
    end in an asset file extension, and must not look like a retina
    image suffix ("@2x").
    """

    local, _, domain = email.rpartition("@")

    if not local or not domain:
        return False

    if local.startswith(".") or local.endswith("."):
        return False

    if ".." in email:
        return False

    last_label = domain.rsplit(".", 1)[-1].lower()

    if last_label in ASSET_EXTENSIONS:
        return False

    first_label = domain.split(".", 1)[0].lower()

    if re.fullmatch(r"\d+(?:\.\d+)?x", first_label):
        return False

    extracted = TLD_EXTRACT(domain.lower())

    return bool(extracted.suffix and extracted.domain)


def extract_emails(text: str) -> list[str]:
    return sorted(
        {
            email
            for email in EMAIL_PATTERN.findall(text)
            if _is_valid_email(email)
        }
    )


# "name [at] example [dot] com", "name(at)example(dot)com", "name{at}..."
_AT_RE = re.compile(r"\s*[\[\(\{]\s*at\s*[\]\)\}]\s*", re.IGNORECASE)
_DOT_RE = re.compile(r"\s*[\[\(\{]\s*dot\s*[\]\)\}]\s*", re.IGNORECASE)


def deobfuscate_emails(text: str) -> str:
    """Turn '[at]' / '(dot)' style obfuscation back into '@' and '.'."""

    return _DOT_RE.sub(".", _AT_RE.sub("@", text))


def collect_emails(
    text: str,
    html: str = "",
    page_text: str = "",
) -> list[dict[str, str]]:
    """
    Find e-mail addresses and record where each came from.

    Sources, strongest first: "mailto" (an explicit mailto: link),
    "cfemail" (Cloudflare-obfuscated), "content" (main text), "page"
    (elsewhere on the page, e.g. the footer), "obfuscated" ([at]/[dot]).
    """

    found: dict[str, dict[str, str]] = {}

    def add(email: str, source: str) -> None:
        email = email.strip().strip(".,;:<>()[]\"'")

        if not email or not _is_valid_email(email):
            return

        found.setdefault(
            email.lower(),
            {"type": "email", "value": email, "source": source},
        )

    if html:
        soup = make_soup(html)

        for tag in soup.find_all("a", href=True):
            href = tag["href"].strip()

            if href.lower().startswith("mailto:"):
                address = unquote(href[7:].split("?")[0])

                for part in re.split(r"[;,]", address):
                    add(part, "mailto")

            elif "email-protection#" in href:
                decoded = decode_cfemail(href.split("#", 1)[1])

                if decoded:
                    add(decoded, "cfemail")

        for tag in soup.find_all(attrs={"data-cfemail": True}):
            decoded = decode_cfemail(tag["data-cfemail"])

            if decoded:
                add(decoded, "cfemail")

    for email in EMAIL_PATTERN.findall(text):
        add(email, "content")

    for email in EMAIL_PATTERN.findall(page_text):
        add(email, "page")

    for source_text in (text, page_text):
        plain = set(EMAIL_PATTERN.findall(source_text))

        for email in EMAIL_PATTERN.findall(deobfuscate_emails(source_text)):
            if email not in plain:
                add(email, "obfuscated")

    return sorted(found.values(), key=lambda item: item["value"].lower())


def registered_domain(host: str) -> str:
    """example.co.uk for www.example.co.uk (subdomains stripped)."""

    host = (host or "").lower().strip(".")
    extracted = TLD_EXTRACT(host)

    if extracted.suffix and extracted.domain:
        return f"{extracted.domain}.{extracted.suffix}"

    return host


def own_registrable_domain(url: str) -> str:
    return registered_domain(urlparse(url).hostname or "")


def outbound_domains(hrefs: list[str], source_url: str) -> list[str]:
    """Every external domain linked from anywhere on the page."""

    own = own_registrable_domain(source_url)
    domains: set[str] = set()

    for href in hrefs:
        if not href.startswith(("http://", "https://", "//")):
            continue

        domain = _normalize_domain(
            href,
            allow_single_character_domain=True,
        )

        if domain and registered_domain(domain) != own:
            domains.add(domain)

    return sorted(domains)


def infer_region(
    source_url: str = "",
    language: str | None = None,
) -> str | None:
    """
    Infer an ISO country/region code from generic page context.

    Examples:
        https://example.in  -> IN
        https://example.co.uk -> GB
        en-IN                -> IN
        en-US                -> US
    """

    # ---------------------------------------------------------
    # First: language/locale
    # ---------------------------------------------------------
    if language:
        match = re.search(
            r"(?:^|[-_])([A-Za-z]{2})$",
            language.strip(),
        )

        if match:
            region = match.group(1).upper()

            if region in phonenumbers.SUPPORTED_REGIONS:
                return region

    # ---------------------------------------------------------
    # Second: website TLD
    # ---------------------------------------------------------
    if source_url:
        try:
            parsed = urlparse(source_url)
            host = parsed.hostname or ""

            extracted = TLD_EXTRACT(host)

            if extracted.suffix:
                suffix_parts = extracted.suffix.split(".")
                possible_region = suffix_parts[-1].upper()

                # libphonenumber uses GB rather than UK.
                if possible_region == "UK":
                    possible_region = "GB"

                if (
                    len(possible_region) == 2
                    and possible_region
                    in phonenumbers.SUPPORTED_REGIONS
                ):
                    return possible_region

        except Exception:
            pass

    return None


def _mask_dates(text: str) -> str:
    """
    Temporarily remove common date formats so they cannot
    be interpreted as phone numbers.
    """

    masked = text

    # DD-MM-YYYY
    # DD/MM/YYYY
    # DD.MM.YYYY
    masked = re.sub(
        r"\b\d{1,2}\s*[-/.]\s*"
        r"\d{1,2}\s*[-/.]\s*"
        r"\d{2,4}\b",
        " ",
        masked,
    )

    # YYYY-MM-DD
    # YYYY/MM/DD
    # YYYY.MM.DD
    masked = re.sub(
        r"\b\d{4}\s*[-/.]\s*"
        r"\d{1,2}\s*[-/.]\s*"
        r"\d{1,2}\b",
        " ",
        masked,
    )

    # Month-name dates.
    masked = re.sub(
        rf"\b{MONTH_PATTERN}\s+"
        r"\d{1,2}(?:st|nd|rd|th)?"
        r"(?:,\s*|\s+)"
        r"\d{4}\b",
        " ",
        masked,
        flags=re.IGNORECASE,
    )

    masked = re.sub(
        rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+"
        rf"{MONTH_PATTERN}\s+\d{{4}}\b",
        " ",
        masked,
        flags=re.IGNORECASE,
    )

    return masked


def _expand_compact_numbers(
    text: str,
) -> list[str]:
    """
    Expand compact telephone formats such as:

        +91-1822-282533,35,83
        01822-282513,31

    This is format-based rather than website-specific.
    """

    pattern = re.compile(
        r"(?P<base>"
        r"(?:\+\d{1,3}[-.\s]?)?"
        r"\d{3,5}"
        r"[-.\s]"
        r"\d{5,8}"
        r")"
        r"(?P<suffixes>"
        r"(?:,\s*\d{1,4})+"
        r")"
    )

    results: list[str] = []

    for match in pattern.finditer(text):

        base = match.group("base")
        suffixes = match.group("suffixes")

        results.append(base)

        for suffix_match in re.finditer(
            r",\s*(\d{1,4})",
            suffixes,
        ):
            suffix = suffix_match.group(1)

            base_without_suffix = re.sub(
                r"\d+$",
                "",
                base,
            )

            base_digits = re.findall(
                r"\d",
                base,
            )

            if len(suffix) <= len(base_digits):
                expanded = (
                    base_without_suffix
                    + suffix
                )

                results.append(expanded)

    return results


def _extract_tel_links(
    html: str,
) -> list[str]:
    """
    Extract phone numbers explicitly declared in tel: links.
    """

    if not html:
        return []

    soup = make_soup(html)

    numbers: list[str] = []

    for tag in soup.find_all(
        "a",
        href=True,
    ):
        href = tag.get("href", "")

        if href.lower().startswith("tel:"):
            number = href[4:].split("?")[0]
            numbers.append(number)

    return numbers


def _format_phone(
    value: str,
    region: str | None,
) -> tuple[str, str] | None:
    """
    Parse and normalize a phone number.

    Returns:
        display_value, deduplication_key
    """

    value = value.strip()

    if not value:
        return None

    try:
        parsed = phonenumbers.parse(
            value,
            region,
        )

    except NumberParseException:
        return None

    if not phonenumbers.is_possible_number(
        parsed
    ):
        return None

    if not phonenumbers.is_valid_number(
        parsed
    ):
        return None

    e164 = phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.E164,
    )

    international = phonenumbers.format_number(
        parsed,
        phonenumbers.PhoneNumberFormat.INTERNATIONAL,
    )

    return international, e164


def _extract_with_library(
    text: str,
    region: str | None,
) -> list[str]:
    """
    Extract phone numbers using Google's
    libphonenumber data through the Python package.
    """

    results: list[str] = []

    try:
        matcher = phonenumbers.PhoneNumberMatcher(
            text,
            region,
        )

        for match in matcher:
            number = match.number

            if not phonenumbers.is_possible_number(
                number
            ):
                continue

            if not phonenumbers.is_valid_number(
                number
            ):
                continue

            formatted = phonenumbers.format_number(
                number,
                phonenumbers.PhoneNumberFormat.INTERNATIONAL,
            )

            results.append(formatted)

    except Exception:
        return results

    return results


def extract_phone_numbers(
    text: str,
    html: str = "",
    region: str | None = None,
) -> list[str]:
    """
    Generic phone-number extraction.

    Sources:
        1. Visible page text
        2. Compact grouped phone formats
        3. tel: hyperlinks

    Numbers are validated and deduplicated using
    libphonenumber.
    """

    safe_text = _mask_dates(text)

    candidates: list[str] = []

    # General text extraction.
    candidates.extend(
        _extract_with_library(
            safe_text,
            region,
        )
    )

    # Compact grouped numbers.
    candidates.extend(
        _expand_compact_numbers(
            safe_text
        )
    )

    # tel: links.
    candidates.extend(
        _extract_tel_links(html)
    )

    normalized: dict[str, str] = {}

    for candidate in candidates:

        result = _format_phone(
            candidate,
            region,
        )

        if result is None:
            continue

        display, key = result

        normalized[key] = display

    return sorted(
        normalized.values()
    )


def _normalize_domain(
    value: str,
    allow_single_character_domain: bool = False,
) -> str | None:
    """
    Validate a domain using the Public Suffix List.
    """

    value = value.strip().rstrip(
        ".,;:!?)]}>\"'"
    )

    if not value:
        return None

    parsed = urlparse(
        value
        if "://" in value or value.startswith("//")
        else f"//{value}"
    )

    hostname = parsed.hostname

    if not hostname:
        return None

    hostname = hostname.lower().strip(".")

    extracted = TLD_EXTRACT(hostname)

    if not extracted.suffix:
        return None

    if not extracted.domain:
        return None

    # Bare text such as B.Sc or B.Tech has a
    # one-character registrable part.
    if (
        len(extracted.domain) < 2
        and not allow_single_character_domain
    ):
        return None

    # Canonicalize www.example.com -> example.com.
    return extracted.fqdn.lower().removeprefix(
        "www."
    )


def _extract_domains_from_html(
    html: str,
) -> list[str]:
    """
    Extract domain candidates from actual hyperlinks.
    """

    if not html:
        return []

    soup = make_soup(html)

    values: list[str] = []

    for tag in soup.find_all(
        "a",
        href=True,
    ):
        href = tag.get("href", "")

        if href.startswith(
            (
                "http://",
                "https://",
                "//",
            )
        ):
            values.append(href)

    return values


def extract_domains(
    text: str,
    html: str = "",
    anchors: list[str] | None = None,
) -> list[str]:
    """
    Generic domain extraction.

    Preference is given to domains that appear in:
        - email addresses
        - explicit URLs
        - actual hyperlinks

    Bare domains are validated through the
    Public Suffix List.

    `anchors` (a list of hrefs) replaces `html` as the hyperlink source,
    so the caller can restrict domains to links inside the main content
    instead of every nav/footer/social link on the page.
    """

    domains: set[str] = set()

    # ---------------------------------------------------------
    # Email domains
    # ---------------------------------------------------------
    for email in extract_emails(text):

        host = email.rsplit(
            "@",
            1,
        )[-1]

        normalized = _normalize_domain(
            host,
            allow_single_character_domain=True,
        )

        if normalized:
            domains.add(normalized)

    # ---------------------------------------------------------
    # Explicit URLs
    # ---------------------------------------------------------
    for value in URL_PATTERN.findall(text):

        normalized = _normalize_domain(
            value,
            allow_single_character_domain=True,
        )

        if normalized:
            domains.add(normalized)

    # ---------------------------------------------------------
    # Actual HTML links have stronger evidence.
    # ---------------------------------------------------------
    hyperlink_values = (
        [h for h in anchors if h.startswith(("http://", "https://", "//"))]
        if anchors is not None
        else _extract_domains_from_html(html)
    )

    for value in hyperlink_values:

        normalized = _normalize_domain(
            value,
            allow_single_character_domain=True,
        )

        if normalized:
            domains.add(normalized)

    # ---------------------------------------------------------
    # Bare domain-like text.
    # ---------------------------------------------------------
    for value in BARE_DOMAIN_PATTERN.findall(text):

        normalized = _normalize_domain(
            value,
            allow_single_character_domain=False,
        )

        if normalized:
            domains.add(normalized)

    return sorted(domains)
