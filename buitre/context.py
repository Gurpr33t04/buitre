import threading
from collections import defaultdict
from typing import Any
from urllib.parse import urlparse

from buitre.cleaner.boilerplate import BoilerplateRegistry
from buitre.crawler.urlpatterns import url_template


class HostMemory:
    """
    Remembers, per host and URL shape, whether plain HTTP was enough.

    After a shape has needed the browser twice (and never worked over
    HTTP), later pages of that shape go straight to the browser instead
    of paying for a useless HTTP fetch first.
    """

    def __init__(self, threshold: int = 2) -> None:
        self.threshold = threshold
        self._browser: dict[str, int] = defaultdict(int)
        self._http: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()

    @staticmethod
    def key(url: str) -> str:
        return url_template(url)

    def record(self, url: str, needed_browser: bool) -> None:
        key = self.key(url)

        with self._lock:
            if needed_browser:
                self._browser[key] += 1
            else:
                self._http[key] += 1

    def prefers_browser(self, url: str) -> bool:
        key = self.key(url)

        with self._lock:
            return (
                self._browser[key] >= self.threshold
                and self._http[key] == 0
            )


class PipelineContext:
    """
    State shared by every page of a crawl: one HTTP client (keep-alive,
    cookies), one browser per mode, per-host strategy memory and
    per-host boilerplate learners.

    Everything is created lazily, so a crawl that never needs a browser
    never starts one. Use as a context manager, or call close().
    """

    def __init__(
        self,
        *,
        learn_boilerplate: bool = True,
        use_embedded_state: bool = True,
        documents: bool = False,
        browser_pool_factory: Any = None,
        http_client_factory: Any = None,
        tor_client_factory: Any = None,
        tor_control: dict | None = None,
    ) -> None:
        self.learn_boilerplate = learn_boilerplate
        self.use_embedded_state = use_embedded_state
        self.documents = documents

        self.host_memory = HostMemory()
        self.boilerplate = BoilerplateRegistry()

        self._browser_pool_factory = browser_pool_factory
        self._http_client_factory = http_client_factory
        self._tor_client_factory = tor_client_factory

        self.tor_control = tor_control or {}

        self._http_client: Any = None
        self._tor_client: Any = None
        self._pools: dict[bool, Any] = {}
        self._lock = threading.Lock()
        self._last_rotation = 0.0
        self._closed = False

    # ------------------------------------------------------------ clients

    def http_client(self) -> Any:
        with self._lock:
            if self._http_client is None:
                factory = self._http_client_factory

                if factory is None:
                    from buitre.fetcher.http import make_client

                    factory = make_client

                self._http_client = factory()

            return self._http_client

    def tor_client(self) -> Any:
        with self._lock:
            if self._tor_client is None:
                factory = self._tor_client_factory

                if factory is None:
                    from buitre.fetcher.tor import make_tor_client

                    factory = make_tor_client

                self._tor_client = factory()

            return self._tor_client

    def browser_pool(self, tor: bool) -> Any:
        with self._lock:
            if tor not in self._pools:
                factory = self._browser_pool_factory

                if factory is None:
                    from buitre.fetcher.browser_pool import BrowserPool

                    factory = BrowserPool

                self._pools[tor] = factory(tor=tor)

            return self._pools[tor]

    # ---------------------------------------------------------- Tor

    def rotate_tor_circuit(self, min_interval: float = 10.0) -> bool:
        """Ask Tor for new circuits (rate-limited; never raises)."""

        import time

        now = time.monotonic()

        with self._lock:
            if now - self._last_rotation < min_interval:
                return False

            self._last_rotation = now

        from buitre.fetcher.tor_control import new_identity

        return new_identity(**self.tor_control)

    # ----------------------------------------------------------- lifecycle

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closed = True
            clients = [self._http_client, self._tor_client]
            pools = list(self._pools.values())

        for client in clients:
            if client is not None:
                try:
                    client.close()
                except Exception:  # noqa: BLE001
                    pass

        for pool in pools:
            try:
                pool.close()
            except Exception:  # noqa: BLE001
                pass

    def __enter__(self) -> "PipelineContext":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()
