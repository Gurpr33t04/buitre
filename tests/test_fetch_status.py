"""Fetch classification and pipeline status handling."""

from unittest.mock import patch

from buitre.crawler.crawler import crawl
from buitre.errors import FetchStatusError
from buitre.fetcher import status
from buitre.models import FetchResult
from buitre.pipeline import run_pipeline


URL = "https://example.com/"

GOOD_HTML = (
    "<html lang='en'><head><title>Good</title></head><body>"
    "<h1>Welcome to the department</h1>"
    + "".join(
        f"<p>Paragraph {i} describing the programmes, faculty and the "
        "admissions process in enough detail to be useful content.</p>"
        for i in range(6)
    )
    + "".join(f"<a href='/p{i}'>page {i}</a> " for i in range(5))
    + "</body></html>"
)

# An application shell: almost no text, so browser rendering is suggested.
SHELL_HTML = "<html><body><div id='root'></div></body></html>"


def fetch(body="", status_code=200, method="http",
          content_type="text/html; charset=utf-8", headers=None):
    return FetchResult(
        requested_url=URL,
        final_url=URL,
        status_code=status_code,
        content_type=content_type,
        headers=headers or {},
        body=body,
        fetch_method=method,
    )


def raises_status(callable_):
    try:
        callable_()
    except FetchStatusError as exc:
        return exc

    raise AssertionError("FetchStatusError was not raised")


# ---- classify_fetch -----------------------------------------------------

def test_classify_ok():
    assert status.classify_fetch(fetch(GOOD_HTML)).ok


def test_classify_error_statuses():
    assert status.classify_fetch(fetch(status_code=404)).verdict == status.CLIENT_ERROR
    assert status.classify_fetch(fetch(status_code=410)).verdict == status.CLIENT_ERROR
    assert status.classify_fetch(fetch(status_code=500)).verdict == status.SERVER_ERROR
    assert status.classify_fetch(fetch(status_code=503)).verdict == status.SERVER_ERROR


def test_classify_rate_limit_keeps_retry_after():
    verdict = status.classify_fetch(
        fetch(status_code=429, headers={"Retry-After": "30"})
    )

    assert verdict.verdict == status.RATE_LIMITED
    assert verdict.retry_after == "30"


def test_classify_challenge_page():
    body = "<html><title>Just a moment...</title></html>"

    assert status.classify_fetch(fetch(body, 403)).verdict == status.BLOCKED
    assert status.classify_fetch(fetch(body, 503)).verdict == status.BLOCKED


def test_long_article_mentioning_captcha_is_not_a_challenge():
    article = GOOD_HTML + "<p>Some sites ask you to verify you are human.</p>" * 200

    assert len(article) > status.CHALLENGE_MAX_BODY
    assert status.classify_fetch(fetch(article)).ok


def test_classify_binary_content_type():
    verdict = status.classify_fetch(fetch("", content_type="application/pdf"))

    assert verdict.verdict == status.NON_TEXT


def test_missing_content_type_is_treated_as_text():
    assert status.classify_fetch(fetch(GOOD_HTML, content_type="")).ok


# ---- pipeline: error pages are failures, not content --------------------

def test_404_page_is_a_failure_and_never_launches_a_browser():
    with patch("buitre.pipeline.fetch_url", return_value=fetch("<p>Not found</p>", 404)), \
         patch("buitre.pipeline.fetch_with_browser") as browser:
        exc = raises_status(lambda: run_pipeline(URL))

    assert exc.status_code == 404
    browser.assert_not_called()


def test_429_failure_carries_retry_after():
    response = fetch("", 429, headers={"retry-after": "12"})

    with patch("buitre.pipeline.fetch_url", return_value=response):
        exc = raises_status(lambda: run_pipeline(URL))

    assert exc.status_code == 429
    assert exc.retry_after == "12"


def test_pdf_payload_is_a_failure():
    response = fetch("", content_type="application/pdf")

    with patch("buitre.pipeline.fetch_url", return_value=response):
        exc = raises_status(lambda: run_pipeline(URL))

    assert exc.verdict == status.NON_TEXT


# ---- pipeline: challenge pages get exactly one browser attempt ---------

def test_challenge_page_is_retried_in_browser():
    challenge = fetch("<html><title>Just a moment...</title></html>", 403)
    rendered = fetch(GOOD_HTML, method="browser")

    with patch("buitre.pipeline.fetch_url", return_value=challenge), \
         patch("buitre.pipeline.fetch_with_browser", return_value=rendered):
        result = run_pipeline(URL)

    assert result.fetch.fetch_method == "browser"
    assert any("bot challenge" in w for w in result.warnings)


def test_challenge_page_that_stays_blocked_fails():
    challenge = fetch("<html><title>Just a moment...</title></html>", 403)

    with patch("buitre.pipeline.fetch_url", return_value=challenge), \
         patch("buitre.pipeline.fetch_with_browser", return_value=challenge):
        exc = raises_status(lambda: run_pipeline(URL))

    assert exc.verdict == status.BLOCKED


# ---- pipeline: browser fallback can no longer destroy a good fetch -----

def test_good_http_page_never_touches_the_browser():
    with patch("buitre.pipeline.fetch_url", return_value=fetch(GOOD_HTML)), \
         patch("buitre.pipeline.fetch_with_browser") as browser:
        result = run_pipeline(URL)

    browser.assert_not_called()
    assert result.fetch.fetch_method == "http"
    assert result.warnings == []


def test_browser_failure_keeps_the_http_result():
    # Previously this raised and the whole page was lost.
    http_page = fetch(SHELL_HTML)

    with patch("buitre.pipeline.fetch_url", return_value=http_page), \
         patch("buitre.pipeline.fetch_with_browser",
               side_effect=RuntimeError("Browser request timed out")):
        result = run_pipeline(URL)

    assert result.fetch.fetch_method == "http"
    assert any("Browser fallback failed" in w for w in result.warnings)


def test_richer_browser_render_replaces_the_shell():
    rendered = fetch(GOOD_HTML, method="browser")

    with patch("buitre.pipeline.fetch_url", return_value=fetch(SHELL_HTML)), \
         patch("buitre.pipeline.fetch_with_browser", return_value=rendered):
        result = run_pipeline(URL)

    assert result.fetch.fetch_method == "browser"
    assert any(w.startswith("Browser fallback used") for w in result.warnings)
    assert result.extracted.important_content


def test_emptier_browser_render_is_ignored():
    # An application shell that shows a "Loading" placeholder, so the
    # browser IS tried; its render comes back emptier, so HTTP wins.
    http_page = fetch(
        "<html><body><div id='root'>Loading</div>"
        "<script src='/a.js'></script><script src='/b.js'></script>"
        "<script src='/c.js'></script></body></html>"
    )
    empty_render = fetch("<html><body></body></html>", method="browser")

    with patch("buitre.pipeline.fetch_url", return_value=http_page), \
         patch("buitre.pipeline.fetch_with_browser",
               return_value=empty_render) as browser:
        result = run_pipeline(URL)

    browser.assert_called_once()
    assert result.fetch.fetch_method == "http"
    assert "Loading" in result.page.text
    assert not any(w.startswith("Browser fallback used") for w in result.warnings)


def test_failed_browser_status_keeps_the_http_result():
    http_page = fetch(SHELL_HTML)
    bad_render = fetch("<p>Forbidden</p>", 403, method="browser")

    with patch("buitre.pipeline.fetch_url", return_value=http_page), \
         patch("buitre.pipeline.fetch_with_browser", return_value=bad_render):
        result = run_pipeline(URL)

    assert result.fetch.fetch_method == "http"


# ---- crawler: failures record their HTTP status ------------------------

def test_crawler_records_status_code_of_failed_pages():
    def fake_pipeline(url, content_limit=10):
        raise FetchStatusError("HTTP 404: the page is not available.",
                               status_code=404, verdict=status.CLIENT_ERROR)

    with patch("buitre.crawler.crawler.run_pipeline", side_effect=fake_pipeline):
        result = crawl(URL, max_depth=0)

    assert result.pages_scanned == 0
    assert result.page_metadata[0].success is False
    assert result.page_metadata[0].status_code == 404
