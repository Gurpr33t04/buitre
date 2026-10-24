from unittest.mock import patch
from buitre.target.analyzer import analyze_target

import pytest

from buitre.crawler.crawler import crawl
from buitre.models import (
    ExtractedData,
    FetchResult,
    PageData,
    ScrapeResult,
    Statistics,
)


def make_result(requested_url, final_url=None, links=None):
    final_url = final_url or requested_url

    return ScrapeResult(
        target=analyze_target(requested_url),
        fetch=FetchResult(
            requested_url=requested_url,
            final_url=final_url,
            status_code=200,
            fetch_method="test",
        ),
        page=PageData(
            title="Test page",
            links=links or [],
        ),
        extracted=ExtractedData(),
        statistics=Statistics(),
    )


def pipeline_for(pages):
    def fake_pipeline(url, content_limit=10):
        if url not in pages:
            raise AssertionError(f"Unexpected URL fetched: {url}")
        return pages[url]

    return fake_pipeline


def test_crawler_accepts_same_host_links():
    start = "https://example.com/"
    child = "https://example.com/about"

    pages = {
        start: make_result(start, links=["/about"]),
        child: make_result(child),
    }

    with patch(
        "buitre.crawler.crawler.run_pipeline",
        side_effect=pipeline_for(pages),
    ):
        result = crawl(start, max_depth=1)

    assert result.pages_scanned == 2
    assert {p.fetch.final_url for p in result.pages} == {
        start, child
    }


def test_crawler_blocks_subdomains_by_default():
    start = "https://example.com/"
    subdomain = "https://api.example.com/data"

    pages = {
        start: make_result(start, links=[subdomain]),
    }

    with patch(
        "buitre.crawler.crawler.run_pipeline",
        side_effect=pipeline_for(pages),
    ):
        result = crawl(start, max_depth=1)

    assert result.pages_scanned == 1


def test_crawler_accepts_subdomains_when_enabled():
    start = "https://example.com/"
    subdomain = "https://api.example.com/data"

    pages = {
        start: make_result(start, links=[subdomain]),
        subdomain: make_result(subdomain),
    }

    with patch(
        "buitre.crawler.crawler.run_pipeline",
        side_effect=pipeline_for(pages),
    ):
        result = crawl(
            start,
            max_depth=1,
            allow_subdomains=True,
        )

    assert result.pages_scanned == 2


def test_crawler_rejects_external_redirect():
    start = "https://example.com/"
    external = "https://evil.example.net/"

    pages = {
        start: make_result(
            start,
            final_url=external,
        ),
    }

    with patch(
        "buitre.crawler.crawler.run_pipeline",
        side_effect=pipeline_for(pages),
    ):
        result = crawl(start, max_depth=0)

    assert result.pages_scanned == 0
    assert any(
        "outside allowed crawl boundary" in warning
        for warning in result.warnings
    )
    assert any(
        not metadata.success
        for metadata in result.page_metadata
    )


def test_crawler_respects_max_depth():
    start = "https://example.com/"
    child = "https://example.com/child"
    grandchild = "https://example.com/child/grandchild"

    pages = {
        start: make_result(start, links=["/child"]),
        child: make_result(
            child,
            links=["/child/grandchild"],
        ),
        grandchild: make_result(grandchild),
    }

    with patch(
        "buitre.crawler.crawler.run_pipeline",
        side_effect=pipeline_for(pages),
    ):
        result = crawl(start, max_depth=1)

    assert result.pages_scanned == 2
    assert all(
        metadata.depth <= 1
        for metadata in result.page_metadata
    )
