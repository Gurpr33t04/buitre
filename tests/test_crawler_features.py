"""Crawler features: best-first, caps, robots, retries, workers, dedupe,
resume, JSONL, deep forms, sitemap seeding and boilerplate refinement."""

import json
import os
import tempfile
import threading
from unittest.mock import patch

from buitre.cleaner.boilerplate import BoilerplateLearner
from buitre.context import PipelineContext
from buitre.crawler.crawler import crawl
from buitre.crawler.robots import RobotsCache
from buitre.crawler.store import CrawlStore
from buitre.errors import FetchStatusError
from buitre.models import (
    ExtractedData,
    FetchResult,
    LinkInfo,
    PageData,
    ScrapeResult,
    Statistics,
)
from buitre.target.analyzer import analyze_target


START = "https://example.com/"


def make(url, links=None, *, text="", page_type="generic", forms=None,
         quality=0.5, candidates=None, final_url=None, important=None,
         body="<html>raw</html>"):
    details = [
        l if isinstance(l, LinkInfo) else LinkInfo(url=l) for l in (links or [])
    ]

    return ScrapeResult(
        target=analyze_target(url),
        fetch=FetchResult(requested_url=url, final_url=final_url or url,
                          status_code=200, fetch_method="test", body=body),
        page=PageData(title="T " + url, text=text, links=[l.url for l in details],
                      link_details=details, page_type=page_type,
                      forms=forms or []),
        extracted=ExtractedData(important_content=important or []),
        statistics=Statistics(extraction_score=quality),
        candidates=candidates or [],
    )


class Site:
    """A fake pipeline over a dict of url -> result|Exception, with a log."""

    def __init__(self, pages):
        self.pages, self.calls = pages, []
        self.lock = threading.Lock()

    def __call__(self, url, content_limit=10, context=None):
        with self.lock:
            self.calls.append(url)

        page = self.pages[url]

        if isinstance(page, Exception):
            raise page

        if callable(page):
            return page()

        return page


def run(pages, **kw):
    site = Site(pages)
    kw.setdefault("max_depth", 1)

    with patch("buitre.crawler.crawler.run_pipeline", side_effect=site):
        result = crawl(START, **kw)

    return result, site


def urls(result):
    return [m.url for m in result.page_metadata]


# ---- best-first ordering -----------------------------------------------

def test_best_first_visits_promising_links_before_boilerplate_links():
    links = [
        LinkInfo(url="https://example.com/login", text="Log in", region="footer"),
        LinkInfo(url="https://example.com/privacy", text="Privacy", region="footer"),
        LinkInfo(url="https://example.com/news/launch", text="News", region="main"),
        LinkInfo(url="https://example.com/about", text="About us", region="main"),
    ]
    pages = {START: make(START, links)}
    pages.update({l.url: make(l.url) for l in links})

    result, site = run(pages, max_pages=3)

    assert site.calls[0] == START
    assert set(site.calls[1:3]) == {"https://example.com/news/launch",
                                     "https://example.com/about"}


def test_bfs_strategy_keeps_discovery_order():
    links = ["https://example.com/z", "https://example.com/a"]
    pages = {START: make(START, links), links[0]: make(links[0]), links[1]: make(links[1])}

    result, site = run(pages, strategy="bfs")

    assert site.calls == [START] + links


def test_focus_keywords_raise_priority():
    links = [LinkInfo(url="https://example.com/misc", text="Misc"),
             LinkInfo(url="https://example.com/scholarship-forms", text="Forms")]
    pages = {START: make(START, links)}
    pages.update({l.url: make(l.url) for l in links})

    result, site = run(pages, max_pages=2, focus=["scholarship"])

    assert site.calls[1] == "https://example.com/scholarship-forms"


def test_nofollow_links_are_skipped_by_default():
    links = [LinkInfo(url="https://example.com/a", rel=["nofollow"]),
             LinkInfo(url="https://example.com/b")]
    pages = {START: make(START, links), links[1].url: make(links[1].url)}

    result, site = run(pages)

    assert "https://example.com/a" not in site.calls
    assert result.skipped["nofollow"] == 1


# ---- template caps and traps -------------------------------------------

def test_template_cap_stops_facet_floods_but_not_distinct_pages():
    facets = [f"https://example.com/shop?color=c{i}" for i in range(30)]
    normal = ["https://example.com/about", "https://example.com/contact"]
    pages = {START: make(START, facets + normal)}
    pages.update({u: make(u) for u in facets + normal})

    result, site = run(pages, max_pages=50, max_per_query_template=5)

    fetched_facets = [u for u in site.calls if "shop?" in u]

    assert len(fetched_facets) == 5
    assert set(normal) <= set(site.calls)
    assert result.skipped["template_cap"] == 25


def test_path_template_cap_is_separate_and_larger():
    posts = [f"https://example.com/blog/{i}" for i in range(1, 21)]
    pages = {START: make(START, posts)}
    pages.update({u: make(u) for u in posts})

    result, site = run(pages, max_pages=50, max_per_template=8)

    assert len([u for u in site.calls if "/blog/" in u]) == 8


def test_crawler_traps_are_not_followed():
    trap = "https://example.com/a/b/a/b/a/b/c"
    pages = {START: make(START, [trap, "https://example.com/ok"]),
             "https://example.com/ok": make("https://example.com/ok")}

    result, site = run(pages)

    assert trap not in site.calls and result.skipped["trap"] == 1


# ---- robots.txt --------------------------------------------------------

def test_robots_disallowed_urls_are_not_fetched():
    robots = RobotsCache(lambda u: (200, "User-agent: *\nDisallow: /private\n"))
    links = ["https://example.com/private/x", "https://example.com/open"]
    pages = {START: make(START, links), links[1]: make(links[1])}

    result, site = run(pages, robots=robots)

    assert "https://example.com/private/x" not in site.calls
    assert result.skipped["robots"] == 1
    blocked = [m for m in result.page_metadata if m.error == "Blocked by robots.txt."]
    assert len(blocked) == 1 and blocked[0].success is False


def test_robots_can_be_disabled():
    robots = RobotsCache(lambda u: (200, "User-agent: *\nDisallow: /\n"))
    pages = {START: make(START)}

    result, site = run(pages, robots=robots, respect_robots=False)

    assert site.calls == [START]


def test_robots_crawl_delay_is_honoured_via_the_throttle():
    now, slept = [0.0], []
    robots = RobotsCache(lambda u: (200, "User-agent: *\nCrawl-delay: 3\n"))
    links = ["https://example.com/a", "https://example.com/b"]
    pages = {START: make(START, links), links[0]: make(links[0]), links[1]: make(links[1])}

    def sleep(seconds):
        slept.append(round(seconds, 2))
        now[0] += seconds

    run(pages, robots=robots, clock=lambda: now[0], sleep=sleep)

    assert slept == [3.0, 3.0]


def test_sitemap_urls_seed_the_frontier():
    sitemap = ("<urlset><url><loc>https://example.com/hidden-page</loc></url>"
               "<url><loc>https://other.org/offsite</loc></url></urlset>")
    robots = RobotsCache(
        lambda u: (200, sitemap) if u.endswith("sitemap.xml") else (404, "")
    )
    pages = {START: make(START), "https://example.com/hidden-page": make("https://example.com/hidden-page")}

    result, site = run(pages, robots=robots)

    assert "https://example.com/hidden-page" in site.calls       # not linked anywhere
    assert "https://other.org/offsite" not in site.calls          # off-host ignored


def test_sitemap_is_not_fetched_for_a_depth_zero_crawl():
    fetched = []
    robots = RobotsCache(lambda u: fetched.append(u) or (404, ""))

    run({START: make(START)}, robots=robots, max_depth=0)

    assert not any("sitemap" in u for u in fetched)


# ---- retries -----------------------------------------------------------

def make_clock():
    now = [1000.0]
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 1))
        now[0] += seconds

    return (lambda: now[0]), sleep, slept


def test_transient_failures_are_retried_with_backoff_then_succeed():
    clock, sleep, slept = make_clock()
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1

        if attempts["n"] < 3:
            raise FetchStatusError("HTTP 503", status_code=503, verdict="server_error")

        return make(START)

    result, _ = run({START: flaky}, max_retries=3, clock=clock, sleep=sleep)

    assert attempts["n"] == 3
    assert result.pages_scanned == 1 and result.retries == 2
    assert result.urls_attempted == 1               # retries are not new URLs
    assert len(slept) == 2 and slept[0] >= 2 and slept[1] >= 4   # exponential


def test_retry_after_header_is_honoured():
    clock, sleep, slept = make_clock()
    calls = {"n": 0}

    def limited():
        calls["n"] += 1

        if calls["n"] == 1:
            raise FetchStatusError("429", status_code=429, verdict="rate_limited",
                                   retry_after="17")

        return make(START)

    run({START: limited}, max_retries=2, clock=clock, sleep=sleep)

    assert slept == [17.0]


def test_permanent_errors_are_not_retried():
    clock, sleep, slept = make_clock()
    err = FetchStatusError("HTTP 404", status_code=404, verdict="client_error")

    result, site = run({START: err}, max_retries=3, clock=clock, sleep=sleep)

    assert site.calls == [START] and slept == []
    assert result.page_metadata[0].status_code == 404


def test_retries_give_up_and_record_the_failure():
    clock, sleep, slept = make_clock()
    err = RuntimeError("Request timed out after 15.0 seconds.")

    result, site = run({START: err}, max_retries=2, clock=clock, sleep=sleep)

    assert len(site.calls) == 3                      # 1 try + 2 retries
    assert result.pages_scanned == 0
    assert result.page_metadata[0].success is False
    assert result.page_metadata[0].retries == 2


def test_no_retries_by_default():
    result, site = run({START: RuntimeError("boom")})

    assert site.calls == [START]


def test_tor_circuit_is_rotated_after_repeated_onion_failures():
    clock, sleep, _ = make_clock()
    onion = "http://abcdefghij234567.onion/"
    rotations = []

    class Ctx(PipelineContext):
        def rotate_tor_circuit(self, min_interval=10.0):
            rotations.append(1)
            return True

    site = Site({onion: RuntimeError("Tor request timed out.")})

    with patch("buitre.crawler.crawler.run_pipeline", side_effect=site):
        crawl(onion, max_depth=0, max_retries=3, clock=clock, sleep=sleep,
              context=Ctx(), respect_robots=False)

    assert len(rotations) >= 1


# ---- workers -----------------------------------------------------------

def test_parallel_workers_fetch_everything_exactly_once():
    links = [f"https://example.com/p{i}" for i in range(12)]
    pages = {START: make(START, links)}
    pages.update({u: make(u) for u in links})

    result, site = run(pages, workers=4, max_pages=50)

    assert sorted(site.calls) == sorted([START] + links)
    assert result.pages_scanned == 13


def test_parallel_workers_respect_max_pages():
    links = [f"https://example.com/p{i}" for i in range(20)]
    pages = {START: make(START, links)}
    pages.update({u: make(u) for u in links})

    result, site = run(pages, workers=5, max_pages=6)

    assert result.pages_scanned == 6 and len(site.calls) == 6


def test_workers_must_be_positive():
    try:
        crawl(START, workers=0)
    except ValueError:
        return

    raise AssertionError("expected ValueError")


# ---- dedupe, page types ------------------------------------------------

import random as _random

_rng = _random.Random(3)
_VOCAB = [f"term{i}" for i in range(700)]
LONG_TEXT = " ".join(_rng.choice(_VOCAB) for _ in range(300))


def test_near_duplicate_pages_are_recorded_but_not_kept():
    links = ["https://example.com/print/1", "https://example.com/mirror"]
    pages = {
        START: make(START, links, text=LONG_TEXT + " original"),
        links[0]: make(links[0], text=LONG_TEXT + " printed"),
        links[1]: make(links[1], text="entirely different words " * 80),
    }

    result, _ = run(pages, max_pages=10)

    dup = [m for m in result.page_metadata if m.duplicate_of]

    assert len(dup) == 1 and dup[0].duplicate_of == START
    assert result.duplicates == 1
    assert result.pages_scanned == 2


def test_dedupe_can_be_disabled():
    link = "https://example.com/copy"
    pages = {START: make(START, [link], text=LONG_TEXT),
             link: make(link, text=LONG_TEXT)}

    result, _ = run(pages, dedupe_content=False)

    assert result.pages_scanned == 2


def test_login_and_error_pages_are_skipped_and_not_expanded():
    login = "https://example.com/secure-login"
    behind = "https://example.com/behind-login"
    pages = {
        START: make(START, [login]),
        login: make(login, [behind], page_type="login"),
    }

    result, site = run(pages, max_depth=3)

    assert behind not in site.calls
    assert result.pages_scanned == 1
    assert result.skipped["login"] == 1
    skipped = [m for m in result.page_metadata if m.page_type == "login"][0]
    assert skipped.success is False and "Skipped login" in skipped.error


def test_skipping_can_be_turned_off():
    login = "https://example.com/login"
    pages = {START: make(START, [login]), login: make(login, page_type="login")}

    result, _ = run(pages, skip_page_types=())

    assert result.pages_scanned == 2


# ---- memory, outputs ---------------------------------------------------

def test_raw_bodies_are_dropped_unless_requested():
    result, _ = run({START: make(START)})
    assert result.pages[0].fetch.body == ""

    result, _ = run({START: make(START)}, keep_bodies=True)
    assert result.pages[0].fetch.body == "<html>raw</html>"


def test_jsonl_streams_one_record_per_page():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "out", "crawl.jsonl")
        link = "https://example.com/a"
        pages = {START: make(START, [link], important=["Hello world content."]),
                 link: make(link)}

        run(pages, jsonl_path=path)

        lines = [json.loads(l) for l in open(path, encoding="utf-8")]

    assert [l["crawl"]["url"] for l in lines] == [START, link]
    assert lines[0]["important_content"] == ["Hello world content."]
    assert lines[1]["crawl"]["parent_url"] == START
    assert "text" not in lines[0]["page"]


# ---- persistence and resume --------------------------------------------

def test_store_refuses_an_existing_crawl_without_resume():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.db")
        run({START: make(START)}, store_path=path)

        try:
            run({START: make(START)}, store_path=path)
        except ValueError as exc:
            assert "resume" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def test_interrupted_crawl_resumes_without_refetching():
    links = [f"https://example.com/p{i}" for i in range(6)]
    pages = {START: make(START, links)}
    pages.update({u: make(u) for u in links})

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.db")

        first, site1 = run(pages, store_path=path, max_pages=3)
        assert first.pages_scanned == 3

        second, site2 = run(pages, store_path=path, resume=True, max_pages=50)

        assert not set(site1.calls) & set(site2.calls)     # nothing refetched
        assert set(site1.calls) | set(site2.calls) == {START, *links}
        assert second.resumed_pages == 3
        assert second.pages_scanned == 4

        store = CrawlStore(path)
        assert store.ok_count() == 7
        assert len(list(store.iter_records())) == 7
        assert store.pending() == []
        store.close()


def test_resume_keeps_near_duplicate_memory():
    link = "https://example.com/copy"
    pages = {START: make(START, [link], text=LONG_TEXT), link: make(link, text=LONG_TEXT)}

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "c.db")

        run(pages, store_path=path, max_pages=1)
        second, _ = run(pages, store_path=path, resume=True, max_pages=10)

    assert second.duplicates == 1


# ---- deep mode ---------------------------------------------------------

FORM = {"action": "https://example.com/directory", "method": "get",
        "has_password": False, "has_file": False,
        "fields": [{"name": "dept", "type": "select",
                    "options": ["", "physics", "chemistry"], "selected": ""}]}


def test_deep_mode_enumerates_safe_get_forms():
    urls_ = ["https://example.com/directory?dept=physics",
             "https://example.com/directory?dept=chemistry"]
    pages = {START: make(START, forms=[FORM])}
    pages.update({u: make(u) for u in urls_})

    off, site_off = run(pages)
    on, site_on = run(pages, deep=True)

    assert set(site_off.calls) == {START}
    assert set(urls_) <= set(site_on.calls)


def test_deep_mode_ignores_post_and_password_forms():
    post = dict(FORM, method="post")
    login = dict(FORM, has_password=True)

    result, site = run({START: make(START, forms=[post, login])}, deep=True)

    assert site.calls == [START]


# ---- documents ---------------------------------------------------------

def test_pdf_links_are_followed_only_when_documents_are_enabled():
    pdf = "https://example.com/notice.pdf"
    pages = {START: make(START, [pdf]), pdf: make(pdf, page_type="document")}

    off, site_off = run(pages)
    on, site_on = run(pages, include_documents=True)

    assert pdf not in site_off.calls and pdf in site_on.calls


# ---- boilerplate refinement --------------------------------------------

def test_refine_removes_template_blocks_missed_on_early_pages():
    template = "Open every weekday from nine in the morning until five in the evening."
    unique = ["Admissions open in March for the new academic session of 2026.",
              "The library has extended its reading hours during examinations.",
              "A new laboratory for robotics research was inaugurated by the dean.",
              "Scholarship applications must reach the registrar before June."]

    def cand(text, i):
        return {"text": text, "index": i, "in_main": True, "link_ratio": 0.0}

    links = [f"https://example.com/p{i}" for i in range(1, 4)]
    pages = {START: make(START, links)}

    for i, link in enumerate([START] + links):
        pages[link] = make(link if link != START else START, links if link == START else [],
                           important=[template, unique[i]],
                           candidates=[cand(template, 0), cand(unique[i], 1)])

    ctx = PipelineContext(http_client_factory=lambda: None,
                          browser_pool_factory=lambda **k: None)

    learner = ctx.boilerplate.for_host("example.com")
    learner.min_pages, learner.threshold, learner.min_count = 3, 0.5, 2

    # The crawl itself fed the learner (as the real pipeline does).
    for page in pages.values():
        learner.observe([template, page.extracted.important_content[1]])

    result, _ = run(pages, context=ctx, max_pages=10)

    for page in result.pages:
        assert template not in page.extracted.important_content
        assert len(page.extracted.important_content) == 1
        assert page.candidates == []

    ctx.close()


def test_refine_can_be_disabled():
    template = "Open every weekday from nine in the morning until five in the evening."
    page = make(START, important=[template, "x"],
                candidates=[{"text": template, "index": 0, "in_main": True, "link_ratio": 0.0}])
    ctx = PipelineContext(http_client_factory=lambda: None, browser_pool_factory=lambda **k: None)
    learner = ctx.boilerplate.for_host("example.com")
    learner.min_pages, learner.threshold, learner.min_count = 1, 0.1, 1
    learner.observe([template])

    result, _ = run({START: page}, context=ctx, refine=False)

    assert template in result.pages[0].extracted.important_content


# ---- context ownership -------------------------------------------------

def test_crawl_closes_a_context_it_created_but_not_one_it_was_given():
    closed = []

    class Tracking(PipelineContext):
        def close(self):
            closed.append(1)
            super().close()

    run({START: make(START)}, context=Tracking())
    assert closed == []

    with patch("buitre.crawler.crawler.PipelineContext", Tracking):
        run({START: make(START)})

    assert closed == [1]
