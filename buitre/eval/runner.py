import json
from pathlib import Path

from buitre.eval.metrics import (
    PageScore,
    content_scores,
    digits,
    mean,
    prf,
)
from buitre.extractor.main_content import consensus_available
from buitre.pipeline import process_html


CORPUS = Path(__file__).parent / "corpus"


def _phones_supported() -> bool:
    try:
        import phonenumbers

        return bool(getattr(phonenumbers, "SUPPORTED_REGIONS", None))

    except ImportError:
        return False


def baseline_blocks(html: str, url: str) -> list[str] | None:
    """Paragraphs from trafilatura, the reference extractor (optional)."""

    if not consensus_available():
        return None

    import trafilatura

    text = trafilatura.extract(html, url=url, include_comments=False) or ""

    return [line.strip() for line in text.splitlines() if line.strip()]


def evaluate_page(html_path: Path, gold: dict, content_limit: int = 20) -> PageScore:
    html = html_path.read_text(encoding="utf-8")
    url = gold.get("url", f"https://example.com/{html_path.stem}")

    result = process_html(
        html,
        url,
        content_limit=content_limit,
        headers=gold.get("headers"),
    )

    junk = gold.get("must_not_contain", [])

    content = content_scores(
        gold.get("main_paragraphs", []),
        result.extracted.important_content,
        junk,
    )

    expected_emails = {e.lower() for e in gold.get("emails", [])}
    got_emails = {e.lower() for e in result.extracted.emails}
    emails = prf(expected_emails, got_emails)

    phones = None

    if "phones" in gold and _phones_supported():
        phones = prf(
            {digits(p) for p in gold["phones"]},
            {digits(p) for p in result.extracted.phone_numbers},
        )

    page_type_ok = (
        result.page.page_type == gold["page_type"]
        if "page_type" in gold
        else None
    )

    published_ok = (
        (result.page.published_at or "").startswith(gold["published_at"])
        if "published_at" in gold
        else None
    )

    language_ok = (
        (result.page.language or "").lower().startswith(gold["language"])
        if "language" in gold
        else None
    )

    notes = []

    if content["recall"] < 1.0:
        missing = [
            g for g in gold.get("main_paragraphs", [])
            if not any(
                g[:40].lower() in b.lower()
                for b in result.extracted.important_content
            )
        ]
        notes.append(f"missing: {[m[:50] for m in missing]}")

    if content["junk_rate"] > 0:
        notes.append("junk reached important_content")

    return PageScore(
        name=html_path.stem,
        content=content,
        emails=emails,
        phones=phones,
        page_type_ok=page_type_ok,
        published_ok=published_ok,
        language_ok=language_ok,
        notes=notes,
    )


def evaluate_corpus(corpus: Path = CORPUS) -> list[PageScore]:
    scores = []

    for html_path in sorted(corpus.glob("*.html")):
        gold_path = html_path.with_suffix(".json")

        if not gold_path.exists():
            continue

        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        scores.append(evaluate_page(html_path, gold))

    return scores


def baseline_report(corpus: Path = CORPUS) -> dict | None:
    """Same content metrics for trafilatura, when installed."""

    if not consensus_available():
        return None

    rows = []

    for html_path in sorted(corpus.glob("*.html")):
        gold_path = html_path.with_suffix(".json")

        if not gold_path.exists():
            continue

        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        url = gold.get("url", f"https://example.com/{html_path.stem}")
        blocks = baseline_blocks(html_path.read_text(encoding="utf-8"), url) or []

        rows.append(
            content_scores(
                gold.get("main_paragraphs", []),
                blocks,
                gold.get("must_not_contain", []),
            )
        )

    return {
        key: round(mean([r[key] for r in rows]), 3)
        for key in ("precision", "recall", "f1", "junk_rate")
    }


def summarize(scores: list[PageScore]) -> dict:
    def avg(getter) -> float | None:
        values = [getter(s) for s in scores if getter(s) is not None]

        return round(mean(values), 3) if values else None

    def flag(getter) -> float | None:
        values = [getter(s) for s in scores if getter(s) is not None]

        return round(sum(values) / len(values), 3) if values else None

    return {
        "pages": len(scores),
        "content_precision": avg(lambda s: s.content["precision"]),
        "content_recall": avg(lambda s: s.content["recall"]),
        "content_f1": avg(lambda s: s.content["f1"]),
        "junk_rate": avg(lambda s: s.content["junk_rate"]),
        "email_f1": avg(lambda s: s.emails[2]),
        "phone_f1": avg(lambda s: s.phones[2] if s.phones else None),
        "page_type_accuracy": flag(lambda s: s.page_type_ok),
        "published_accuracy": flag(lambda s: s.published_ok),
        "language_accuracy": flag(lambda s: s.language_ok),
    }


def format_report(scores: list[PageScore], baseline: dict | None = None) -> str:
    lines = [
        f"{'page':<22}{'prec':>6}{'recall':>8}{'f1':>6}{'junk':>6}"
        f"{'email':>7}  notes",
        "-" * 78,
    ]

    for s in scores:
        lines.append(
            f"{s.name:<22}{s.content['precision']:>6.2f}"
            f"{s.content['recall']:>8.2f}{s.content['f1']:>6.2f}"
            f"{s.content['junk_rate']:>6.2f}{s.emails[2]:>7.2f}  "
            + "; ".join(s.notes)
        )

    summary = summarize(scores)
    lines += ["-" * 78, "MEAN"]

    for key, value in summary.items():
        lines.append(f"  {key:<22}{value}")

    if baseline:
        lines += ["", "trafilatura baseline (content only)"]
        lines += [f"  {k:<22}{v}" for k, v in baseline.items()]

    return "\n".join(lines)
