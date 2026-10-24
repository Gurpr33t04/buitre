from pathlib import Path

from buitre.models import ScrapeResult
from buitre.output.json import result_to_json


def save_result(
    result: ScrapeResult,
    output_path: str,
    indent: int = 2,
) -> None:
    """
    Save a Buitre result as JSON.
    """

    path = Path(output_path)

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    path.write_text(
        result_to_json(
            result,
            indent=indent,
        ),
        encoding="utf-8",
    )
