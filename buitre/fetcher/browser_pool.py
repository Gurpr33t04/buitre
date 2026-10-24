import queue
import threading
import time
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from typing import Any, Callable

from buitre.config import (
    BROWSER_TIMEOUT_MS,
    DOM_MAX_WAIT_MS,
    DOM_QUIET_MS,
    MAX_API_BYTES,
    MAX_API_RESPONSES,
    MAX_SCROLL_STEPS,
    TOR_BROWSER_TIMEOUT_MS,
    TOR_PROXY,
    USER_AGENT,
)
from buitre.models import FetchResult


# Heavy resources the extractor never reads.
BLOCKED_RESOURCE_TYPES = {"image", "media", "font"}

# Analytics / ad hosts: they keep the network busy and add nothing.
BLOCKED_URL_PARTS = (
    "google-analytics.com",
    "googletagmanager.com",
    "doubleclick.net",
    "googlesyndication.com",
    "facebook.net",
    "connect.facebook",
    "hotjar.com",
    "clarity.ms",
    "segment.io",
    "segment.com/analytics",
    "mixpanel.com",
    "newrelic.com",
    "scorecardresearch.com",
)

# Resolves once the DOM has been quiet for `quiet` ms (or `max` ms passed).
DOM_STABLE_JS = """
([quiet, max]) => new Promise((resolve) => {
  let timer;
  const finish = () => { observer.disconnect(); resolve(true); };
  const observer = new MutationObserver(() => {
    clearTimeout(timer);
    timer = setTimeout(finish, quiet);
  });
  observer.observe(document.documentElement,
    {childList: true, subtree: true, characterData: true});
  timer = setTimeout(finish, quiet);
  setTimeout(finish, max);
})
"""

# Scroll down in steps to trigger lazy loading; stop when height is stable.
SCROLL_JS = """
async (steps) => {
  let last = 0;
  for (let i = 0; i < steps; i++) {
    window.scrollBy(0, window.innerHeight);
    await new Promise(r => setTimeout(r, 250));
    const h = document.documentElement.scrollHeight;
    if (h === last) break;
    last = h;
  }
  window.scrollTo(0, 0);
  return last;
}
"""


def _is_timeout(exc: BaseException) -> bool:
    return exc.__class__.__name__ == "TimeoutError"


class BrowserPool:
    """
    One persistent Chromium shared by all fetches.

    Sync Playwright objects are bound to the thread that created them, so
    the browser lives on its own worker thread and callers submit jobs
    through a queue. Any number of crawler threads can call `fetch()`.

    Per fetch: a fresh, isolated context (no state leaks between pages);
    images/fonts/media/trackers blocked; `domcontentloaded` followed by a
    DOM-quiet wait instead of the flaky `networkidle`; scroll for lazy
    content; JSON XHR/fetch responses captured (often the real data of a
    single-page app).

    `tor=True` routes everything through the Tor SOCKS proxy, disables
    WebRTC IP leaks and never downloads files.
    """

    def __init__(
        self,
        *,
        tor: bool = False,
        headless: bool = True,
        proxy: str = TOR_PROXY,
        playwright_factory: Callable[[], Any] | None = None,
        start_timeout: float = 60.0,
    ) -> None:
        self.tor = tor
        self.headless = headless
        self.proxy = proxy
        self.start_timeout = start_timeout

        self._factory = playwright_factory
        self._jobs: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._start_error: BaseException | None = None
        self._closed = False

    # ------------------------------------------------------------ lifecycle

    def _ensure_started(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Browser pool is closed.")

            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run,
                    name="buitre-browser",
                    daemon=True,
                )
                self._thread.start()

        if not self._ready.wait(self.start_timeout):
            raise RuntimeError("Browser failed to start in time.")

        if self._start_error is not None:
            raise RuntimeError(
                f"Browser failed to start: {self._start_error}. "
                "Run 'playwright install chromium' if the browser "
                "is not installed."
            )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return

            self._closed = True
            thread = self._thread

        if thread is not None:
            self._jobs.put(None)
            thread.join(timeout=15)

    def __enter__(self) -> "BrowserPool":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # --------------------------------------------------------------- worker

    def _launch(self, playwright: Any) -> Any:
        kwargs: dict[str, Any] = {"headless": self.headless}

        if self.tor:
            kwargs["proxy"] = {"server": self.proxy}
            kwargs["args"] = [
                "--force-webrtc-ip-handling-policy="
                "disable_non_proxied_udp",
            ]

        return playwright.chromium.launch(**kwargs)

    def _run(self) -> None:
        try:
            factory = self._factory

            if factory is None:
                from playwright.sync_api import sync_playwright

                factory = sync_playwright

            with factory() as playwright:
                browser = self._launch(playwright)
                self._ready.set()

                while True:
                    job = self._jobs.get()

                    if job is None:
                        break

                    url, timeout_ms, future = job

                    if not future.set_running_or_notify_cancel():
                        continue

                    try:
                        future.set_result(
                            self._fetch_one(browser, url, timeout_ms)
                        )

                    except BaseException as exc:  # noqa: BLE001
                        future.set_exception(exc)

                        # A crashed browser would fail every later job.
                        is_connected = getattr(
                            browser, "is_connected", None
                        )

                        if is_connected and not is_connected():
                            try:
                                browser = self._launch(playwright)
                            except BaseException:  # noqa: BLE001
                                pass

                try:
                    browser.close()
                except Exception:  # noqa: BLE001
                    pass

        except BaseException as exc:  # noqa: BLE001
            self._start_error = exc
            self._ready.set()

            while True:
                try:
                    job = self._jobs.get_nowait()
                except queue.Empty:
                    break

                if job is not None:
                    job[2].set_exception(
                        RuntimeError(f"Browser unavailable: {exc}")
                    )

    # ---------------------------------------------------------------- fetch

    def fetch(
        self,
        url: str,
        timeout_ms: int | None = None,
    ) -> FetchResult:
        if timeout_ms is None:
            timeout_ms = (
                TOR_BROWSER_TIMEOUT_MS if self.tor else BROWSER_TIMEOUT_MS
            )

        self._ensure_started()

        future: Future = Future()
        self._jobs.put((url, timeout_ms, future))

        try:
            return future.result(timeout=timeout_ms / 1000 + 60)

        except FutureTimeout as exc:
            raise RuntimeError(
                f"Browser request timed out after {timeout_ms} ms."
            ) from exc

        except RuntimeError:
            raise

        except BaseException as exc:  # noqa: BLE001
            prefix = "Tor browser" if self.tor else "Browser"

            if _is_timeout(exc):
                raise RuntimeError(
                    f"{prefix} request timed out after {timeout_ms} ms."
                ) from exc

            raise RuntimeError(
                f"{prefix} acquisition failed: {exc}"
            ) from exc

    def _fetch_one(
        self,
        browser: Any,
        url: str,
        timeout_ms: int,
    ) -> FetchResult:
        started = time.perf_counter()

        context = browser.new_context(
            user_agent=USER_AGENT,
            accept_downloads=False,
            java_script_enabled=True,
            viewport={"width": 1280, "height": 900},
        )

        try:
            page = context.new_page()
            page.set_default_timeout(timeout_ms)

            api_responses: list[dict[str, Any]] = []
            api_bytes = [0]

            def handle_route(route: Any) -> None:
                request = route.request

                if (
                    request.resource_type in BLOCKED_RESOURCE_TYPES
                    or any(part in request.url for part in BLOCKED_URL_PARTS)
                ):
                    route.abort()
                else:
                    route.continue_()

            def on_response(response: Any) -> None:
                try:
                    if response.request.resource_type not in (
                        "xhr",
                        "fetch",
                    ):
                        return

                    content_type = (
                        response.headers.get("content-type") or ""
                    ).lower()

                    if "json" not in content_type:
                        return

                    if (
                        len(api_responses) >= MAX_API_RESPONSES
                        or api_bytes[0] >= MAX_API_BYTES
                    ):
                        return

                    text = response.text()

                    if len(text) > 300_000:
                        return

                    api_bytes[0] += len(text)
                    api_responses.append(
                        {
                            "url": response.url,
                            "status": response.status,
                            "text": text,
                        }
                    )

                except Exception:  # noqa: BLE001
                    return

            page.route("**/*", handle_route)
            page.on("response", on_response)

            truncated = False
            response = None

            try:
                response = page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=timeout_ms,
                )

            except Exception as exc:  # noqa: BLE001
                if not _is_timeout(exc):
                    raise

                # Slow site: keep whatever has rendered, if anything.
                truncated = True

                try:
                    partial = page.content()
                except Exception:  # noqa: BLE001
                    partial = ""

                if len(partial) < 500:
                    raise

            self._settle(page, timeout_ms)

            body = page.content()
            final_url = page.url

            headers: dict[str, str] = {}
            status_code = None

            if response is not None:
                status_code = response.status

                try:
                    headers = dict(response.all_headers())
                except Exception:  # noqa: BLE001
                    headers = {}

            return FetchResult(
                requested_url=url,
                final_url=final_url,
                status_code=status_code,
                content_type=headers.get("content-type", "text/html"),
                headers=headers,
                body=body,
                fetch_method="tor_browser" if self.tor else "browser",
                elapsed_time=round(time.perf_counter() - started, 4),
                encoding="utf-8",
                truncated=truncated,
                api_responses=api_responses,
            )

        finally:
            try:
                context.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _settle(page: Any, timeout_ms: int) -> None:
        """Let client-side rendering finish without waiting for 'idle'."""

        try:
            page.wait_for_load_state(
                "load",
                timeout=min(5000, timeout_ms),
            )
        except Exception:  # noqa: BLE001
            pass

        for step in (
            lambda: page.evaluate(
                DOM_STABLE_JS, [DOM_QUIET_MS, DOM_MAX_WAIT_MS]
            ),
            lambda: page.evaluate(SCROLL_JS, MAX_SCROLL_STEPS),
            lambda: page.evaluate(
                DOM_STABLE_JS, [DOM_QUIET_MS, DOM_MAX_WAIT_MS]
            ),
        ):
            try:
                step()
            except Exception:  # noqa: BLE001
                pass
