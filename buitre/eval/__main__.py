import argparse
import json
import sys
from pathlib import Path

from buitre.eval.runner import (
    CORPUS,
    baseline_report,
    evaluate_corpus,
    format_report,
    summarize,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="python -m buitre.eval",
        description=(
            "Evaluate extraction quality on saved pages. Each NAME.html "
            "needs a NAME.json gold file next to it."
        ),
    )

    parser.add_argument("corpus", nargs="?", default=str(CORPUS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--min-f1", type=float, default=None,
                        help="Exit 1 if mean content F1 is below this")
    parser.add_argument("--max-junk", type=float, default=None,
                        help="Exit 1 if mean junk rate is above this")

    args = parser.parse_args()

    scores = evaluate_corpus(Path(args.corpus))

    if not scores:
        print("No pages with gold files found.", file=sys.stderr)
        return 2

    summary = summarize(scores)
    baseline = baseline_report(Path(args.corpus))

    if args.json:
        print(json.dumps({"summary": summary, "baseline": baseline}, indent=2))
    else:
        print(format_report(scores, baseline))

    failed = False

    if args.min_f1 is not None and summary["content_f1"] < args.min_f1:
        print(f"FAIL: content F1 {summary['content_f1']} < {args.min_f1}",
              file=sys.stderr)
        failed = True

    if args.max_junk is not None and summary["junk_rate"] > args.max_junk:
        print(f"FAIL: junk rate {summary['junk_rate']} > {args.max_junk}",
              file=sys.stderr)
        failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
