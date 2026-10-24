import json
import logging
from typing import Any

from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


ORGANIZATION_TYPES = {
    "Organization",
    "Corporation",
    "EducationalOrganization",
    "CollegeOrUniversity",
    "GovernmentOrganization",
    "NGO",
    "NewsMediaOrganization",
}

PERSON_TYPES = {"Person"}


def _add_unique(
    values: list[str],
    value: Any,
) -> None:
    """Add a clean string if it is not already present."""

    if not isinstance(value, str):
        return

    value = " ".join(value.split()).strip()

    if value and value not in values:
        values.append(value)


def _as_list(value: Any) -> list[Any]:
    """JSON-LD lets almost any property be a single value or a list."""

    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def _types(node: Any) -> set[str]:
    """
    Return the schema.org types of a JSON-LD node as a set of strings.

    "@type" may be a string ("Person") or a list (["Organization", "NGO"]).
    Prefixed forms such as "schema:Person" are reduced to "Person".
    """

    if not isinstance(node, dict):
        return set()

    result: set[str] = set()

    for item in _as_list(node.get("@type")):
        if isinstance(item, str):
            result.add(item.rsplit(":", 1)[-1].rsplit("/", 1)[-1])

    return result


def _add_party(
    value: Any,
    people: list[str],
    organizations: list[str],
    default: str,
) -> None:
    """
    Add an author/publisher-style property to people or organizations.

    `value` may be a name string, a node, or a list of either. A node with
    no usable "@type" is filed under `default`.
    """

    for item in _as_list(value):
        if isinstance(item, str):
            target = people if default == "person" else organizations
            _add_unique(target, item)
            continue

        if not isinstance(item, dict):
            continue

        types = _types(item)

        if types & PERSON_TYPES:
            _add_unique(people, item.get("name"))

        elif types & ORGANIZATION_TYPES:
            _add_unique(organizations, item.get("name"))

        elif not types:
            target = people if default == "person" else organizations
            _add_unique(target, item.get("name"))


def _extract_from_json(
    data: Any,
    people: list[str],
    organizations: list[str],
    locations: list[str],
) -> None:
    """
    Recursively inspect JSON-LD data for common entity fields.
    """

    if isinstance(data, list):
        for item in data:
            _extract_from_json(
                item,
                people,
                organizations,
                locations,
            )

        return

    if not isinstance(data, dict):
        return

    _add_party(data.get("author"), people, organizations, "person")
    _add_party(data.get("publisher"), people, organizations, "organization")

    # Entity represented directly
    types = _types(data)

    if types & ORGANIZATION_TYPES:
        _add_unique(organizations, data.get("name"))

    # Location
    for location in _as_list(data.get("location")):
        if isinstance(location, dict):
            _add_unique(locations, location.get("name"))

        elif isinstance(location, str):
            _add_unique(locations, location)

    # Recursive inspection
    for value in data.values():
        if isinstance(value, (dict, list)):
            _extract_from_json(
                value,
                people,
                organizations,
                locations,
            )


def extract_entities(
    html: str,
) -> dict[str, list[str]]:
    """
    Extract people, organizations and locations
    primarily from structured JSON-LD data.

    A malformed or unexpected JSON-LD block is skipped; it must never
    cause the whole page to fail.
    """

    people: list[str] = []
    organizations: list[str] = []
    locations: list[str] = []

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    scripts = soup.find_all(
        "script",
        attrs={"type": "application/ld+json"},
    )

    for script in scripts:
        # .string is None when the tag has more than one child node.
        raw = script.get_text()

        if not raw or not raw.strip():
            continue

        try:
            data = json.loads(raw)

        except (json.JSONDecodeError, TypeError):
            continue

        try:
            _extract_from_json(
                data,
                people,
                organizations,
                locations,
            )

        except Exception:
            logger.warning(
                "Skipping JSON-LD block that could not be processed.",
                exc_info=True,
            )

    return {
        "people": people,
        "organizations": organizations,
        "locations": locations,
    }
