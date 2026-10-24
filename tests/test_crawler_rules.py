from buitre.crawler.rules import (
    is_allowed_host,
    is_path_allowed,
    is_crawlable_url,
    normalize_url,
    resolve_url,
)


def test_same_host_is_allowed():
    assert is_allowed_host(
        "https://example.com/page",
        "https://example.com",
    )


def test_subdomain_is_blocked_by_default():
    assert not is_allowed_host(
        "https://api.example.com/page",
        "https://example.com",
    )


def test_subdomain_is_allowed_when_enabled():
    assert is_allowed_host(
        "https://api.example.com/page",
        "https://example.com",
        allow_subdomains=True,
    )


def test_similar_but_different_domain_is_blocked():
    assert not is_allowed_host(
        "https://evil-example.com/page",
        "https://example.com",
        allow_subdomains=True,
    )


def test_unrelated_domain_is_blocked():
    assert not is_allowed_host(
        "https://other.com/page",
        "https://example.com",
        allow_subdomains=True,
    )


def test_normalize_url_removes_fragment():
    assert normalize_url(
        "https://example.com/page#section"
    ) == "https://example.com/page"


def test_resolve_relative_link():
    assert resolve_url(
        "https://example.com/blog/",
        "article",
    ) == "https://example.com/blog/article"


def test_non_http_links_are_ignored():
    assert resolve_url(
        "https://example.com/",
        "mailto:test@example.com",
    ) is None


def test_pdf_is_not_crawlable():
    assert not is_crawlable_url(
        "https://example.com/report.pdf",
        "https://example.com",
    )


def test_include_path_allows_matching_url():
    assert is_path_allowed(
        "https://example.com/blog/article",
        include_paths=["/blog/"],
    )


def test_include_path_blocks_nonmatching_url():
    assert not is_path_allowed(
        "https://example.com/about",
        include_paths=["/blog/"],
    )


def test_exclude_path_blocks_matching_url():
    assert not is_path_allowed(
        "https://example.com/admin/users",
        exclude_paths=["/admin/"],
    )


def test_exclude_takes_priority_over_include():
    assert not is_path_allowed(
        "https://example.com/blog/admin",
        include_paths=["/blog/"],
        exclude_paths=["/blog/admin"],
    )


def test_path_prefix_does_not_match_similar_name():
    assert not is_path_allowed(
        "https://example.com/blogger",
        include_paths=["/blog/"],
    )
