import os


def consensus_available() -> bool:
    if os.environ.get("BUITRE_NO_TRAFILATURA"):
        return False

    try:
        import trafilatura  # noqa: F401

        return True

    except ImportError:
        return False


def consensus_text(html: str, url: str | None = None) -> str | None:
    """
    Main-content text from trafilatura, used as an independent second
    opinion: blocks that Buitre's own extractor and trafilatura both
    select get a score bonus. Returns None when trafilatura is not
    installed, so the whole feature is optional.
    """

    if not consensus_available():
        return None

    try:
        import trafilatura

        return (
            trafilatura.extract(
                html,
                url=url,
                include_comments=False,
                include_tables=True,
                favor_recall=True,
            )
            or None
        )

    except Exception:  # noqa: BLE001
        return None
