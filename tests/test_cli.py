"""CLI wiring (no network: crawl/run_pipeline are patched)."""

import io
import json
import sys
from contextlib import redirect_stdout
from unittest.mock import patch

from buitre import cli
from buitre.crawler.crawler import crawl as real_crawl
from buitre.models import FetchResult

from test_crawler_features import START, Site, make


def run_cli(argv, pages):
    site = Site(pages)
    out = io.StringIO()

    with patch.object(sys, "argv", ["buitre", *argv]), \
         patch("buitre.crawler.crawler.run_pipeline", side_effect=site), \
         redirect_stdout(out):
        cli.main()

    return out.getvalue(), site


def test_parser_has_the_new_options():
    args = cli.build_parser().parse_args(
        [START, "--crawl", "--workers", "4", "--delay", "0.5", "--retries", "3",
         "--strategy", "bfs", "--focus", "a", "--focus", "b", "--deep",
         "--documents", "--no-robots", "--store", "c.db", "--resume",
         "--jsonl", "o.jsonl"])

    assert (args.workers, args.delay, args.retries, args.strategy) == (4, 0.5, 3, "bfs")
    assert args.focus == ["a", "b"] and args.deep and args.documents
    assert args.no_robots and args.resume and args.jsonl == "o.jsonl"


def test_crawl_json_output_includes_new_fields_and_options():
    out, site = run_cli(
        [START, "--crawl", "--depth", "0", "--json", "--no-robots", "--quiet",
         "--delay", "0", "--text"],
        {START: make(START, text="Full page text here.")},
    )

    data = json.loads(out)

    assert data["crawl"]["duplicates"] == 0
    assert data["pages"][0]["text"] == "Full page text here."
    assert data["pages"][0]["page_type"] == "generic"


def test_resume_requires_a_store():
    with patch.object(sys, "argv", ["buitre", START, "--crawl", "--resume"]):
        try:
            cli.main()
        except SystemExit as exc:
            assert exc.code == 2
        else:
            raise AssertionError("expected SystemExit")
