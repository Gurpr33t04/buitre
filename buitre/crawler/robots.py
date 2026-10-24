import re
import threading
from dataclasses import dataclass, field
from urllib.parse import urlparse

from buitre.config import ROBOTS_TOKEN


def default_fetch_text(url: str) -> tuple[int | None, str]:
    """
    Fetch a small text resource (robots.txt, sitemap) -> (status, body).
    Returns (None, "") on network failure. Onion hosts go through Tor.
    """

    from buitre.fetcher.http import fetch_url
    from buitre.fetcher.tor import fetch_with_tor

    try:
        if (urlparse(url).hostname or "").endswith(".onion"):
            result = fetch_with_tor(url)
        else:
            result = fetch_url(url)

    except Exception:  # noqa: BLE001
        return None, ""

    return result.status_code, result.body


def _pattern_to_regex(pattern: str) -> re.Pattern:
    """robots.txt path pattern: '*' matches anything, '$' anchors the end."""

    anchored = pattern.endswith("$")

    if anchored:
        pattern = pattern[:-1]

    regex = ".*".join(re.escape(part) for part in pattern.split("*"))

    return re.compile("^" + regex + ("$" if anchored else ""))


@dataclass
class _Group:
    agents: list[str] = field(default_factory=list)
    rules: list[tuple[bool, str, re.Pattern]] = field(default_factory=list)
    delay: float | None = None


class RobotsRules:
    """
    A robots.txt parsed the way modern crawlers read it (RFC 9309):
    the most specific user-agent group wins, the longest matching rule
    wins, and Allow beats Disallow on a tie. Supports '*' and '$'.
    """

    def __init__(
        self,
        body: str = "",
        *,
        allow_all: bool = False,
        disallow_all: bool = False,
    ) -> None:
        self.allow_all = allow_all
        self.disallow_all = disallow_all
        self.sitemaps: list[str] = []
        self._groups: list[_Group] = []

        if body:
            self._parse(body)

    def _parse(self, body: str) -> None:
        group: _Group | None = None
        last_was_agent = False

        for raw in body.splitlines():
            line = raw.split("#", 1)[0].strip()

            if ":" not in line:
                continue

            field_name, _, value = line.partition(":")
            field_name = field_name.strip().lower()
            value = value.strip()

            if field_name == "user-agent":
                if group is None or not last_was_agent:
                    group = _Group()
                    self._groups.append(group)

                group.agents.append(value.lower())
                last_was_agent = True
                continue

            last_was_agent = False

            if field_name == "sitemap":
                if value:
                    self.sitemaps.append(value)

                continue

            if group is None:
                continue

            if field_name in ("allow", "disallow"):
                if value:
                    group.rules.append(
                        (
                            field_name == "allow",
                            value,
                            _pattern_to_regex(value),
                        )
                    )

            elif field_name == "crawl-delay":
                try:
                    group.delay = float(value)
                except ValueError:
                    pass

    def _group_for(self, agent: str) -> _Group | None:
        agent = agent.lower()

        best: _Group | None = None
        best_length = -1

        for group in self._groups:
            for name in group.agents:
                if name == "*":
                    length = 0
                elif name and name in agent:
                    length = len(name)
                else:
                    continue

                if length > best_length:
                    best, best_length = group, length

        return best

    def can_fetch(self, url: str, agent: str = ROBOTS_TOKEN) -> bool:
        if self.disallow_all:
            return False

        if self.allow_all:
            return True

        group = self._group_for(agent)

        if group is None:
            return True

        parsed = urlparse(url)
        path = parsed.path or "/"

        if parsed.query:
            path += "?" + parsed.query

        verdict = True
        best_length = -1

        for allow, raw, regex in group.rules:
            if regex.match(path):
                length = len(raw)

                if length > best_length or (
                    length == best_length and allow
                ):
                    verdict, best_length = allow, length

        return verdict

    def crawl_delay(self, agent: str = ROBOTS_TOKEN) -> float | None:
        group = self._group_for(agent)

        return group.delay if group else None


class RobotsCache:
    """
    Fetches and caches robots.txt once per origin (thread-safe).

    Status handling follows common crawler practice: 200 -> parse;
    4xx -> no restrictions; 5xx -> treat the whole site as off-limits
    for this crawl; network failure -> no restrictions (cannot know).
    """

    def __init__(
        self,
        fetch_text=None,
        agent: str = ROBOTS_TOKEN,
    ) -> None:
        # Looked up at call time so tests can swap default_fetch_text.
        self._fetch_text = fetch_text or (
            lambda url: default_fetch_text(url)
        )
        self.agent = agent
        self._cache: dict[str, RobotsRules] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _origin(url: str) -> str:
        parsed = urlparse(url)

        return f"{parsed.scheme}://{parsed.netloc}"

    def rules_for(self, url: str) -> RobotsRules:
        origin = self._origin(url)

        with self._lock:
            if origin in self._cache:
                return self._cache[origin]

            status, body = self._fetch_text(origin + "/robots.txt")

            if status is not None and status >= 500:
                rules = RobotsRules(disallow_all=True)
            elif status == 200:
                rules = RobotsRules(body)
            else:
                rules = RobotsRules(allow_all=True)

            self._cache[origin] = rules

            return rules

    def can_fetch(self, url: str) -> bool:
        return self.rules_for(url).can_fetch(url, self.agent)

    def crawl_delay(self, url: str) -> float | None:
        return self.rules_for(url).crawl_delay(self.agent)

    def sitemaps(self, url: str) -> list[str]:
        return list(self.rules_for(url).sitemaps)
