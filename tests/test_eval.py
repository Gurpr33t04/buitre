"""Extraction-quality regression guard.

Runs Buitre over the saved pages in buitre/eval/corpus and fails if quality
drops. Add your own saved pages (NAME.html + NAME.json) to that folder, or
point `python -m buitre.eval DIR` at a corpus of real pages.
"""

from buitre.eval.metrics import content_scores, prf
from buitre.eval.runner import evaluate_corpus, summarize


def test_corpus_quality_has_not_regressed():
    scores = evaluate_corpus()
    summary = summarize(scores)

    assert summary["pages"] >= 10
    assert summary["content_f1"] >= 0.95, summary
    assert summary["content_recall"] >= 0.95, summary
    assert summary["junk_rate"] <= 0.02, summary
    assert summary["email_f1"] >= 0.95, summary
    assert summary["page_type_accuracy"] == 1.0, summary
    assert summary["published_accuracy"] == 1.0, summary
    assert summary["language_accuracy"] == 1.0, summary


def test_no_single_page_is_badly_extracted():
    for score in evaluate_corpus():
        assert score.content["f1"] >= 0.8, (score.name, score.notes)
        assert score.content["junk_rate"] <= 0.1, (score.name, score.notes)


def test_metrics_behave_sensibly():
    assert prf({"a", "b"}, {"a", "b"}) == (1.0, 1.0, 1.0)
    assert prf(set(), set()) == (1.0, 1.0, 1.0)
    assert prf({"a"}, set())[2] == 0.0
    assert prf({"a", "b"}, {"a", "c"})[:2] == (0.5, 0.5)

    gold = ["The quick brown fox jumps over the lazy dog today."]

    perfect = content_scores(gold, list(gold), [])
    assert perfect["f1"] == 1.0

    noisy = content_scores(gold, gold + ["Subscribe to our newsletter now"], ["subscribe"])
    assert noisy["precision"] == 0.5 and noisy["junk_rate"] == 0.5

    assert content_scores(gold, [], [])["recall"] == 0.0
