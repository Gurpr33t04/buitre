import hashlib
import re
import threading
from collections import Counter


_DIGITS_RE = re.compile(r"\d+")
_NONWORD_RE = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE_RE = re.compile(r"\s+")


def fingerprint(text: str) -> str:
    """
    Stable id for a block. Case, punctuation, spacing and digits are
    ignored so "© 2025 Acme" and "(c) 2026  Acme." are the same block.
    """

    text = text.casefold()
    text = _DIGITS_RE.sub("0", text)
    text = _NONWORD_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()

    return hashlib.blake2b(text.encode("utf-8"), digest_size=8).hexdigest()


class BoilerplateLearner:
    """
    Learns a website's template from the pages themselves.

    A block that shows up on a large share of a site's pages is part of
    the template (cookie notice, "Subscribe", address strip, sidebar
    blurb), whatever language it is written in. This replaces hand
    maintained phrase lists: no list can cover every site, but every
    site repeats its own chrome on every page.

    A block is boilerplate once at least `min_count` pages have been seen
    containing it AND it appears on at least `threshold` of all pages.
    """

    def __init__(
        self,
        min_pages: int = 4,
        threshold: float = 0.4,
        min_count: int = 3,
    ) -> None:
        self.min_pages = min_pages
        self.threshold = threshold
        self.min_count = min_count

        self.pages = 0
        self._counts: Counter[str] = Counter()
        self._lock = threading.Lock()

    def observe(self, texts: list[str]) -> None:
        """Record the blocks of one page (each counted once per page)."""

        prints = {fingerprint(text) for text in texts}

        with self._lock:
            self.pages += 1
            self._counts.update(prints)

    def is_boilerplate(self, text: str) -> bool:
        with self._lock:
            if self.pages < self.min_pages:
                return False

            count = self._counts.get(fingerprint(text), 0)

            return (
                count >= self.min_count
                and count / self.pages >= self.threshold
            )


class BoilerplateRegistry:
    """One learner per host (templates differ between sites)."""

    def __init__(self, **learner_options: float) -> None:
        self._options = learner_options
        self._learners: dict[str, BoilerplateLearner] = {}
        self._lock = threading.Lock()

    def for_host(self, host: str) -> BoilerplateLearner:
        host = (host or "").lower()

        with self._lock:
            if host not in self._learners:
                self._learners[host] = BoilerplateLearner(**self._options)

            return self._learners[host]
