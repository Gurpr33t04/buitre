import os

from bs4 import BeautifulSoup


def _default_parser() -> str:
    forced = os.environ.get("BUITRE_PARSER")

    if forced:
        return forced

    try:
        import lxml  # noqa: F401

        # lxml copes far better with the broken markup of old sites
        # (unclosed <p>/<td>, stray tags) and is much faster.
        return "lxml"

    except ImportError:
        return "html.parser"


PARSER = _default_parser()


def make_soup(markup: str) -> BeautifulSoup:
    """The one place Buitre builds a BeautifulSoup tree."""

    return BeautifulSoup(markup or "", PARSER)
