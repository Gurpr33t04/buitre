"""Keep the test suite off the network.

The crawler fetches robots.txt and sitemaps. Tests that exercise the crawler
with a patched `run_pipeline` must not make real HTTP requests for those.
"""

import pytest


@pytest.fixture(autouse=True)
def no_network_for_robots_and_sitemaps(monkeypatch):
    from buitre.crawler import robots

    monkeypatch.setattr(robots, "default_fetch_text", lambda url: (404, ""))
