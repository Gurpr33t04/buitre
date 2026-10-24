import httpx

from buitre.config import (
    MAX_BODY_BYTES,
    TOR_HTTP_TIMEOUT,
    TOR_PROXY,
    USER_AGENT,
)
from buitre.fetcher.transport import (
    fetch_via_client,
    is_text_content,
)
from buitre.models import FetchResult


DEFAULT_TIMEOUT = TOR_HTTP_TIMEOUT

DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,"
        "application/xml;q=0.9,"
        "application/json;q=0.8,*/*;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.8",
}


def _is_text_content(content_type: str) -> bool:
    return is_text_content(content_type)


def make_tor_client(
    timeout: float = DEFAULT_TIMEOUT,
    proxy: str = TOR_PROXY,
) -> httpx.Client:
    return httpx.Client(
        proxy=proxy,
        timeout=timeout,
        follow_redirects=True,
        headers=DEFAULT_HEADERS,
    )


def _classify_tor_error(exc: Exception) -> str:
    message = str(exc).lower()

    if "connection refused" in message:
        return (
            "Tor SOCKS proxy is unavailable. "
            "Make sure the Tor service is running "
            "and listening on 127.0.0.1:9050."
        )

    if "host unreachable" in message:
        return (
            "The onion target is unreachable through Tor. "
            "The Tor proxy is reachable, but the target "
            "onion service could not be reached."
        )

    if "network is unreachable" in message:
        return (
            "The Tor network is currently unreachable. "
            "Check that the Tor service is running correctly."
        )

    if "timed out" in message or "timeout" in message:
        return (
            "The onion target timed out through Tor. "
            "The service may be slow or temporarily unavailable."
        )

    return (
        f"Tor connection failed: {exc}"
    )


def fetch_with_tor(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
    max_bytes: int = MAX_BODY_BYTES,
) -> FetchResult:
    """
    Fetch an onion (or any) URL through the Tor SOCKS proxy.

    Onion services are slow: the default timeout is 60 s. Retrying and
    circuit rotation are handled by the crawler.
    """

    owns_client = client is None

    if owns_client:
        client = make_tor_client(timeout)

    try:
        return fetch_via_client(
            client,
            url,
            "tor_http",
            max_bytes,
        )

    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "Tor request timed out. "
            "The onion target may be slow or unavailable."
        ) from exc

    except httpx.RequestError as exc:
        raise RuntimeError(
            _classify_tor_error(exc)
        ) from exc

    finally:
        if owns_client:
            client.close()
