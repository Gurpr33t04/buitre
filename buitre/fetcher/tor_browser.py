from buitre.config import TOR_BROWSER_TIMEOUT_MS
from buitre.fetcher.browser_pool import BrowserPool
from buitre.models import FetchResult


DEFAULT_TIMEOUT = TOR_BROWSER_TIMEOUT_MS


def fetch_with_tor_browser(
    url: str,
    timeout: int = DEFAULT_TIMEOUT,
    pool: BrowserPool | None = None,
) -> FetchResult:
    """Fetch an onion target in Chromium routed through Tor."""

    if pool is not None:
        return pool.fetch(url, timeout)

    with BrowserPool(tor=True) as one_shot:
        return one_shot.fetch(url, timeout)
