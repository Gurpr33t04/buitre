from enum import Enum

class FetchStrategy(str, Enum):
	HTTP = "http"
	TOR_HTTP = "tor_http"
	BROWSER = "browser"
	TOR_BROWSER = "tor_browser"
