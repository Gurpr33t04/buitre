from buitre.models import ExtractedData, FetchResult, PageData


def assess_quality(
    page: PageData,
    extracted: ExtractedData,
    fetch: FetchResult,
    *,
    content_limit: int,
    main_blocks: int,
    total_blocks: int,
    browser_used: bool = False,
    embedded_used: bool = False,
) -> tuple[float, list[str]]:
    """
    A 0..1 confidence that the extraction is real content, plus the
    reasons it is not higher. Replaces the old constant score.

    Components: amount of text (0.30), how many content blocks survived
    (0.25), share of blocks inside the main region (0.15), a title
    (0.10), a known language (0.05), structured metadata (0.10) and low
    link density (0.05).
    """

    flags: list[str] = []
    text_length = len(page.text.strip())

    score = 0.0

    score += 0.30 * min(text_length / 800, 1.0)

    if content_limit > 0:
        score += 0.25 * min(len(extracted.important_content) / content_limit, 1.0)

    if total_blocks:
        score += 0.15 * (main_blocks / total_blocks)
    else:
        flags.append("no_content_blocks")

    if page.title:
        score += 0.10
    else:
        flags.append("no_title")

    if page.language:
        score += 0.05

    if page.structured_data or page.metadata.get("opengraph") or page.published_at:
        score += 0.10

    if len(page.links) < max(text_length / 60, 10):
        score += 0.05
    else:
        flags.append("link_heavy")

    if text_length < 200:
        flags.append("thin_text")

    if total_blocks and not main_blocks:
        flags.append("no_main_region")

    if page.page_type in ("error", "login"):
        flags.append(f"page_type_{page.page_type}")
        score *= 0.4

    if fetch.truncated:
        flags.append("truncated")

    if browser_used:
        flags.append("rendered_by_browser")

    if embedded_used:
        flags.append("embedded_state_used")

    return round(min(max(score, 0.0), 1.0), 3), flags
