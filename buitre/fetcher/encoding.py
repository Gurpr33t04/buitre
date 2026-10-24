import codecs
import re


# Declared charsets that real servers routinely get wrong: pages labelled
# latin-1/ascii that are actually UTF-8. Browsers treat "latin-1" as
# windows-1252, and so do we.
_LATIN_LABELS = {
    "iso-8859-1", "latin-1", "latin1", "l1", "iso8859-1", "iso_8859-1",
    "us-ascii", "ascii", "ansi_x3.4-1968",
}

_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([A-Za-z0-9_.:-]+)""",
    re.IGNORECASE,
)
_HEADER_CHARSET_RE = re.compile(
    r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_.:-]+)",
    re.IGNORECASE,
)

_BOMS = [
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
]


def _lookup(name: str | None) -> str | None:
    if not name:
        return None

    try:
        return codecs.lookup(name.strip().strip("\"'")).name

    except LookupError:
        return None


_DETECTOR_TRUSTED = {
    _lookup(name)
    for name in (
        "shift_jis", "cp932", "euc_jp", "iso2022_jp",
        "euc_kr", "cp949", "gb2312", "gbk", "gb18030", "big5", "big5hkscs",
        "koi8_r", "koi8_u", "cp1251", "iso8859_5",
        "cp1253", "iso8859_7", "cp1255", "iso8859_8",
        "cp1256", "iso8859_6", "tis_620", "cp874",
        "utf_16", "utf_32",
    )
}

def _try(content: bytes, encoding: str) -> str | None:
    try:
        return content.decode(encoding)

    except (UnicodeDecodeError, LookupError):
        return None


def _meta_charset(content: bytes) -> str | None:
    match = _META_CHARSET_RE.search(content[:4096])

    if not match:
        return None

    return _lookup(match.group(1).decode("ascii", "ignore"))


def _header_charset(content_type: str | None) -> str | None:
    if not content_type:
        return None

    match = _HEADER_CHARSET_RE.search(content_type)

    return _lookup(match.group(1)) if match else None


def decode_body(
    content: bytes,
    content_type: str | None = None,
) -> tuple[str, str]:
    """
    Decode a response body to text. Returns (text, encoding_used).

    Order: BOM -> charset in Content-Type -> <meta charset> -> strict
    UTF-8 -> charset-normalizer (if installed) -> windows-1252.

    httpx's own `response.text` falls back to UTF-8 with replacement
    characters, which turns the many legacy windows-1252, Shift-JIS and
    GBK pages into mojibake.
    """

    if not content:
        return "", "utf-8"

    for bom, encoding in _BOMS:
        if content.startswith(bom):
            text = _try(content, encoding)

            if text is not None:
                return text, encoding

    declared = [
        name
        for name in (_header_charset(content_type), _meta_charset(content))
        if name
    ]

    for name in declared:
        if name in {_lookup(label) for label in _LATIN_LABELS}:
            # Probably mislabelled UTF-8: trust the bytes, not the label.
            if any(byte > 127 for byte in content):
                text = _try(content, "utf-8")

                if text is not None:
                    return text, "utf-8"

            text = _try(content, "cp1252")

            if text is not None:
                return text, "cp1252"

            continue

        text = _try(content, name)

        if text is not None:
            return text, name

    text = _try(content, "utf-8")

    if text is not None:
        return text, "utf-8"

    try:
        from charset_normalizer import from_bytes

        best = from_bytes(content).best()

        if best is not None:
            name = _lookup(best.encoding)

            # The detector is only trusted for multi-byte and non-Latin
            # encodings. For single-byte Western text it likes to answer
            # with obscure DOS code pages (cp775, cp1125...), which turns
            # a plain windows-1252 page into garbage.
            if name in _DETECTOR_TRUSTED:
                return str(best), name

    except ImportError:
        pass

    return content.decode("cp1252", errors="replace"), "cp1252"
