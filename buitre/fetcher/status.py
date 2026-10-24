from dataclasses import dataclass

from buitre.fetcher.http import is_text_content
from buitre.models import FetchResult


OK = "ok"
RATE_LIMITED = "rate_limited"
BLOCKED = "blocked"
CLIENT_ERROR = "client_error"
SERVER_ERROR = "server_error"
NON_TEXT = "non_text"

# Phrases found on bot-protection / challenge interstitials.
CHALLENGE_MARKERS = [
    "just a moment...",
    "attention required! | cloudflare",
    "checking your browser before accessing",
    "cf-browser-verification",
    "cf-challenge",
    "enable javascript and cookies to continue",
    "verify you are human",
    "are you a robot",
    "unusual traffic from your computer",
    "_incapsula_resource",
    "request unsuccessful. incapsula",
    "px-captcha",
    "captcha-delivery.com",
    "errors.edgesuite.net",
]

# Challenge pages are small. Only inspect the body when the status is
# suspicious or the document is tiny, so a long article that happens to
# mention "verify you are human" is never misclassified.
CHALLENGE_STATUSES = {403, 429, 503}
CHALLENGE_MAX_BODY = 5000


@dataclass(frozen=True)
class FetchVerdict:
    verdict: str
    reason: str
    retry_after: str | None = None

    @property
    def ok(self) -> bool:
        return self.verdict == OK


def _header(headers: dict[str, str], name: str) -> str | None:
    wanted = name.lower()

    for key, value in headers.items():
        if key.lower() == wanted:
            return value

    return None


def looks_like_challenge(
    body: str,
    status_code: int | None,
) -> bool:
    if not body:
        return False

    if (
        status_code not in CHALLENGE_STATUSES
        and len(body) > CHALLENGE_MAX_BODY
    ):
        return False

    lowered = body.casefold()

    return any(marker in lowered for marker in CHALLENGE_MARKERS)


def classify_fetch(fetch: FetchResult) -> FetchVerdict:
    """
    Decide whether a fetch result is real page content.

    Order matters: a 429 carrying a Cloudflare page is still a rate
    limit; a 403/503 carrying a challenge page is "blocked" (worth one
    browser attempt); any other 4xx/5xx is an error page, not content.
    """

    status = fetch.status_code

    if status == 429:
        return FetchVerdict(
            RATE_LIMITED,
            "HTTP 429: the server is rate limiting requests.",
            retry_after=_header(fetch.headers, "retry-after"),
        )

    if looks_like_challenge(fetch.body, status):
        return FetchVerdict(
            BLOCKED,
            "The response looks like a bot-protection challenge page"
            + (f" (HTTP {status})." if status else "."),
        )

    if status is not None and status >= 500:
        return FetchVerdict(
            SERVER_ERROR,
            f"HTTP {status}: server error.",
        )

    if status is not None and status >= 400:
        return FetchVerdict(
            CLIENT_ERROR,
            f"HTTP {status}: the page is not available.",
        )

    content_type = fetch.content_type or ""

    if content_type and not is_text_content(content_type):
        return FetchVerdict(
            NON_TEXT,
            f"Unsupported non-text content type: {content_type}.",
        )

    return FetchVerdict(OK, "Response looks like page content.")
