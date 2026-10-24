import re
from dataclasses import dataclass, field

from buitre.prioritizer.scorer import normalize_for_comparison


def norm(text: str) -> str:
    return normalize_for_comparison(text)


def prf(expected: set, got: set) -> tuple[float, float, float]:
    """Precision, recall, F1 of two sets. Two empty sets are a perfect match."""

    if not expected and not got:
        return 1.0, 1.0, 1.0

    hits = len(expected & got)
    precision = hits / len(got) if got else 0.0
    recall = hits / len(expected) if expected else 1.0

    if precision + recall == 0:
        return precision, recall, 0.0

    return precision, recall, 2 * precision * recall / (precision + recall)


def _matches(block: str, paragraph: str) -> bool:
    """A block matches a gold paragraph if either contains the other."""

    a, b = norm(block), norm(paragraph)

    return bool(a and b and (a in b or b in a))


def content_scores(
    gold_paragraphs: list[str],
    blocks: list[str],
    junk: list[str],
) -> dict[str, float]:
    """
    precision  share of output blocks that are real content
    recall     share of gold paragraphs that were found
    junk_rate  share of output blocks containing a known junk phrase
    """

    relevant = [b for b in blocks if any(_matches(b, g) for g in gold_paragraphs)]
    found = [g for g in gold_paragraphs if any(_matches(b, g) for b in blocks)]

    if not gold_paragraphs and not blocks:
        precision = recall = 1.0
    else:
        precision = len(relevant) / len(blocks) if blocks else 0.0
        recall = len(found) / len(gold_paragraphs) if gold_paragraphs else 1.0

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    junk_norm = [norm(j) for j in junk]
    junk_blocks = [b for b in blocks if any(j in norm(b) for j in junk_norm)]

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "junk_rate": len(junk_blocks) / len(blocks) if blocks else 0.0,
    }


def digits(value: str) -> str:
    return re.sub(r"\D", "", value)


@dataclass
class PageScore:
    name: str
    content: dict[str, float]
    emails: tuple[float, float, float]
    phones: tuple[float, float, float] | None
    page_type_ok: bool | None
    published_ok: bool | None
    language_ok: bool | None
    notes: list[str] = field(default_factory=list)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0
