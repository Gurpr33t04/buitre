"""robots.txt, sitemaps, throttle, URL rules and templates."""

import threading

from buitre.crawler.robots import RobotsCache, RobotsRules
from buitre.crawler.rules import (
    is_allowed_host,
    is_crawlable_url,
    normalize_url,
)
from buitre.crawler.sitemap import discover_sitemap_urls, parse_sitemap
from buitre.crawler.throttle import HostThrottle
from buitre.crawler.urlpatterns import (
    has_query,
    looks_like_trap,
    url_template,
)


# ---- robots.txt --------------------------------------------------------

ROBOTS = """
User-agent: *
Disallow: /private/
Disallow: /*.pdf$
Allow: /private/public-note.html
Crawl-delay: 2

User-agent: Buitre
Disallow: /secret/
Crawl-delay: 5

Sitemap: https://e.com/sitemap.xml
"""


def test_specific_user_agent_group_wins_over_star():
    rules = RobotsRules(ROBOTS)

    assert rules.can_fetch("https://e.com/secret/a", "Buitre") is False
    # The Buitre group has no /private rule, and the * group is ignored.
    assert rules.can_fetch("https://e.com/private/a", "Buitre") is True
    assert rules.crawl_delay("Buitre") == 5.0


def test_star_group_rules_longest_match_and_wildcards():
    rules = RobotsRules(ROBOTS)

    assert rules.can_fetch("https://e.com/private/x", "Other") is False
    assert rules.can_fetch("https://e.com/private/public-note.html", "Other") is True
    assert rules.can_fetch("https://e.com/files/report.pdf", "Other") is False
    assert rules.can_fetch("https://e.com/files/report.pdf?x=1", "Other") is True
    assert rules.can_fetch("https://e.com/open", "Other") is True
    assert rules.crawl_delay("Other") == 2.0
    assert rules.sitemaps == ["https://e.com/sitemap.xml"]


def test_allow_beats_disallow_on_equal_length():
    rules = RobotsRules("User-agent: *\nDisallow: /a\nAllow: /a\n")

    assert rules.can_fetch("https://e.com/a", "x") is True


def test_empty_disallow_means_allow_everything():
    assert RobotsRules("User-agent: *\nDisallow:\n").can_fetch("https://e.com/x", "x")


def test_comments_and_stacked_user_agents():
    rules = RobotsRules(
        "User-agent: botA  # first\nUser-agent: botB\nDisallow: /x # no\n"
    )

    assert rules.can_fetch("https://e.com/x", "botA") is False
    assert rules.can_fetch("https://e.com/x", "botB") is False
    assert rules.can_fetch("https://e.com/x", "botC") is True


def test_cache_status_handling():
    def cache(status, body=""):
        return RobotsCache(lambda url: (status, body))

    assert cache(200, "User-agent: *\nDisallow: /\n").can_fetch("https://e.com/a") is False
    assert cache(404).can_fetch("https://e.com/a") is True      # no robots.txt
    assert cache(403).can_fetch("https://e.com/a") is True
    assert cache(None).can_fetch("https://e.com/a") is True     # unreachable
    assert cache(503).can_fetch("https://e.com/a") is False     # server error


def test_cache_fetches_once_per_origin_even_across_threads():
    calls = []

    def fetch(url):
        calls.append(url)
        return 200, "User-agent: *\nDisallow: /x\n"

    cache = RobotsCache(fetch)
    threads = [
        threading.Thread(target=cache.can_fetch, args=("https://e.com/a",))
        for _ in range(8)
    ]
    [t.start() for t in threads]
    [t.join() for t in threads]
    cache.can_fetch("https://e.com/b")
    cache.can_fetch("https://other.com/b")

    assert calls == ["https://e.com/robots.txt", "https://other.com/robots.txt"]


# ---- sitemaps ----------------------------------------------------------

def test_parse_sitemap_urlset_and_index_and_cdata():
    urlset = ("<urlset><url><loc>https://e.com/a</loc></url>"
              "<url><loc><![CDATA[https://e.com/b?x=1&y=2]]></loc></url></urlset>")
    index = ("<sitemapindex><sitemap><loc>https://e.com/s1.xml</loc></sitemap>"
             "</sitemapindex>")

    assert parse_sitemap(urlset) == (["https://e.com/a", "https://e.com/b?x=1&y=2"], [])
    assert parse_sitemap(index) == ([], ["https://e.com/s1.xml"])


def test_discover_follows_index_and_respects_limit():
    files = {
        "https://e.com/sitemap.xml": "<sitemapindex><sitemap><loc>https://e.com/s1.xml</loc></sitemap></sitemapindex>",
        "https://e.com/s1.xml": "<urlset>" + "".join(
            f"<url><loc>https://e.com/p{i}</loc></url>" for i in range(50)) + "</urlset>",
    }

    def fetch(url):
        return (200, files[url]) if url in files else (404, "")

    urls = discover_sitemap_urls("https://e.com/", [], fetch, limit=20)

    assert len(urls) == 20 and urls[0] == "https://e.com/p0"


def test_discover_uses_declared_sitemaps_and_survives_404():
    assert discover_sitemap_urls("https://e.com/", [], lambda u: (404, "")) == []

    seen = []

    def fetch(url):
        seen.append(url)
        return 404, ""

    discover_sitemap_urls("https://e.com/", ["https://e.com/custom.xml"], fetch)

    assert seen == ["https://e.com/custom.xml"]


# ---- throttle ----------------------------------------------------------

def test_throttle_spaces_requests_per_host_not_across_hosts():
    now = [100.0]
    slept = []

    def sleep(seconds):
        slept.append(round(seconds, 3))
        now[0] += seconds

    throttle = HostThrottle(2.0, clock=lambda: now[0], sleep=sleep)

    assert throttle.wait("a.com") == 0.0          # first request: no wait
    assert throttle.wait("b.com") == 0.0          # other host: independent
    assert throttle.wait("a.com") == 2.0          # same host: must wait
    assert throttle.wait("a.com", min_delay=5.0) == 5.0   # robots Crawl-delay wins
    assert slept == [2.0, 5.0]


def test_throttle_disabled_by_default():
    assert HostThrottle(0.0).wait("a.com") == 0.0


def test_throttle_reserves_distinct_slots_under_concurrency():
    now = [0.0]
    lock = threading.Lock()
    slots = []

    def sleep(seconds):
        with lock:
            slots.append(round(seconds, 3))

    throttle = HostThrottle(1.0, clock=lambda: now[0], sleep=sleep)
    threads = [threading.Thread(target=throttle.wait, args=("a.com",)) for _ in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]

    assert sorted(slots) == [1.0, 2.0, 3.0, 4.0]    # 5 callers, 4 distinct waits


# ---- URL rules ---------------------------------------------------------

def test_normalize_sorts_query_and_strips_sessions():
    assert normalize_url("https://E.com/a?b=2&a=1&utm_source=x&PHPSESSID=abc#frag") \
        == "https://e.com/a?a=1&b=2"
    assert normalize_url("https://e.com/p;jsessionid=AB12?x=1") == "https://e.com/p?x=1"


def test_allowed_host_with_subdomains_and_external():
    # Used to be False: allow_subdomains blocked external hosts too.
    assert is_allowed_host("https://other.org/", "https://e.com/",
                           same_host=False, allow_subdomains=True) is True
    assert is_allowed_host("https://blog.e.com/", "https://e.com/",
                           same_host=True, allow_subdomains=True) is True
    assert is_allowed_host("https://other.org/", "https://e.com/",
                           same_host=True, allow_subdomains=True) is False
    assert is_allowed_host("https://blog.e.com/", "https://e.com/",
                           same_host=True, allow_subdomains=False) is False


def test_pdf_links_are_crawlable_only_when_documents_enabled():
    assert is_crawlable_url("https://e.com/a.pdf", "https://e.com/") is False
    assert is_crawlable_url("https://e.com/a.pdf", "https://e.com/",
                            include_documents=True) is True
    assert is_crawlable_url("https://e.com/a.docx", "https://e.com/",
                            include_documents=True) is False


# ---- URL templates and traps -------------------------------------------

def test_templates_group_ids_slugs_and_facets():
    assert url_template("https://e.com/p/123") == url_template("https://e.com/p/987")
    assert url_template("https://e.com/news/this-is-a-long-post-title-here") == \
        url_template("https://e.com/news/another-quite-long-post-title-too")
    assert url_template("https://e.com/l/filter?Series=A") == \
        url_template("https://e.com/l/filter?Series=B")
    assert url_template("https://e.com/l/filter?Series=A") != \
        url_template("https://e.com/l/filter?Series=A&Sort=x")
    assert url_template("https://e.com/about") != url_template("https://e.com/contact")
    assert has_query("https://e.com/a?x=1") and not has_query("https://e.com/a")


def test_traps():
    assert looks_like_trap("https://e.com/a/b/a/b/a/b/c")
    assert looks_like_trap("https://e.com/" + "/".join("abcdefghijkl"))
    assert looks_like_trap("https://e.com/x?" + "&".join(f"k{i}=1" for i in range(8)))
    assert looks_like_trap("https://e.com/news/2026/story") is None
