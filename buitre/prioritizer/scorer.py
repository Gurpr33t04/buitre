import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from buitre.cleaner.boilerplate import BoilerplateLearner
from buitre.cleaner.text import Block, _word_count


BOILERPLATE_PHRASES = [
    "all rights reserved",
    "privacy policy",
    "terms of service",
    "terms of use",
    "cookie policy",
    "subscribe to our newsletter",
    "sign up",
    "follow us",
    "back to top",
    "fallback because interactive scripts did not run",
    "possible causes include disabled javascript",
    "failure to load scripts or stylesheets",
]

_SENTENCE_RE = re.compile(
    r"[.!?\u3002\uff01\uff1f\u0964\u0965\u061f](?:\s|$)"
    r"|[\u3002\uff01\uff1f\u0964\u0965\u061f]"
)

# Generic "this carries facts" signals. Deliberately topic-free: no
# vocabulary that favours one kind of website.
_FACT_PATTERNS = [
    re.compile(r"\b(?:19|20)\d{2}\b"),          # a year
    re.compile(r"\b\d+(?:[.,]\d+)?\s?%"),        # a percentage
    re.compile(r"[$€£₹¥]\s?\d|\d\s?(?:USD|EUR|GBP|INR|Rs\.?)\b"),
    re.compile(r"\bhttps?://"),
    re.compile(r"\b\d{1,3}(?:,\d{3})+\b"),      # 12,500
]

_UI_ENDING_RE = re.compile(
    r"\b(?:learn|read|view|click|visit|get)\s+(?:more|started)\b",
    re.IGNORECASE,
)


def normalize_for_comparison(text: str) -> str:
    """Normalize text for duplicate comparison."""

    text = text.casefold()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)

    return text.strip()


def similarity(first: str, second: str) -> float:
    return SequenceMatcher(
        None,
        normalize_for_comparison(first),
        normalize_for_comparison(second),
    ).ratio()


def is_duplicate(
    text: str,
    selected: list[str],
    threshold: float = 0.88,
) -> bool:
    """Detect exact and near-duplicate content."""

    normalized = normalize_for_comparison(text)

    for existing in selected:
        other = normalize_for_comparison(existing)

        if normalized == other:
            return True

        matcher = SequenceMatcher(None, normalized, other)

        # Cheap upper bounds first: SequenceMatcher.ratio() is O(n^2).
        if (
            matcher.real_quick_ratio() >= threshold
            and matcher.quick_ratio() >= threshold
            and matcher.ratio() >= threshold
        ):
            return True

    return False


def calculate_score(
    text: str,
    *,
    in_main: bool = False,
    link_ratio: float = 0.0,
    consensus: bool = False,
) -> float:
    """
    Score a block by how much it looks like real content.

    Uses only structure and text statistics (length, sentences, lexical
    diversity, letter ratio, link density, position inside <main> or
    <article>), never topic words, so it behaves the same on a hospital,
    a shop and a research blog. `consensus` marks agreement with an
    independent extractor (trafilatura) and is a strong positive signal.
    """

    words = _word_count(text)

    if words == 0:
        return 0.0

    score = 0.0

    # Preferred length: a paragraph, not a label and not a wall of text.
    if 12 <= words <= 70:
        score += 3.0
    elif 8 <= words <= 100:
        score += 2.0
    elif words > 120:
        score -= 2.5

    # Sentence quality.
    sentences = len(_SENTENCE_RE.findall(text))

    if sentences >= 1:
        score += 1.5

    if sentences >= 2:
        score += 0.5

    # Lexical diversity: link lists and repeated labels are repetitive.
    tokens = text.casefold().split()

    if len(tokens) >= 12:
        diversity = len(set(tokens)) / len(tokens)

        if diversity >= 0.6:
            score += 1.0
        elif diversity < 0.35:
            score -= 1.5

    # Mostly digits/symbols (tables of codes, ids) is rarely prose.
    letters = sum(char.isalpha() for char in text)

    if letters / max(len(text), 1) < 0.5:
        score -= 2.0

    # SHOUTING.
    cased = [c for c in text if c.isalpha() and c.casefold() != c.upper()]

    if len(cased) > 20 and sum(c.isupper() for c in cased) / len(cased) > 0.6:
        score -= 1.0

    # Fact-like signals (topic-free).
    for pattern in _FACT_PATTERNS:
        if pattern.search(text):
            score += 0.4

    # Known chrome phrases.
    lowered = text.casefold()

    for phrase in BOILERPLATE_PHRASES:
        if phrase in lowered:
            score -= 5.0

    if _UI_ENDING_RE.search(lowered):
        score -= 1.0

    # Structure.
    if in_main:
        score += 2.5

    score -= 4.0 * link_ratio

    if consensus:
        score += 3.0

    return score


@dataclass
class ScoredBlock:
    score: float
    block: Block


def _as_block(item: "Block | str", index: int) -> Block:
    if isinstance(item, Block):
        return item

    return Block(text=str(item).strip(), index=index)


def _consensus_hit(text: str, consensus_normalized: str) -> bool:
    if not consensus_normalized:
        return False

    normalized = normalize_for_comparison(text)

    if not normalized:
        return False

    if normalized in consensus_normalized:
        return True

    # trafilatura may re-wrap or trim a paragraph: match on its opening.
    return len(normalized) > 60 and normalized[:60] in consensus_normalized


def rank_blocks(
    blocks: Iterable["Block | str"],
    *,
    boilerplate: BoilerplateLearner | None = None,
    consensus_text: str | None = None,
) -> list[ScoredBlock]:
    """
    Score blocks and return them best-first. Blocks the site template
    repeats on many pages (learned by `boilerplate`) are removed.
    """

    consensus_normalized = (
        normalize_for_comparison(consensus_text) if consensus_text else ""
    )

    ranked: list[ScoredBlock] = []

    for index, item in enumerate(blocks):
        block = _as_block(item, index)

        if not block.text.strip():
            continue

        if boilerplate is not None and boilerplate.is_boilerplate(block.text):
            continue

        ranked.append(
            ScoredBlock(
                score=calculate_score(
                    block.text,
                    in_main=block.in_main,
                    link_ratio=block.link_ratio,
                    consensus=_consensus_hit(
                        block.text, consensus_normalized
                    ),
                ),
                block=block,
            )
        )

    ranked.sort(key=lambda item: (-item.score, item.block.index))

    return ranked


def prioritize_blocks(
    blocks: Iterable["Block | str"],
    limit: int = 10,
    *,
    boilerplate: BoilerplateLearner | None = None,
    consensus_text: str | None = None,
) -> list[str]:
    """
    Pick the `limit` most informative blocks, drop near-duplicates, and
    return them in the page's reading order (not score order).
    """

    selected: list[ScoredBlock] = []
    texts: list[str] = []

    for item in rank_blocks(
        blocks,
        boilerplate=boilerplate,
        consensus_text=consensus_text,
    ):
        if is_duplicate(item.block.text, texts):
            continue

        selected.append(item)
        texts.append(item.block.text)

        if len(selected) >= limit:
            break

    selected.sort(key=lambda item: item.block.index)

    return [item.block.text for item in selected]


def candidate_records(
    blocks: Iterable["Block | str"],
    limit: int,
    *,
    consensus_text: str | None = None,
    boilerplate: BoilerplateLearner | None = None,
) -> list[dict]:
    """
    The best `limit` blocks with the fields needed to re-select later.

    Kept on the result so the crawler can, once it has seen the whole
    site, drop template blocks that were still unrecognised on early
    pages.
    """

    return [
        {
            "text": item.block.text,
            "index": item.block.index,
            "in_main": item.block.in_main,
            "link_ratio": item.block.link_ratio,
            "consensus": bool(consensus_text)
            and _consensus_hit(
                item.block.text, normalize_for_comparison(consensus_text)
            ),
        }
        for item in rank_blocks(
            blocks,
            boilerplate=boilerplate,
            consensus_text=consensus_text,
        )[:limit]
    ]


def reselect(
    candidates: list[dict],
    limit: int,
    boilerplate: BoilerplateLearner | None = None,
) -> list[str]:
    """Re-run selection over stored candidates with a better learner."""

    blocks = [
        Block(
            text=c["text"],
            index=c["index"],
            in_main=c["in_main"],
            link_ratio=c["link_ratio"],
        )
        for c in candidates
    ]

    selected: list[ScoredBlock] = []
    texts: list[str] = []

    consensus_flags = {c["text"]: c.get("consensus", False) for c in candidates}

    ranked = []

    for item in blocks:
        if boilerplate is not None and boilerplate.is_boilerplate(item.text):
            continue

        ranked.append(
            ScoredBlock(
                score=calculate_score(
                    item.text,
                    in_main=item.in_main,
                    link_ratio=item.link_ratio,
                    consensus=consensus_flags.get(item.text, False),
                ),
                block=item,
            )
        )

    ranked.sort(key=lambda s: (-s.score, s.block.index))

    for item in ranked:
        if is_duplicate(item.block.text, texts):
            continue

        selected.append(item)
        texts.append(item.block.text)

        if len(selected) >= limit:
            break

    selected.sort(key=lambda s: s.block.index)

    return [s.block.text for s in selected]
