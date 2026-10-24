"""Encoding, transport, Tor control and the browser pool (no network)."""

import socketserver
import threading

from buitre.fetcher.browser_pool import BrowserPool
from buitre.fetcher.encoding import decode_body
from buitre.fetcher.transport import build_fetch_result, fetch_via_client
from buitre.fetcher.tor_control import new_identity


# ---- encoding ----------------------------------------------------------

def test_utf8_is_default():
    text, enc = decode_body("héllo wörld".encode("utf-8"), "text/html")

    assert text == "héllo wörld" and enc == "utf-8"


def test_cp1252_page_without_declaration_is_not_mojibake():
    # Typical legacy page: "Café – 5€" saved as windows-1252, no charset.
    raw = "Café – 5€".encode("cp1252")

    text, _ = decode_body(raw, "text/html")

    assert text == "Café – 5€"


def test_header_charset_is_honoured():
    raw = "こんにちは世界".encode("shift_jis")

    text, enc = decode_body(raw, "text/html; charset=Shift_JIS")

    assert text == "こんにちは世界"


def test_meta_charset_is_honoured():
    raw = (
        b'<html><head><meta charset="windows-1251"></head><body>'
        + "Привет мир".encode("cp1251")
        + b"</body></html>"
    )

    text, _ = decode_body(raw, "text/html")

    assert "Привет мир" in text


def test_mislabelled_latin1_utf8_is_decoded_as_utf8():
    raw = "Zürich – naïve café".encode("utf-8")

    text, enc = decode_body(raw, "text/html; charset=ISO-8859-1")

    assert text == "Zürich – naïve café" and enc == "utf-8"


def test_bom_wins():
    raw = b"\xef\xbb\xbf" + "añb".encode("utf-8")

    text, _ = decode_body(raw, "text/html; charset=latin-1")

    assert text == "añb"


def test_unknown_declared_charset_falls_back():
    text, _ = decode_body("plain ascii".encode(), "text/html; charset=bogus-9")

    assert text == "plain ascii"


# ---- transport ---------------------------------------------------------

def make(content, headers, **kw):
    return build_fetch_result(
        requested_url="https://e.com/",
        final_url="https://e.com/",
        status_code=200,
        headers=headers,
        content=content,
        method="http",
        elapsed=0.1,
        **kw,
    )


def test_text_response_is_decoded_and_records_encoding():
    result = make("é".encode("utf-8"), {"Content-Type": "text/html"})

    assert result.body == "é" and result.encoding == "utf-8"


def test_pdf_keeps_bytes_not_text():
    result = make(b"%PDF-1.4 data", {"content-type": "application/pdf"})

    assert result.body == "" and result.content_bytes.startswith(b"%PDF")


def test_pdf_without_content_type_is_sniffed():
    result = make(b"%PDF-1.7 data", {})

    assert result.content_type == "application/pdf"


def test_image_payload_is_ignored():
    result = make(b"\x89PNG....", {"content-type": "image/png"})

    assert result.body == "" and result.content_bytes is None


class FakeResponse:
    def __init__(self, chunks, headers, status=200, url="https://e.com/x"):
        self._chunks, self.headers = chunks, headers
        self.status_code, self.url = status, url
        self.read = 0

    def iter_bytes(self):
        for chunk in self._chunks:
            self.read += len(chunk)
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeClient:
    def __init__(self, response):
        self.response = response

    def stream(self, method, url):
        return self.response


def test_streaming_cuts_text_at_the_cap():
    response = FakeResponse([b"a" * 600] * 10, {"content-type": "text/html"})

    result = fetch_via_client(FakeClient(response), "https://e.com/", "http",
                              max_bytes=1000)

    assert result.truncated is True
    assert len(result.body) == 1000
    assert response.read < 6000  # stopped early, did not read it all


def test_binary_body_is_never_downloaded():
    response = FakeResponse([b"x" * 1000] * 50, {"content-type": "video/mp4"})

    result = fetch_via_client(FakeClient(response), "https://e.com/", "http")

    assert response.read == 0
    assert result.body == ""


# ---- Tor control port --------------------------------------------------

class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        while True:
            data = self.request.recv(1024)

            if not data:
                return

            line = data.decode().strip()
            self.server.received.append(line)

            if line.startswith("AUTHENTICATE"):
                ok = self.server.password in line if self.server.password else True
                self.request.sendall(b"250 OK\r\n" if ok else b"515 Bad\r\n")
            elif line == "SIGNAL NEWNYM":
                self.request.sendall(b"250 OK\r\n")
            else:
                self.request.sendall(b"250 closing\r\n")
                return


def _serve(password=None):
    server = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    server.received, server.password = [], password
    threading.Thread(target=server.serve_forever, daemon=True).start()

    return server


def test_new_identity_sends_authenticate_then_newnym():
    server = _serve()

    try:
        assert new_identity("127.0.0.1", server.server_address[1]) is True
        assert server.received[:2] == ["AUTHENTICATE", "SIGNAL NEWNYM"]
    finally:
        server.shutdown()


def test_new_identity_with_password_and_wrong_password():
    server = _serve(password="secret")

    try:
        port = server.server_address[1]

        assert new_identity("127.0.0.1", port, password="secret") is True
        assert new_identity("127.0.0.1", port, password="nope") is False
    finally:
        server.shutdown()


def test_new_identity_never_raises_when_port_is_closed():
    assert new_identity("127.0.0.1", 1, timeout=0.5) is False


# ---- Browser pool with a fake Playwright -------------------------------

class TimeoutError(Exception):  # noqa: A001 - name checked by the pool
    pass


class FakeRoute:
    def __init__(self, url, rtype):
        self.request = type("R", (), {"url": url, "resource_type": rtype})()
        self.aborted = self.continued = False

    def abort(self):
        self.aborted = True

    def continue_(self):
        self.continued = True


class FakeResp:
    def __init__(self, url, rtype, ctype, text, status=200):
        self.url, self.status = url, status
        self.request = type("R", (), {"resource_type": rtype})()
        self.headers = {"content-type": ctype}
        self._text = text

    def text(self):
        return self._text


class FakeMainResponse:
    status = 200

    def all_headers(self):
        return {"content-type": "text/html; charset=utf-8"}


class FakePage:
    def __init__(self, site, log):
        self.site, self.log = site, log
        self.url = ""
        self.handlers, self.route_handler = {}, None

    def set_default_timeout(self, ms):
        self.log["timeout"] = ms

    def route(self, pattern, handler):
        self.route_handler = handler

    def on(self, event, handler):
        self.handlers[event] = handler

    def goto(self, url, wait_until=None, timeout=None):
        self.log["wait_until"] = wait_until
        self.url = url

        if self.site.get("goto_timeout"):
            raise TimeoutError("goto timed out")

        for rurl, rtype in self.site.get("resources", []):
            route = FakeRoute(rurl, rtype)
            self.route_handler(route)
            self.log.setdefault("routes", []).append(route)

        for resp in self.site.get("responses", []):
            self.handlers["response"](resp)

        return FakeMainResponse()

    def wait_for_load_state(self, state, timeout=None):
        self.log["load_state"] = state

    def evaluate(self, js, arg=None):
        self.log.setdefault("evals", []).append(arg)

    def content(self):
        return self.site.get("html", "<html><body>x</body></html>")


class FakeContext:
    def __init__(self, site, log):
        self.site, self.log = site, log

    def new_page(self):
        return FakePage(self.site, self.log)

    def close(self):
        self.log["context_closed"] = self.log.get("context_closed", 0) + 1


class FakeBrowser:
    def __init__(self, site, log, launches):
        self.site, self.log, self.launches = site, log, launches
        self.connected = True

    def new_context(self, **kw):
        self.log["context_kwargs"] = kw
        return FakeContext(self.site, self.log)

    def is_connected(self):
        return self.connected

    def close(self):
        self.log["browser_closed"] = True


class FakePlaywright:
    def __init__(self, site, log):
        self.site, self.log = site, log
        self.launches = []
        self.chromium = self

    def launch(self, **kw):
        self.launches.append(kw)
        self.log["launches"] = self.launches
        return FakeBrowser(self.site, self.log, self.launches)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def pool_for(site, log, **kw):
    return BrowserPool(playwright_factory=lambda: FakePlaywright(site, log), **kw)


def test_pool_fetches_and_blocks_heavy_resources():
    log = {}
    site = {
        "html": "<html><body><p>rendered</p></body></html>",
        "resources": [
            ("https://e.com/a.png", "image"),
            ("https://www.google-analytics.com/collect", "script"),
            ("https://e.com/app.js", "script"),
        ],
    }

    with pool_for(site, log) as pool:
        result = pool.fetch("https://e.com/")

    routes = log["routes"]

    assert [r.aborted for r in routes] == [True, True, False]
    assert routes[2].continued
    assert result.fetch_method == "browser"
    assert "rendered" in result.body
    assert log["wait_until"] == "domcontentloaded"   # never networkidle
    assert log["context_closed"] == 1
    assert log["browser_closed"] is True


def test_pool_captures_json_api_responses_only():
    log = {}
    site = {
        "responses": [
            FakeResp("https://e.com/api/posts", "xhr", "application/json",
                     '{"body": "hello"}'),
            FakeResp("https://e.com/style.css", "stylesheet", "text/css", "x"),
            FakeResp("https://e.com/page", "fetch", "text/html", "<p>"),
        ],
    }

    with pool_for(site, log) as pool:
        result = pool.fetch("https://e.com/")

    assert [r["url"] for r in result.api_responses] == [
        "https://e.com/api/posts"
    ]


def test_pool_reuses_one_browser_for_many_fetches():
    log = {}

    with pool_for({}, log) as pool:
        for _ in range(3):
            pool.fetch("https://e.com/")

    assert len(log["launches"]) == 1
    assert log["context_closed"] == 3


def test_pool_keeps_partial_page_on_slow_site():
    log = {}
    site = {"goto_timeout": True, "html": "<html>" + "x" * 800 + "</html>"}

    with pool_for(site, log) as pool:
        result = pool.fetch("https://e.com/")

    assert result.truncated is True


def test_pool_raises_when_nothing_rendered_before_timeout():
    log = {}
    site = {"goto_timeout": True, "html": "<html></html>"}

    with pool_for(site, log) as pool:
        try:
            pool.fetch("https://e.com/")
        except RuntimeError as exc:
            assert "timed out" in str(exc)
        else:
            raise AssertionError("expected RuntimeError")


def test_tor_pool_uses_proxy_and_webrtc_guard():
    log = {}

    with pool_for({}, log, tor=True) as pool:
        result = pool.fetch("http://abc.onion/")

    launch = log["launches"][0]

    assert launch["proxy"] == {"server": "socks5://127.0.0.1:9050"}
    assert any("webrtc" in a for a in launch["args"])
    assert log["context_kwargs"]["accept_downloads"] is False
    assert result.fetch_method == "tor_browser"


def test_pool_reports_startup_failure_clearly():
    class Broken:
        def __enter__(self):
            raise RuntimeError("Executable doesn't exist")

        def __exit__(self, *a):
            return False

    pool = BrowserPool(playwright_factory=Broken)

    try:
        pool.fetch("https://e.com/")
    except RuntimeError as exc:
        assert "playwright install" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
    finally:
        pool.close()


# ---- encoding: undeclared legacy pages (detector must not pick DOS pages)

def test_realistic_undeclared_cp1252_page():
    html = (
        "<html><body><p>The café's “quoted” price – 5€ for the résumé "
        "service. </p>" * 6
    )

    text, enc = decode_body(html.encode("cp1252"), "text/html")

    assert enc == "cp1252" and text == html


def test_undeclared_cyrillic_and_japanese_are_detected():
    russian = ("Москва является столицей России и крупнейшим городом "
               "страны. " * 5)
    japanese = ("東京は日本の首都であり、人口が最も多い都市の一つです。" * 5)

    text, _ = decode_body(russian.encode("cp1251"), "text/html")
    assert text == russian

    text, _ = decode_body(japanese.encode("shift_jis"), "text/html")
    assert text == japanese
