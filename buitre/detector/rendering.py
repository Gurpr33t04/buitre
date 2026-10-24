from buitre.models import PageData
from buitre.utils.soup import make_soup


# Statements the page itself makes when scripts did not run. Checked in
# VISIBLE text and <noscript> only. The old check searched raw HTML,
# where "loading..." and "please wait" appear inside every JS bundle.
SCRIPT_REQUIRED_MARKERS = [
    "fallback because interactive scripts did not run",
    "you need to enable javascript",
    "javascript is required",
    "javascript must be enabled",
    "this site requires javascript",
    "this page requires javascript",
    "please turn on javascript",
    "enable javascript to",
]

# Placeholder text of an application that has not rendered yet.
LOADING_MARKERS = ["loading...", "loading…", "please wait"]

# Mount points of single-page applications.
SPA_ROOT_SELECTORS = [
    "#root", "#app", "#__next", "#___gatsby", "#__nuxt",
    "#react-root", "app-root", "[ng-app]",
]


def spa_signals(raw_html: str) -> list[str]:
    """Evidence that the HTML is an application shell, not a document."""

    if not raw_html:
        return []

    soup = make_soup(raw_html)
    signals: list[str] = []

    for selector in SPA_ROOT_SELECTORS:
        try:
            element = soup.select_one(selector)
        except Exception:  # noqa: BLE001
            continue

        if element is not None and len(element.get_text(strip=True)) < 20:
            signals.append(f"empty application root ({selector})")

    bundles = soup.find_all("script", src=True)

    if len(bundles) >= 3:
        signals.append(f"{len(bundles)} script bundles")

    return signals


def _noscript_text(raw_html: str) -> str:
    if not raw_html:
        return ""

    soup = make_soup(raw_html)

    return " ".join(
        tag.get_text(" ", strip=True) for tag in soup.find_all("noscript")
    ).casefold()


def needs_browser_rendering(
    page: PageData,
    raw_html: str = "",
) -> tuple[bool, str]:
    """
    Decide whether browser rendering is likely to add content.

    Short pages are not automatically suspicious: a tiny static page is
    complete. Thin text only triggers the browser when something also
    says "this is an application shell" (empty mount point, script
    bundles, a no-script notice).
    """

    text = page.text.casefold().strip()
    text_length = len(text)

    for marker in SCRIPT_REQUIRED_MARKERS:
        if marker in text:
            return True, f"The page states it needs JavaScript: '{marker}'."

    if text_length < 300:
        for marker in LOADING_MARKERS:
            if marker in text:
                return True, f"Loading placeholder in page text: '{marker}'."

    if text_length < 600:
        noscript = _noscript_text(raw_html)

        for marker in SCRIPT_REQUIRED_MARKERS:
            if marker in noscript:
                return (
                    True,
                    f"<noscript> says JavaScript is required: '{marker}'.",
                )

    if text_length < 40:
        return True, "Practically no readable content was extracted."

    signals = spa_signals(raw_html) if text_length < 600 else []

    if text_length < 600 and any(s.startswith("empty") for s in signals):
        return True, "Application shell detected: " + "; ".join(signals) + "."

    if text_length < 200 and signals:
        return True, "Little content and " + "; ".join(signals) + "."

    if (
        text_length < 500
        and not page.headings
        and len(page.links) <= 3
        and "<script" in raw_html.casefold()
    ):
        return True, "Little structure and scripts present."

    if text_length < 200:
        return False, "Short page with no sign of client-side rendering."

    return False, "HTTP extraction appears sufficient."
