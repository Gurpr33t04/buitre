import re
from urllib.parse import urlparse

from buitre.cleaner.text import Block, extract_blocks, normalize_text
from buitre.context import PipelineContext
from buitre.detector.language import detect_language
from buitre.detector.pagetype import classify_page
from buitre.detector.rendering import needs_browser_rendering
from buitre.errors import FetchStatusError
from buitre.extractor.embedded import embedded_text_blocks
from buitre.extractor.information import extract_information
from buitre.extractor.main_content import consensus_text
from buitre.extractor.pdf import extract_pdf
from buitre.fetcher.browser import fetch_with_browser
from buitre.fetcher.http import fetch_url
from buitre.fetcher.status import BLOCKED, NON_TEXT, classify_fetch
from buitre.fetcher.tor import fetch_with_tor
from buitre.fetcher.tor_browser import fetch_with_tor_browser
from buitre.fetcher.transport import is_pdf_content
from buitre.models import (
    FetchResult,
    PageData,
    ScrapeResult,
    Statistics,
    TargetInfo,
)
from buitre.parser.html import parse_html
from buitre.prioritizer.scorer import (
    candidate_records,
    prioritize_blocks,
)
from buitre.quality import assess_quality
from buitre.strategy.selector import select_initial_strategy
from buitre.target.analyzer import analyze_target
from buitre.utils.soup import make_soup


BROWSER_METHODS = {"browser", "tor_browser"}

# Enough embedded/API prose that a browser would add nothing.
EMBEDDED_SUFFICIENT_CHARS = 200

# A page with less visible text than this may be an application shell,
# so embedded app state / captured API JSON is consulted.
THIN_TEXT_CHARS = 400

MIN_DOCUMENT_PARAGRAPH = 35


def _fetch(
    method: str,
    url: str,
    context: PipelineContext | None,
) -> FetchResult:
    """Run one acquisition method by name."""

    if method == "http":
        if context is not None:
            return fetch_url(url, client=context.http_client())

        return fetch_url(url)

    if method == "tor_http":
        if context is not None:
            return fetch_with_tor(url, client=context.tor_client())

        return fetch_with_tor(url)

    if method == "browser":
        if context is not None:
            return fetch_with_browser(
                url, pool=context.browser_pool(tor=False)
            )

        return fetch_with_browser(url)

    if method == "tor_browser":
        if context is not None:
            return fetch_with_tor_browser(
                url, pool=context.browser_pool(tor=True)
            )

        return fetch_with_tor_browser(url)

    raise RuntimeError(f"Unsupported acquisition strategy: {method}")


def _browser_method(target: TargetInfo) -> str:
    return "tor_browser" if target.is_onion else "browser"


# ---------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------

def run_pipeline(
    url: str,
    content_limit: int = 10,
    context: PipelineContext | None = None,
) -> ScrapeResult:
    """
    Run the complete Buitre scraping pipeline.

    Surface web:
        HTTP -> browser fallback (only when it can add content)

    Onion service:
        Tor HTTP -> Tor browser fallback

    Pass a `context` (the crawler does) to share one HTTP client, one
    browser, per-host strategy memory and learned site templates across
    pages.
    """

    target = analyze_target(url)
    strategy = select_initial_strategy(target)

    warnings: list[str] = []

    # ---- 1. first acquisition (host memory may skip straight to the
    #         browser for URL shapes that never worked over HTTP) ----
    method = strategy.strategy

    if (
        context is not None
        and strategy.fallback_allowed
        and not target.is_onion
        and context.host_memory.prefers_browser(target.url)
    ):
        method = "browser"
        warnings.append(
            "Browser used directly: this kind of page needed it before."
        )

    fetch_result = _fetch(method, target.url, context)
    already_browser = fetch_result.fetch_method in BROWSER_METHODS

    # ---- 2. is the response real page content? ----
    verdict = classify_fetch(fetch_result)

    if (
        verdict.verdict == NON_TEXT
        and context is not None
        and context.documents
        and is_pdf_content(
            fetch_result.content_type or "",
            fetch_result.content_bytes or b"",
        )
        and fetch_result.content_bytes
    ):
        return _document_result(
            target, fetch_result, content_limit, warnings
        )

    if (
        verdict.verdict == BLOCKED
        and strategy.fallback_allowed
        and not already_browser
    ):
        # A bot-protection challenge gets exactly one browser attempt.
        try:
            browser_result = _fetch(
                _browser_method(target), target.url, context
            )

        except RuntimeError as exc:
            raise FetchStatusError(
                f"{verdict.reason} Browser retry failed: {exc}",
                status_code=fetch_result.status_code,
                verdict=verdict.verdict,
            ) from exc

        browser_verdict = classify_fetch(browser_result)

        if not browser_verdict.ok:
            raise FetchStatusError(
                f"{verdict.reason} Browser retry: {browser_verdict.reason}",
                status_code=(
                    browser_result.status_code or fetch_result.status_code
                ),
                verdict=verdict.verdict,
            )

        fetch_result = browser_result
        already_browser = True

        warnings.append(
            "HTTP response looked like a bot challenge; "
            "the browser retry succeeded."
        )

    elif not verdict.ok:
        raise FetchStatusError(
            verdict.reason,
            status_code=fetch_result.status_code,
            verdict=verdict.verdict,
            retry_after=verdict.retry_after,
        )

    # ---- 3. parse, then decide whether rendering would add anything ----
    page = parse_html(fetch_result)

    needs_browser, render_reason = needs_browser_rendering(
        page,
        raw_html=fetch_result.body,
    )

    embedded_blocks: list[str] | None = None
    browser_used = already_browser

    if needs_browser and not already_browser:
        # Many "JavaScript-only" pages ship their content as JSON inside
        # the HTML. Reading that is far cheaper than a browser.
        if context is None or context.use_embedded_state:
            embedded_blocks = embedded_text_blocks(
                fetch_result.body,
                fetch_result.api_responses,
            )

        embedded_chars = sum(len(b) for b in embedded_blocks or [])

        if embedded_chars >= EMBEDDED_SUFFICIENT_CHARS:
            warnings.append(
                "Content recovered from the page's embedded application "
                "state; browser rendering was not needed."
            )
            needs_browser = False

    # ---- 4. browser fallback that can never destroy a good fetch ----
    if needs_browser and not already_browser:

        if not strategy.fallback_allowed:
            warnings.append(
                "Browser rendering suggested but not allowed: "
                f"{render_reason}"
            )

        else:
            try:
                rendered = _fetch(
                    _browser_method(target), target.url, context
                )

            except RuntimeError as exc:
                warnings.append(
                    f"Browser fallback failed ({exc}); "
                    "using the HTTP result."
                )

            else:
                rendered_verdict = classify_fetch(rendered)

                if not rendered_verdict.ok:
                    warnings.append(
                        "Browser fallback returned an unusable response "
                        f"({rendered_verdict.reason}); "
                        "using the HTTP result."
                    )

                else:
                    rendered_page = parse_html(rendered)

                    if (
                        len(rendered_page.text.strip())
                        >= len(page.text.strip())
                    ):
                        fetch_result = rendered
                        page = rendered_page
                        embedded_blocks = None
                        browser_used = True

                        warnings.append(
                            f"Browser fallback used: {render_reason}"
                        )

                    else:
                        warnings.append(
                            "Browser rendering produced less text than "
                            "HTTP; using the HTTP result."
                        )

    if context is not None:
        context.host_memory.record(
            target.url,
            needed_browser=fetch_result.fetch_method in BROWSER_METHODS,
        )

    return _analyze(
        target,
        fetch_result,
        page,
        warnings,
        content_limit,
        context,
        browser_used=fetch_result.fetch_method in BROWSER_METHODS,
        embedded_blocks=embedded_blocks,
    )


def process_html(
    html: str,
    url: str,
    content_limit: int = 10,
    context: PipelineContext | None = None,
    headers: dict[str, str] | None = None,
) -> ScrapeResult:
    """
    Run parsing, extraction and scoring on HTML you already have: a saved
    page, a test fixture, the evaluation corpus. No network, no browser.
    """

    target = analyze_target(url)

    fetch_result = FetchResult(
        requested_url=url,
        final_url=url,
        status_code=200,
        content_type="text/html; charset=utf-8",
        headers=headers or {},
        body=html,
        fetch_method="local",
        encoding="utf-8",
    )

    page = parse_html(fetch_result)

    return _analyze(
        target,
        fetch_result,
        page,
        [],
        content_limit,
        context,
        browser_used=False,
        embedded_blocks=None,
    )


# ---------------------------------------------------------------------
# Analysis (shared by live fetches and offline HTML)
# ---------------------------------------------------------------------

def _analyze(
    target: TargetInfo,
    fetch_result: FetchResult,
    page: PageData,
    warnings: list[str],
    content_limit: int,
    context: PipelineContext | None,
    *,
    browser_used: bool,
    embedded_blocks: list[str] | None,
) -> ScrapeResult:
    html = fetch_result.body

    # ---- content blocks, in document order ----
    soup = make_soup(html)
    blocks = extract_blocks(soup)

    # ---- thin page? recover prose from embedded state / API JSON ----
    embedded_used = False
    use_embedded = context is None or context.use_embedded_state
    thin = len(page.text.strip()) < THIN_TEXT_CHARS

    if use_embedded and (thin or not blocks):
        if embedded_blocks is None:
            embedded_blocks = embedded_text_blocks(
                html, fetch_result.api_responses
            )

        for text in embedded_blocks:
            blocks.append(
                Block(text=text, index=len(blocks), in_main=True)
            )

        if embedded_blocks:
            embedded_used = True
            page.text = (
                page.text + "\n" + "\n".join(embedded_blocks)
            ).strip()

            warnings.append(
                "Content read from embedded application state / API "
                "responses."
            )

    # ---- learn this site's template, then drop template blocks ----
    learner = None

    if context is not None and context.learn_boilerplate:
        learner = context.boilerplate.for_host(target.host)
        learner.observe([b.text for b in blocks])

    consensus = None if embedded_used else consensus_text(html, target.url)

    important_content = prioritize_blocks(
        blocks,
        limit=content_limit,
        boilerplate=learner,
        consensus_text=consensus,
    )

    candidates = candidate_records(
        blocks,
        content_limit * 3,
        consensus_text=consensus,
        boilerplate=learner,
    )

    # ---- structured information ----
    extracted = extract_information(
        text=page.text,
        html=html,
        important_content=important_content,
        source_url=target.url,
        language=page.language,
        link_details=page.link_details,
    )

    # ---- page type, onion hints ----
    page.page_type = classify_page(
        fetch_result.final_url or target.url,
        page,
        fetch_result.content_type,
    )

    if (
        page.page_type == "error"
        and (fetch_result.status_code or 200) < 400
        and len(page.text.strip()) < 400
    ):
        raise FetchStatusError(
            f"Soft 404: the page says it is an error page "
            f"({page.title!r}).",
            status_code=fetch_result.status_code,
            verdict="soft_404",
        )

    onion_location = next(
        (
            value
            for key, value in fetch_result.headers.items()
            if key.lower() == "onion-location"
        ),
        None,
    )

    if onion_location:
        page.metadata["onion_location"] = onion_location

    onion_links = sorted(
        {
            host
            for link in page.link_details
            if (host := (urlparse(link.url).hostname or "").lower())
            .endswith(".onion")
            and host != target.host
        }
    )[:50]

    if onion_links:
        page.metadata["onion_links"] = onion_links

    # ---- quality ----
    main_blocks = sum(1 for block in blocks if block.in_main)

    quality, flags = assess_quality(
        page,
        extracted,
        fetch_result,
        content_limit=content_limit,
        main_blocks=main_blocks,
        total_blocks=len(blocks),
        browser_used=browser_used,
        embedded_used=embedded_used,
    )

    statistics = Statistics(
        text_length=len(page.text),
        link_count=len(page.links),
        image_count=len(page.images),
        email_count=len(extracted.emails),
        extraction_score=quality,
        main_text_length=sum(len(b.text) for b in blocks if b.in_main),
        quality_flags=flags,
    )

    if not page.text.strip():
        warnings.insert(0, "No readable text was extracted.")

    return ScrapeResult(
        target=target,
        fetch=fetch_result,
        page=page,
        extracted=extracted,
        statistics=statistics,
        warnings=warnings,
        candidates=candidates,
    )


# ---------------------------------------------------------------------
# Documents (PDF)
# ---------------------------------------------------------------------

def _document_result(
    target: TargetInfo,
    fetch_result: FetchResult,
    content_limit: int,
    warnings: list[str],
) -> ScrapeResult:
    """Build a result from a PDF's text layer (needs `pypdf`)."""

    try:
        document = extract_pdf(fetch_result.content_bytes or b"")

    except RuntimeError as exc:
        raise FetchStatusError(
            str(exc),
            status_code=fetch_result.status_code,
            verdict="document_error",
        ) from exc

    filename = urlparse(target.url).path.rsplit("/", 1)[-1] or target.url

    paragraphs = []

    for raw in re.split(r"\n\s*\n", document.text):
        text = normalize_text(raw.replace("\n", " "))

        if len(text) >= MIN_DOCUMENT_PARAGRAPH:
            paragraphs.append(
                Block(text=text, index=len(paragraphs), in_main=True)
            )

    if not document.text.strip():
        warnings.append(
            "The PDF has no text layer (scanned image); OCR is not "
            "performed."
        )

    if document.truncated:
        warnings.append(
            f"Only the first pages of {document.pages} were read."
        )

    page = PageData(
        title=document.title or filename,
        language=detect_language(document.text),
        text=document.text,
        page_type="document",
        metadata={"pages": document.pages, "truncated": document.truncated},
    )

    important_content = prioritize_blocks(paragraphs, limit=content_limit)

    extracted = extract_information(
        text=document.text,
        html="",
        important_content=important_content,
        source_url=target.url,
        language=page.language,
    )

    quality, flags = assess_quality(
        page,
        extracted,
        fetch_result,
        content_limit=content_limit,
        main_blocks=len(paragraphs),
        total_blocks=len(paragraphs),
    )

    return ScrapeResult(
        target=target,
        fetch=fetch_result,
        page=page,
        extracted=extracted,
        statistics=Statistics(
            text_length=len(document.text),
            email_count=len(extracted.emails),
            extraction_score=quality,
            main_text_length=len(document.text),
            quality_flags=flags,
        ),
        warnings=warnings,
        candidates=candidate_records(paragraphs, content_limit * 3),
    )
