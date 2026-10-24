import httpx

from buitre.config import HTTP_TIMEOUT, MAX_BODY_BYTES, USER_AGENT
from buitre.fetcher.transport import (
    fetch_via_client,
    is_text_content,  # noqa: F401  (re-exported for existing imports)
)
from buitre.models import FetchResult


DEFAULT_TIMEOUT = HTTP_TIMEOUT

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.8",
}


def make_client(
    timeout: float = DEFAULT_TIMEOUT,
    proxy: str | None = None,
) -> httpx.Client:
    """
    A reusable client: keep-alive connections and a cookie jar shared
    across requests (thread-safe), instead of a fresh client per page.
    """

    kwargs = {
        "timeout": timeout,
        "follow_redirects": True,
        "headers": DEFAULT_HEADERS,
    }

    if proxy:
        kwargs["proxy"] = proxy

    return httpx.Client(**kwargs)


def fetch_url(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
    max_bytes: int = MAX_BODY_BYTES,
) -> FetchResult:
    """
    Fetch a URL over plain HTTP(S). Pass a shared `client` when fetching
    many pages (the crawler does).
    """

    owns_client = client is None

    if owns_client:
        client = make_client(timeout)

    try:
        return fetch_via_client(client, url, "http", max_bytes)

    except httpx.TimeoutException as exc:
        raise RuntimeError(
            f"Request timed out after {timeout} seconds."
        ) from exc

    except httpx.RequestError as exc:
        raise RuntimeError(
            f"HTTP request failed: {exc}"
        ) from exc

    finally:
        if owns_client:
            client.close()
