"""Central defaults. Everything here can be overridden per call."""

VERSION = "0.2.0"

# Full User-Agent string and the short token used for robots.txt matching.
USER_AGENT = f"Buitre/{VERSION} (Python Web Information Extractor)"
ROBOTS_TOKEN = "Buitre"

# HTTP
HTTP_TIMEOUT = 15.0
TOR_HTTP_TIMEOUT = 60.0
MAX_BODY_BYTES = 8 * 1024 * 1024
MAX_DOCUMENT_BYTES = 20 * 1024 * 1024

# Browser
BROWSER_TIMEOUT_MS = 20000
TOR_BROWSER_TIMEOUT_MS = 90000
DOM_QUIET_MS = 600
DOM_MAX_WAIT_MS = 6000
MAX_SCROLL_STEPS = 6
MAX_API_RESPONSES = 20
MAX_API_BYTES = 1_000_000

# Tor
TOR_PROXY = "socks5://127.0.0.1:9050"
TOR_CONTROL_HOST = "127.0.0.1"
TOR_CONTROL_PORT = 9051
