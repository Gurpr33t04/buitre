import io
from dataclasses import dataclass


@dataclass
class PdfDocument:
    title: str | None
    text: str
    pages: int
    truncated: bool


def extract_pdf(data: bytes, max_pages: int = 60) -> PdfDocument:
    """
    Extract the text layer of a PDF (needs the optional `pypdf` package).

    Scanned PDFs have no text layer; they yield empty text. OCR is not
    performed.
    """

    try:
        from pypdf import PdfReader

    except ImportError as exc:
        raise RuntimeError(
            "PDF support needs the optional 'pypdf' package "
            "(pip install pypdf)."
        ) from exc

    try:
        reader = PdfReader(io.BytesIO(data))

        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001
                raise RuntimeError("PDF is encrypted.") from None

        total = len(reader.pages)
        parts: list[str] = []

        for page in reader.pages[:max_pages]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:  # noqa: BLE001
                parts.append("")

        title = None

        if reader.metadata and reader.metadata.title:
            title = str(reader.metadata.title).strip() or None

    except RuntimeError:
        raise

    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"PDF could not be read: {exc}") from exc

    return PdfDocument(
        title=title,
        text="\n\n".join(p.strip() for p in parts if p.strip()),
        pages=total,
        truncated=total > max_pages,
    )
