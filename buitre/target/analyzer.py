from urllib.parse import urlparse

from buitre.models import TargetInfo
from buitre.target.validator import validate_url


def analyze_target(url: str) -> TargetInfo:
    """
    Analyze and normalize a target URL before fetching it.
    """

    url = url.strip()

    if not validate_url(url):
        raise ValueError(
            "Invalid URL. Buitre currently supports HTTP and HTTPS URLs."
        )

    parsed = urlparse(url)

    scheme = parsed.scheme.lower()
    host = parsed.hostname or ""

    is_onion = host.lower().endswith(".onion")

    if is_onion:
        target_type = "onion"
    else:
        target_type = "surface_web"

    return TargetInfo(
        url=url,
        scheme=scheme,
        host=host,
        target_type=target_type,
        is_onion=is_onion,
    )
