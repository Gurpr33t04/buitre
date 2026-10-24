from urllib.parse import urlparse


SUPPORTED_SCHEMES = {"http", "https"}


def validate_url(url: str) -> bool:
    """
    Validate whether a URL has a supported HTTP/HTTPS scheme
    and a valid network location.
    """

    parsed = urlparse(url)

    if parsed.scheme.lower() not in SUPPORTED_SCHEMES:
        return False

    if not parsed.netloc:
        return False

    return True
