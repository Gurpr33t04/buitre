"""Scoring, render detection, page types, boilerplate learning, quality,
embedded state, documents and the offline pipeline."""

from unittest.mock import patch

from buitre.cleaner.boilerplate import BoilerplateLearner, fingerprint
from buitre.cleaner.text import Block
from buitre.context import HostMemory, PipelineContext
from buitre.detector.pagetype import classify_page
from buitre.detector.rendering import needs_browser_rendering
from buitre.errors import FetchStatusError
from buitre.models import FetchResult, PageData
from buitre.pipeline import process_html, run_pipeline
from buitre.prioritizer.scorer import (
    calculate_score,
    prioritize_blocks,
    reselect,
)

from helpers import make_pdf


URL = "https://example.com/page"


class _Dummy:
    def __init__(self, **kw):
        pass

    def close(self):
        pass


def make_context(**kw):
    """A context whose clients are inert; fetch functions are patched."""

    return PipelineContext(
        http_client_factory=_Dummy, browser_pool_factory=_Dummy, **kw
    )

PARA_A = ("The committee approved the revised admissions policy on Tuesday "
          "after a lengthy discussion with student representatives.")
PARA_B = ("Applications for the 2026 session will open in March and close on "
          "30 June, with results announced in the first week of July.")
PARA_C = ("Hostel accommodation is limited and will be allotted on merit; "
          "candidates should apply for it together with the admission form.")


def page(text="", **kw):
    return PageData(text=text, **kw)


# ---- scorer: structure, not topic keywords -----------------------------

def test_score_has_no_topic_bias():
    tech = ("Our security research community builds software and code "
            "for system development.")
    plain = ("The council approved the new transport plan on Tuesday after "
             "months of public consultation and debate.")

    # Old scorer added +0.4 per tech keyword, so `tech` always beat `plain`.
    assert calculate_score(plain) > calculate_score(tech)


def test_main_region_and_links_move_the_score():
    text = PARA_A

    assert calculate_score(text, in_main=True) > calculate_score(text)
    assert calculate_score(text, link_ratio=0.8) < calculate_score(text)
    assert calculate_score(text, consensus=True) > calculate_score(text)


def test_repetitive_and_numeric_blocks_are_penalised():
    repetitive = "buy now " * 20
    codes = "A1 B2 C3 D4 E5 F6 G7 H8 I9 J10 K11 L12 M13 N14 15 16 17 18"

    assert calculate_score(repetitive) < calculate_score(PARA_A)
    assert calculate_score(codes) < calculate_score(PARA_A)


def test_prioritize_returns_reading_order_not_score_order():
    blocks = [
        Block(text=PARA_A, index=0),
        Block(text="Short label text here", index=1),
        Block(text=PARA_B, index=2, in_main=True),
        Block(text=PARA_C, index=3),
    ]

    result = prioritize_blocks(blocks, limit=3)

    assert result == [PARA_A, PARA_B, PARA_C]


def test_prioritize_still_accepts_plain_strings_and_dedupes():
    result = prioritize_blocks([PARA_A, PARA_A, PARA_B], limit=5)

    assert result == [PARA_A, PARA_B]


def test_consensus_text_boosts_agreeing_blocks():
    weak = "This sentence is neither long nor special but is correct text."
    blocks = [Block(text=weak, index=0), Block(text=PARA_A, index=1, in_main=True)]

    picked = prioritize_blocks(blocks, limit=1, consensus_text=weak)

    assert picked == [weak] or picked == [PARA_A]
    assert calculate_score(weak, consensus=True) > calculate_score(weak)


# ---- boilerplate learning ---------------------------------------------

def test_fingerprint_ignores_case_digits_and_punctuation():
    assert fingerprint("Copyright 2025 Acme Corp. All rights reserved") == \
        fingerprint("copyright 2026  ACME corp - all rights reserved!")
    assert fingerprint("Call us on 0161 496 0000 today") == \
        fingerprint("call us on 0171 123 9999 today.")


def test_learner_flags_template_blocks_only_after_enough_pages():
    learner = BoilerplateLearner(min_pages=4, threshold=0.4, min_count=3)
    template = "Visit our campus every weekday between nine and five o'clock."

    uniques = [
        "Unique article about astronomy and distant galaxies today.",
        "Another piece concerning medieval pottery and kiln design.",
        "A third text discussing coastal erosion near the harbour.",
    ]

    for unique in uniques:
        learner.observe([template, unique])
        assert not learner.is_boilerplate(template)   # not enough pages yet

    learner.observe([template, "Another unique page entirely different."])

    assert learner.is_boilerplate(template)
    assert not learner.is_boilerplate(uniques[1])


def test_prioritize_drops_learned_boilerplate_in_any_language():
    learner = BoilerplateLearner(min_pages=3, threshold=0.5, min_count=2)
    chrome = "ਸਾਡੇ ਨਾਲ ਸੰਪਰਕ ਕਰੋ ਹਰ ਰੋਜ਼ ਸਵੇਰੇ ਨੌਂ ਵਜੇ ਤੋਂ ਸ਼ਾਮ ਪੰਜ ਵਜੇ ਤੱਕ ਦਫ਼ਤਰ ਖੁੱਲ੍ਹਾ ਹੈ।"

    for _ in range(3):
        learner.observe([chrome, PARA_A])

    result = prioritize_blocks([chrome, PARA_B], limit=5, boilerplate=learner)

    assert chrome not in result and PARA_B in result


def test_reselect_applies_a_better_learner_to_stored_candidates():
    learner = BoilerplateLearner(min_pages=2, threshold=0.5, min_count=2)
    learner.observe([PARA_C])
    learner.observe([PARA_C])

    candidates = [
        {"text": PARA_A, "index": 0, "in_main": True, "link_ratio": 0.0},
        {"text": PARA_C, "index": 1, "in_main": True, "link_ratio": 0.0},
    ]

    assert reselect(candidates, 5, learner) == [PARA_A]
    assert reselect(candidates, 5, None) == [PARA_A, PARA_C]


# ---- render detection --------------------------------------------------

def test_js_bundle_text_no_longer_triggers_the_browser():
    # "loading..." appears in the JS, not on screen. Old code: browser.
    html = (
        "<html><body><h1>Prospectus</h1>" + "<p>" + PARA_A + "</p>" * 1
        + "<p>" + PARA_B + "</p><script>var s='loading...';"
        "var t='please wait';</script></body></html>"
    )
    text = PARA_A + " " + PARA_B

    needs, _ = needs_browser_rendering(page(text, headings=["Prospectus"]), html)

    assert needs is False


def test_short_static_page_is_complete_not_suspicious():
    html = "<html><body><p>Office closed on Monday for maintenance work.</p></body></html>"

    needs, reason = needs_browser_rendering(
        page("Office closed on Monday for maintenance work."), html
    )

    assert needs is False and "Short page" in reason


def test_empty_application_root_needs_the_browser():
    html = "<html><body><div id='root'></div><script src='/a.js'></script></body></html>"

    needs, reason = needs_browser_rendering(page(""), html)

    assert needs is True


def test_noscript_notice_with_thin_text_needs_the_browser():
    html = ("<html><body><noscript>You need to enable JavaScript to run "
            "this app.</noscript><p>" + "x" * 5 + "</p></body></html>")

    needs, _ = needs_browser_rendering(page("x" * 100), html)

    assert needs is True


def test_noscript_notice_on_a_rich_page_is_ignored():
    html = "<html><body><noscript>Please enable JavaScript to view comments.</noscript></body></html>"

    needs, _ = needs_browser_rendering(page("word " * 400, headings=["h"]), html)

    assert needs is False


# ---- page types --------------------------------------------------------

def test_page_types():
    long_text = "word " * 300

    assert classify_page("https://e.com/x", page("Not found", title="404 Not Found")) == "error"
    assert classify_page("https://e.com/x", page("", forms=[{"has_password": True}])) == "login"
    assert classify_page("https://e.com/secureptulogin.aspx", page("Welcome")) == "login"
    assert classify_page("https://e.com/a", page(long_text, structured_data=[{"@type": ["NewsArticle"]}])) == "article"
    assert classify_page("https://e.com/a", page(long_text, structured_data=[{"@type": "Product"}])) == "product"
    assert classify_page("https://e.com/contact-us", page("Call us")) == "contact"
    assert classify_page("https://e.com/privacy-policy", page(long_text)) == "policy"
    assert classify_page("https://e.com/", page(long_text)) == "home"
    assert classify_page("https://e.com/f.pdf", page(""), "application/pdf") == "document"


def test_a_long_article_about_errors_is_not_an_error_page():
    text = "word " * 400

    assert classify_page("https://e.com/a", page(text, title="Error handling in Python")) != "error"


# ---- offline pipeline: structure, metadata, contacts, quality ----------

ARTICLE_HTML = f"""<html lang="en-IN"><head><title>Admissions 2026</title>
<meta property="og:type" content="article">
<meta name="description" content="Admission details">
<script type="application/ld+json">{{"@type":["NewsArticle"],"datePublished":"2026-03-04T10:00:00+05:30",
"author":[{{"@type":"Person","name":"Ann Lee"}}]}}</script></head>
<body><header><a href="/">Home</a></header><nav><a href="/a">A</a><a href="/b">B</a></nav>
<main><h1>Admissions 2026</h1><p>{PARA_A}</p><p>{PARA_B}</p><p>{PARA_C}</p>
<p>Contact: <a href="mailto:admissions@uni.ac.in">admissions@uni.ac.in</a></p></main>
<aside><p>Follow us on every social network that exists today, please do.</p></aside>
<footer><p>Helpdesk: help@uni.ac.in | All rights reserved</p></footer></body></html>"""


def test_process_html_extracts_structure_metadata_and_contacts():
    result = process_html(ARTICLE_HTML, "https://uni.ac.in/admissions")

    assert result.page.page_type == "article"
    assert result.page.published_at == "2026-03-04T10:00:00+05:30"
    assert result.page.author == "Ann Lee"
    assert result.page.metadata["opengraph"]["type"] == "article"

    assert result.extracted.important_content == [PARA_A, PARA_B, PARA_C] \
        or all(p in result.extracted.important_content for p in (PARA_A, PARA_B, PARA_C))

    sources = {c["value"]: c["source"] for c in result.extracted.contacts}
    assert sources["admissions@uni.ac.in"] == "mailto"
    assert sources["help@uni.ac.in"] == "page"      # found in the footer

    assert result.statistics.extraction_score > 0.6
    assert result.statistics.main_text_length > 200
    assert result.fetch.fetch_method == "local"


def test_nav_footer_aside_text_never_reaches_important_content():
    result = process_html(ARTICLE_HTML, "https://uni.ac.in/admissions")

    joined = " ".join(result.extracted.important_content)

    assert "Follow us" not in joined and "All rights reserved" not in joined


def test_quality_is_low_for_junk_and_has_flags():
    junk = "<html><body><div id='root'></div></body></html>"

    result = process_html(junk, "https://example.com/x")

    assert result.statistics.extraction_score < 0.3
    assert "thin_text" in result.statistics.quality_flags
    assert "No readable text was extracted." in result.warnings


def test_soft_404_is_rejected_even_with_http_200():
    html = "<html><head><title>404 Page Not Found</title></head><body><p>Sorry.</p></body></html>"

    try:
        process_html(html, "https://example.com/missing")
    except FetchStatusError as exc:
        assert exc.verdict == "soft_404"
    else:
        raise AssertionError("soft 404 not detected")


def test_onion_hints_are_recorded():
    html = ("<html><body><p>" + PARA_A + "</p>"
            "<a href='http://abcdefghij234567.onion/x'>mirror</a></body></html>")

    result = process_html(
        html,
        "http://zzzzzzzzzzzzzzzz.onion/",
        headers={"Onion-Location": "http://abcdefghij234567.onion/"},
    )

    assert result.page.metadata["onion_location"].endswith(".onion/")
    assert result.page.metadata["onion_links"] == ["abcdefghij234567.onion"]


# ---- embedded state: content without a browser -------------------------

NEXT_SHELL = (
    "<html><head><title>Post</title></head><body><div id='__next'></div>"
    "<script id='__NEXT_DATA__' type='application/json'>"
    '{"props":{"pageProps":{"post":{"body":"<p>' + PARA_A + '</p><p>' + PARA_B +
    '</p>","slug":"a-really-long-slug-that-is-not-prose-at-all-but-is-long-enough"}}}}'
    "</script></body></html>"
)


def fetched(body, method="http", status=200):
    return FetchResult(requested_url=URL, final_url=URL, status_code=status,
                       content_type="text/html", headers={}, body=body,
                       fetch_method=method)


def test_nextjs_shell_is_read_without_launching_a_browser():
    with patch("buitre.pipeline.fetch_url", return_value=fetched(NEXT_SHELL)), \
         patch("buitre.pipeline.fetch_with_browser") as browser:
        result = run_pipeline(URL)

    browser.assert_not_called()
    assert PARA_A in result.extracted.important_content
    assert "embedded_state_used" in result.statistics.quality_flags
    assert any("embedded application state" in w for w in result.warnings)
    assert "a-really-long-slug" not in " ".join(result.extracted.important_content)


def test_browser_captured_api_json_feeds_a_shell_page():
    shell = "<html><body><div id='root'></div><script src='/a.js'></script></body></html>"
    rendered = FetchResult(
        requested_url=URL, final_url=URL, status_code=200,
        content_type="text/html", headers={}, body=shell,
        fetch_method="browser",
        api_responses=[{"url": "https://example.com/api/post", "status": 200,
                        "text": '{"content": "' + PARA_B + '"}'}],
    )

    with patch("buitre.pipeline.fetch_url", return_value=fetched(shell)), \
         patch("buitre.pipeline.fetch_with_browser", return_value=rendered):
        result = run_pipeline(URL)

    assert PARA_B in result.extracted.important_content
    assert result.fetch.fetch_method == "browser"


# ---- host memory & context ---------------------------------------------

def test_host_memory_learns_url_shapes_that_need_the_browser():
    memory = HostMemory()

    memory.record("https://e.com/app/1", needed_browser=True)
    assert not memory.prefers_browser("https://e.com/app/2")

    memory.record("https://e.com/app/3", needed_browser=True)
    assert memory.prefers_browser("https://e.com/app/99")
    assert not memory.prefers_browser("https://e.com/blog/99")

    memory.record("https://e.com/app/4", needed_browser=False)
    assert not memory.prefers_browser("https://e.com/app/5")


def test_pipeline_skips_the_http_fetch_once_a_shape_needs_the_browser():
    shell = "<html><body><div id='root'></div><script src='/a.js'></script></body></html>"
    rendered = fetched("<html><body>" + "".join(
        f"<p>{p}</p>" for p in (PARA_A, PARA_B, PARA_C)) + "</body></html>", "browser")

    context = make_context()

    with patch("buitre.pipeline.fetch_url", return_value=fetched(shell)) as http, \
         patch("buitre.pipeline.fetch_with_browser", return_value=rendered):
        for i in (1, 2):
            run_pipeline(f"https://example.com/app/{i}", context=context)

        assert http.call_count == 2

        result = run_pipeline("https://example.com/app/3", context=context)

        assert http.call_count == 2          # went straight to the browser
        assert result.fetch.fetch_method == "browser"

    context.close()


def test_context_learns_boilerplate_across_pages():
    template = "Open every weekday from nine in the morning until five in the evening."
    context = PipelineContext()

    def html(body):
        return ("<html><body><p>" + template + "</p><p>" + body
                + "</p></body></html>")

    results = []

    for i, body in enumerate([PARA_A, PARA_B, PARA_C, PARA_A + " Extra."]):
        results.append(process_html(html(body), f"https://example.com/p{i}",
                                    context=context))

    assert template in results[0].extracted.important_content   # not yet learned
    assert template not in results[3].extracted.important_content


def test_context_creates_clients_lazily_and_closes_them():
    created = []

    class Closable:
        def __init__(self, **kw):
            created.append(self)
            self.closed = False

        def close(self):
            self.closed = True

    context = PipelineContext(http_client_factory=Closable,
                              browser_pool_factory=Closable)
    assert created == []

    context.http_client()
    context.browser_pool(tor=False)
    context.http_client()                      # reused, not recreated
    assert len(created) == 2

    context.close()

    assert all(c.closed for c in created)


# ---- documents ---------------------------------------------------------

def pdf_fetch(data):
    return FetchResult(requested_url="https://e.com/notice.pdf",
                       final_url="https://e.com/notice.pdf", status_code=200,
                       content_type="application/pdf", headers={}, body="",
                       fetch_method="http", content_bytes=data)


def test_pdf_is_extracted_when_documents_are_enabled():
    data = make_pdf("Admission notice for session 2026. Contact registrar@uni.ac.in for details.")
    context = make_context(documents=True)

    with patch("buitre.pipeline.fetch_url", return_value=pdf_fetch(data)):
        result = run_pipeline("https://e.com/notice.pdf", context=context)

    assert result.page.page_type == "document"
    assert "Admission notice" in result.page.text
    assert result.extracted.emails == ["registrar@uni.ac.in"]


def test_pdf_is_a_clear_failure_when_documents_are_disabled():
    with patch("buitre.pipeline.fetch_url", return_value=pdf_fetch(make_pdf("x"))):
        try:
            run_pipeline("https://e.com/notice.pdf")
        except FetchStatusError as exc:
            assert exc.verdict == "non_text"
        else:
            raise AssertionError("expected failure")


def test_garbage_pdf_reports_a_document_error():
    with patch("buitre.pipeline.fetch_url", return_value=pdf_fetch(b"%PDF-1.4 broken")):
        try:
            run_pipeline("https://e.com/notice.pdf", context=make_context(documents=True))
        except FetchStatusError as exc:
            assert exc.verdict == "document_error"
        else:
            raise AssertionError("expected failure")


# ---- hostile input -----------------------------------------------------

def test_pathological_nesting_is_rejected_fast_not_parsed():
    import time

    html = "<span>" * 20000 + "x" + "</span>" * 20000
    started = time.perf_counter()

    try:
        process_html(html, "https://example.com/x")
    except FetchStatusError as exc:
        assert exc.verdict == "malformed"
    else:
        raise AssertionError("expected rejection")

    assert time.perf_counter() - started < 2.0


def test_sloppy_legacy_html_is_not_mistaken_for_deep_nesting():
    from buitre.parser.guard import max_nesting

    # Thousands of unclosed <p>/<li>/<td> are legal and common in old pages.
    sloppy = "<table>" + "<tr><td>cell<td>cell" * 3000 + "</table>" + "<p>one<p>two" * 3000
    normal = "<div>" * 40 + "x" + "</div>" * 40

    assert max_nesting(sloppy) < 20
    assert max_nesting(normal) == 40
    assert max_nesting("<br><br/><img src=x><p>unclosed") == 0
