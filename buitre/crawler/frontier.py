import heapq
import itertools
import math
import re
from dataclasses import dataclass
from urllib.parse import urlparse


# Small, generic hints. They only nudge the order; they never exclude
# a URL. Supply `focus` keywords to the crawler for your own domain.
POSITIVE_WORDS = {
    "about", "contact", "news", "article", "articles", "research",
    "publication", "publications", "notice", "notices", "circular",
    "circulars", "admission", "admissions", "department", "departments",
    "faculty", "people", "team", "product", "products", "docs",
    "documentation", "blog", "report", "reports", "event", "events",
    "program", "programme", "programs", "course", "courses", "press",
}

NEGATIVE_WORDS = {
    "login", "logout", "signin", "signup", "register", "cart", "checkout",
    "privacy", "terms", "cookie", "cookies", "share", "print", "facebook",
    "twitter", "instagram", "linkedin", "youtube", "subscribe", "feed",
    "comment", "comments", "replytocom", "wp-admin", "wp-login", "calendar",
    "password",
}

NEXT_WORDS = {"next", "older", "more", "\u203a", "\u00bb", ">"}

REGION_BONUS = {
    "main": 2.0,
    "body": 0.5,
    "header": -0.5,
    "aside": -1.0,
    "nav": 0.0,
    "footer": -2.0,
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def score_link(
    url: str,
    text: str = "",
    region: str = "body",
    rel: list[str] | None = None,
    depth: int = 1,
    parent_quality: float = 0.5,
    template_seen: int = 0,
    focus: list[str] | None = None,
) -> float:
    """
    Priority of following a link (higher = sooner).

    Combines where the link sits (main content beats footer), what its
    anchor text and URL say, how close to the start it is, how good the
    page it came from was, and how many URLs of the same shape have
    already been queued (novelty). Used for best-first crawling, so a
    page budget is spent on the most promising pages first.
    """

    rel = rel or []
    parsed = urlparse(url)

    score = 10.0 - 1.5 * depth
    score += REGION_BONUS.get(region, 0.0)
    score += 2.0 * parent_quality

    anchor_words = _words(text)
    path_words = _words(parsed.path.replace("/", " ").replace("_", "-"))

    if anchor_words & POSITIVE_WORDS:
        score += 1.5

    if path_words & POSITIVE_WORDS:
        score += 1.0

    if anchor_words & NEGATIVE_WORDS or {
        "log in", "sign in", "sign up",
    } & {text.lower().strip()}:
        score -= 3.0

    if path_words & NEGATIVE_WORDS:
        score -= 3.0

    if focus:
        haystack = f"{text} {parsed.path} {parsed.query}".lower()

        if any(word.lower() in haystack for word in focus):
            score += 3.0

    # Pagination keeps listings flowing.
    if "next" in rel or text.strip().lower() in NEXT_WORDS:
        score += 2.5

    # Anchor text quality: image-only links tell us nothing.
    if not text.strip():
        score -= 0.5
    elif 3 <= len(text.strip()) <= 60:
        score += 0.5

    if parsed.query:
        score -= 1.0

    depth_in_path = len([s for s in parsed.path.split("/") if s])

    if depth_in_path > 3:
        score -= 0.3 * (depth_in_path - 3)

    # Novelty: the 20th URL of one shape is worth less than the 1st.
    score -= 0.7 * math.log1p(template_seen)

    return round(score, 3)


@dataclass
class FrontierItem:
    url: str
    depth: int
    parent: str | None = None
    score: float = 0.0
    retries: int = 0
    not_before: float = 0.0
    via: str = "link"
    template: str = ""


class Frontier:
    """
    The crawl queue.

    "best_first": highest score first (ties: shallower, then oldest).
    "bfs": plain breadth-first. Items with a `not_before` time (retries
    waiting out a backoff) sit aside until due.
    """

    def __init__(self, strategy: str = "best_first") -> None:
        if strategy not in ("best_first", "bfs"):
            raise ValueError("strategy must be 'best_first' or 'bfs'.")

        self.strategy = strategy
        self._ready: list = []
        self._delayed: list = []
        self._counter = itertools.count()

    def __len__(self) -> int:
        return len(self._ready) + len(self._delayed)

    def _key(self, item: FrontierItem, seq: int) -> tuple:
        if self.strategy == "bfs":
            return (item.depth, seq)

        return (-item.score, item.depth, seq)

    def push(self, item: FrontierItem) -> None:
        seq = next(self._counter)

        if item.not_before > 0:
            heapq.heappush(
                self._delayed, (item.not_before, seq, item)
            )
        else:
            heapq.heappush(self._ready, (self._key(item, seq), item))

    def pop(self, now: float = 0.0) -> FrontierItem | None:
        while self._delayed and self._delayed[0][0] <= now:
            _, seq, item = heapq.heappop(self._delayed)
            heapq.heappush(self._ready, (self._key(item, seq), item))

        if not self._ready:
            return None

        return heapq.heappop(self._ready)[1]

    def seconds_until_due(self, now: float) -> float | None:
        """None when nothing is waiting on a backoff timer."""

        if self._ready:
            return 0.0

        if not self._delayed:
            return None

        return max(self._delayed[0][0] - now, 0.0)

    def items(self) -> list[FrontierItem]:
        return [entry[1] for entry in self._ready] + [
            entry[2] for entry in self._delayed
        ]
