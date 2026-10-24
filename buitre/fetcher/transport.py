import time
from typing import Any

from buitre.config import MAX_BODY_BYTES, MAX_DOCUMENT_BYTES
from buitre.fetcher.encoding import decode_body
from buitre.models import FetchResult


def is_text_content(content_type: str) -> bool:
    """Determine whether a response should be treated as text."""

    content_type = (content_type or "").lower()

    if not content_type:
        # Missing header: assume markup and let the parser decide.
        return True

    return (
        content_type.startswith("text/")
        or "html" in content_type
        or "xml" in content_type
        or "json" in content_type
        or "javascript" in content_type
    )


def is_pdf_content(content_type: str, content: bytes = b"") -> bool:
    return "pdf" in (content_type or "").lower() or content[:5] == b"%PDF-"


def _header(headers: dict[str, str], name: str) -> str:
    wanted = name.lower()

    for key, value in headers.items():
        if key.lower() == wanted:
            return value

    return ""


def build_fetch_result(
    *,
    requested_url: str,
    final_url: str,
    status_code: int | None,
    headers: dict[str, str],
    content: bytes,
    method: str,
    elapsed: float,
    truncated: bool = False,
) -> FetchResult:
    """
    Turn raw response parts into a FetchResult. Pure function: no I/O, so
    it is fully unit-testable without a network.
    """

    content_type = _header(headers, "content-type")

    if not content_type and is_pdf_content("", content):
        content_type = "application/pdf"

    body = ""
    encoding = None
    content_bytes = None

    if is_pdf_content(content_type, content):
        if not truncated:
            content_bytes = content

    elif is_text_content(content_type):
        body, encoding = decode_body(content, content_type)

    return FetchResult(
        requested_url=requested_url,
        final_url=final_url,
        status_code=status_code,
        content_type=content_type,
        headers=dict(headers),
        body=body,
        fetch_method=method,
        elapsed_time=round(elapsed, 4),
        encoding=encoding,
        truncated=truncated,
        content_bytes=content_bytes,
    )


def fetch_via_client(
    client: Any,
    url: str,
    method: str,
    max_bytes: int = MAX_BODY_BYTES,
) -> FetchResult:
    """
    GET `url` through an httpx-style client, streaming the body so that a
    huge or binary response is never fully downloaded:

    * binary payloads (images, video, archives) are not read at all;
    * text is cut at `max_bytes`;
    * PDFs are read up to MAX_DOCUMENT_BYTES for text extraction.
    """

    started = time.perf_counter()

    with client.stream("GET", url) as response:
        headers = dict(response.headers)
        content_type = _header(headers, "content-type")

        is_pdf = is_pdf_content(content_type)
        is_text = is_text_content(content_type)

        limit = MAX_DOCUMENT_BYTES if is_pdf else max_bytes

        buffer = bytearray()
        truncated = False

        if is_text or is_pdf:
            for chunk in response.iter_bytes():
                buffer.extend(chunk)

                if len(buffer) > limit:
                    truncated = True
                    del buffer[limit:]
                    break

        final_url = str(response.url)
        status_code = response.status_code

    return build_fetch_result(
        requested_url=url,
        final_url=final_url,
        status_code=status_code,
        headers=headers,
        content=bytes(buffer),
        method=method,
        elapsed=time.perf_counter() - started,
        truncated=truncated,
    )
