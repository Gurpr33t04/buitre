from typing import Any

from pydantic import BaseModel, Field


class TargetInfo(BaseModel):
    """Normalized information about the requested target."""

    url: str
    scheme: str
    host: str
    target_type: str
    is_onion: bool = False

class StrategyDecision(BaseModel):
    """Decision made by Buitre's adaptive strategy selector."""

    strategy: str
    reason: str
    fallback_allowed: bool = True

class FetchResult(BaseModel):
    """Information returned by a Buitre acquisition method."""

    requested_url: str
    final_url: str
    status_code: int | None = None
    content_type: str | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    fetch_method: str
    elapsed_time: float = 0.0

    # Detected character encoding of `body` (None when unknown).
    encoding: str | None = None

    # True when the body was cut at the size cap.
    truncated: bool = False

    # Raw bytes, kept only for documents (PDF) that need a parser.
    content_bytes: bytes | None = None

    # JSON XHR/fetch responses captured while rendering in the browser:
    # [{"url": ..., "status": ..., "text": ...}]
    api_responses: list[dict[str, Any]] = Field(default_factory=list)


class LinkInfo(BaseModel):
    """A link with the context needed to decide whether to follow it."""

    url: str
    text: str = ""
    rel: list[str] = Field(default_factory=list)

    # main | body | nav | header | footer | aside
    region: str = "body"


class PageData(BaseModel):
    """Normalized representation of parsed page content."""

    title: str | None = None
    description: str | None = None
    language: str | None = None
    headings: list[str] = Field(default_factory=list)
    text: str = ""
    links: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    structured_data: list[dict[str, Any]] = Field(default_factory=list)

    # Links with anchor text, rel and page region (`links` is kept for
    # backwards compatibility and holds the same URLs).
    link_details: list[LinkInfo] = Field(default_factory=list)

    # article | listing | contact | policy | login | error | document |
    # product | home | generic | unknown
    page_type: str = "unknown"

    canonical_url: str | None = None
    published_at: str | None = None
    modified_at: str | None = None
    author: str | None = None

    forms: list[dict[str, Any]] = Field(default_factory=list)


class ExtractedData(BaseModel):
    """Information extracted from the normalized page."""

    emails: list[str] = Field(default_factory=list)
    phone_numbers: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    dates: list[str] = Field(default_factory=list)
    people: list[str] = Field(default_factory=list)
    organizations: list[str] = Field(default_factory=list)
    locations: list[str] = Field(default_factory=list)
    important_content: list[str] = Field(default_factory=list)

    # `domains` = domains mentioned in the content. `outbound_domains` =
    # every external domain linked from anywhere on the page.
    outbound_domains: list[str] = Field(default_factory=list)

    # [{"type": "email"|"phone", "value": ..., "source": ...}]
    contacts: list[dict[str, str]] = Field(default_factory=list)


class Statistics(BaseModel):
    """Extraction and processing statistics."""

    text_length: int = 0
    link_count: int = 0
    image_count: int = 0
    email_count: int = 0
    extraction_score: float = 0.0
    main_text_length: int = 0
    quality_flags: list[str] = Field(default_factory=list)


class ScrapeResult(BaseModel):
    """Complete normalized result produced by Buitre."""

    target: TargetInfo
    fetch: FetchResult
    page: PageData
    extracted: ExtractedData
    statistics: Statistics
    warnings: list[str] = Field(default_factory=list)

    # Top scored blocks kept so the crawler can re-select the important
    # content once it has learned which blocks are site boilerplate.
    candidates: list[dict[str, Any]] = Field(default_factory=list)


class CrawlPage(BaseModel):
    """Metadata describing a page discovered during crawling."""

    url: str
    parent_url: str | None = None
    depth: int = 0
    status_code: int | None = None
    fetch_method: str | None = None
    success: bool = True
    error: str | None = None
    result_index: int | None = None

    page_type: str | None = None
    quality: float | None = None
    duplicate_of: str | None = None
    template: str | None = None
    priority: float | None = None
    retries: int = 0

class CrawlResult(BaseModel):
    """Complete result produced by a controlled crawl."""

    target: TargetInfo
    pages: list[ScrapeResult] = Field(default_factory=list)
    page_metadata: list[CrawlPage] = Field(default_factory=list)

    pages_scanned: int = 0
    urls_attempted: int = 0

    max_depth: int = 0
    max_depth_reached: int = 0
    max_pages: int = 0

    warnings: list[str] = Field(default_factory=list)

    duplicates: int = 0
    retries: int = 0
    resumed_pages: int = 0

    # Why URLs were not fetched: {"robots": 3, "template_cap": 12, ...}
    skipped: dict[str, int] = Field(default_factory=dict)
