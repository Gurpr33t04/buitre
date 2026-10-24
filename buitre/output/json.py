import json

from buitre.models import CrawlResult, ScrapeResult
from pathlib import Path

def _extracted_dict(extracted) -> dict:
    return {
        "emails": extracted.emails,
        "phone_numbers": extracted.phone_numbers,
        "domains": extracted.domains,
        "outbound_domains": extracted.outbound_domains,
        "contacts": extracted.contacts,
        "dates": extracted.dates,
        "people": extracted.people,
        "organizations": extracted.organizations,
        "locations": extracted.locations,
    }


def _page_dict(page, *, include_text: bool, include_links: bool) -> dict:
    data = {
        "title": page.title,
        "description": page.description,
        "language": page.language,
        "page_type": page.page_type,
        "canonical_url": page.canonical_url,
        "published_at": page.published_at,
        "modified_at": page.modified_at,
        "author": page.author,
        "heading_count": len(page.headings),
        "link_count": len(page.links),
        "image_count": len(page.images),
    }

    if include_text:
        data["headings"] = page.headings
        data["text"] = page.text

    if include_links:
        data["links"] = [
            {
                "url": link.url,
                "text": link.text,
                "rel": link.rel,
                "region": link.region,
            }
            for link in page.link_details
        ]

    return data


def result_to_dict(
    result: ScrapeResult,
    *,
    include_text: bool = False,
    include_links: bool = False,
) -> dict:
    return {
        "target": {
            "url": result.target.url,
            "scheme": result.target.scheme,
            "host": result.target.host,
            "target_type": result.target.target_type,
            "is_onion": result.target.is_onion,
        },
        "fetch": {
            "final_url": result.fetch.final_url,
            "status_code": result.fetch.status_code,
            "content_type": result.fetch.content_type,
            "method": result.fetch.fetch_method,
            "elapsed_time": result.fetch.elapsed_time,
            "encoding": result.fetch.encoding,
        },
        "page": _page_dict(
            result.page,
            include_text=include_text,
            include_links=include_links,
        ),
        "metadata": result.page.metadata,
        "important_content": result.extracted.important_content,
        "extracted": _extracted_dict(result.extracted),
        "statistics": {
            "text_length": result.statistics.text_length,
            "main_text_length": result.statistics.main_text_length,
            "link_count": result.statistics.link_count,
            "image_count": result.statistics.image_count,
            "email_count": result.statistics.email_count,
            "extraction_score": result.statistics.extraction_score,
            "quality_flags": result.statistics.quality_flags,
        },
        "warnings": result.warnings,
    }


def page_record(
    result: ScrapeResult,
    crawl_page,
    *,
    include_text: bool = False,
    include_links: bool = False,
) -> dict:
    """One crawled page as a self-contained JSON object (for JSONL)."""

    record = result_to_dict(
        result,
        include_text=include_text,
        include_links=include_links,
    )

    record["crawl"] = {
        "url": crawl_page.url,
        "parent_url": crawl_page.parent_url,
        "depth": crawl_page.depth,
        "template": crawl_page.template,
        "priority": crawl_page.priority,
        "retries": crawl_page.retries,
    }

    return record


class JsonlWriter:
    """Append one JSON object per line, flushed immediately, so a crawl's
    output survives a crash and never has to fit in memory."""

    def __init__(self, path: str) -> None:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        self._file = destination.open("a", encoding="utf-8")

    def write(self, record: dict) -> None:
        self._file.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def crawl_to_dict(
    result: CrawlResult,
    *,
    include_text: bool = False,
    include_links: bool = False,
) -> dict:
    pages = []

    for metadata in result.page_metadata:

        page_summary = {
            "url": metadata.url,
            "parent_url": metadata.parent_url,
            "depth": metadata.depth,
            "status_code": metadata.status_code,
            "method": metadata.fetch_method,
            "success": metadata.success,
            "error": metadata.error,
            "page_type": metadata.page_type,
            "quality": metadata.quality,
            "duplicate_of": metadata.duplicate_of,
            "template": metadata.template,
            "priority": metadata.priority,
            "retries": metadata.retries,
        }

        if (
            metadata.success
            and metadata.result_index is not None
            and metadata.result_index < len(result.pages)
        ):
            page = result.pages[
                metadata.result_index
            ]

            page_summary.update(
                {
                    "final_url": page.fetch.final_url,
                    "title": page.page.title,
                    "description": page.page.description,
                    "language": page.page.language,
                    "published_at": page.page.published_at,
                    "link_count": len(page.page.links),
                    "image_count": len(page.page.images),
                    "important_content": (
                        page.extracted.important_content
                    ),
                    "extracted": _extracted_dict(page.extracted),
                    "quality_flags": page.statistics.quality_flags,
                }
            )

            if include_text:
                page_summary["text"] = page.page.text
                page_summary["headings"] = page.page.headings

            if include_links:
                page_summary["links"] = [
                    {
                        "url": link.url,
                        "text": link.text,
                        "rel": link.rel,
                        "region": link.region,
                    }
                    for link in page.page.link_details
                ]

        pages.append(page_summary)

    successful_pages = sum(
        1
        for metadata in result.page_metadata
        if metadata.success
    )

    failed_pages = sum(
        1
        for metadata in result.page_metadata
        if not metadata.success
    )

    return {
        "target": {
            "url": result.target.url,
            "scheme": result.target.scheme,
            "host": result.target.host,
            "target_type": result.target.target_type,
            "is_onion": result.target.is_onion,
        },
        "crawl": {
            "pages_scanned": result.pages_scanned,
            "urls_attempted": result.urls_attempted,
            "max_depth": result.max_depth,
            "max_depth_reached": result.max_depth_reached,
            "max_pages": result.max_pages,
            "successful_pages": successful_pages,
            "failed_pages": failed_pages,
            "duplicates": result.duplicates,
            "retries": result.retries,
            "resumed_pages": result.resumed_pages,
            "skipped": result.skipped,
        },
        "pages": pages,
        "warnings": result.warnings,
    }


def result_to_json(
    result: ScrapeResult,
    indent: int = 2,
    **options,
) -> str:
    return json.dumps(
        result_to_dict(result, **options),
        indent=indent,
        ensure_ascii=False,
    )


def crawl_to_json(
    result: CrawlResult,
    indent: int = 2,
    **options,
) -> str:
    return json.dumps(
        crawl_to_dict(result, **options),
        indent=indent,
        ensure_ascii=False,
    )


def save_output(
    output: str,
    filename: str,
) -> None:
    destination = Path(filename)

    temporary = destination.with_suffix(
        destination.suffix + ".tmp"
    )

    try:
        temporary.write_text(
            output,
            encoding="utf-8",
        )

        temporary.replace(destination)

    except Exception:
        if temporary.exists():
            temporary.unlink()

        raise
