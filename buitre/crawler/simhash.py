import hashlib
import re
from collections import defaultdict

from buitre.cleaner.text import _CJK_RE


_WORD_RE = re.compile(r"\w+", re.UNICODE)
BITS = 64


def _tokens(text: str) -> list[str]:
    text = text.casefold()
    tokens: list[str] = []

    for word in _WORD_RE.findall(text):
        if _CJK_RE.search(word):
            # No spaces in CJK: use character bigrams.
            tokens.extend(word[i:i + 2] for i in range(max(len(word) - 1, 1)))
        else:
            tokens.append(word)

    # Word 3-shingles make the hash order-sensitive but robust to small edits.
    if len(tokens) >= 3:
        return [" ".join(tokens[i:i + 3]) for i in range(len(tokens) - 2)]

    return tokens


def simhash(text: str) -> int:
    """64-bit SimHash: similar texts give hashes a few bits apart."""

    weights = [0] * BITS

    for token in _tokens(text):
        value = int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(),
            "big",
        )

        for bit in range(BITS):
            weights[bit] += 1 if value >> bit & 1 else -1

    result = 0

    for bit in range(BITS):
        if weights[bit] > 0:
            result |= 1 << bit

    return result


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


class SimIndex:
    """
    Near-duplicate lookup. With 4 bands of 16 bits, two hashes that differ
    in at most 3 bits must agree on at least one whole band (pigeonhole),
    so lookups touch only a handful of candidates instead of every page.
    """

    BANDS = 4

    def __init__(self, max_distance: int = 3) -> None:
        self.max_distance = max_distance
        self._bands: list[dict[int, list[tuple[int, str]]]] = [
            defaultdict(list) for _ in range(self.BANDS)
        ]

    def _keys(self, value: int) -> list[int]:
        return [(value >> (16 * i)) & 0xFFFF for i in range(self.BANDS)]

    def find(self, value: int) -> str | None:
        for band, key in zip(self._bands, self._keys(value)):
            for other, label in band.get(key, ()):
                if hamming(value, other) <= self.max_distance:
                    return label

        return None

    def add(self, value: int, label: str) -> None:
        for band, key in zip(self._bands, self._keys(value)):
            band[key].append((value, label))
