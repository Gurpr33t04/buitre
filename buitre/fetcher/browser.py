from buitre.config import BROWSER_TIMEOUT_MS
from buitre.fetcher.browser_pool import BrowserPool
from buitre.models import FetchResult


DEFAULT_BROWSER_TIMEOUT = BROWSER_TIMEOUT_MS


def fetch_with_browser(
    url: str,
    timeout: int = DEFAULT_BROWSER_TIMEOUT,
    pool: BrowserPool | None = None,
) -> FetchResult:
    """
    Fetch a page in Chromium.

    Pass a shared `pool` when fetching many pages; without one, a
    throwaway browser is started and closed for this single call.
    """

    if pool is not None:
        return pool.fetch(url, timeout)

    with BrowserPool(tor=False) as one_shot:
        return one_shot.fetch(url, timeout)
