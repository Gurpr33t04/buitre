from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup


SEARCH_FIELD_NAMES = {"q", "s", "search", "query", "keyword", "keywords", "term"}
MAX_OPTIONS = 50


def extract_forms(
    soup: BeautifulSoup,
    base_url: str,
) -> list[dict[str, Any]]:
    """
    Describe every <form>: where it goes, how, and what it asks for.

    This is descriptive only. Nothing is submitted here; the crawler's
    optional deep mode decides which GET forms are safe to enumerate.
    """

    forms: list[dict[str, Any]] = []

    for form in soup.find_all("form"):
        method = (form.get("method") or "get").lower()
        action = urljoin(base_url, form.get("action") or base_url)

        fields: list[dict[str, Any]] = []
        has_password = False
        has_file = False

        for control in form.find_all(["input", "select", "textarea"]):
            name = control.get("name")

            if control.name == "input":
                kind = (control.get("type") or "text").lower()

                if kind == "password":
                    has_password = True

                if kind == "file":
                    has_file = True

                if kind in {"submit", "button", "image", "reset"}:
                    continue

                field: dict[str, Any] = {
                    "name": name,
                    "type": kind,
                    "value": control.get("value", ""),
                }

            elif control.name == "select":
                options = []
                selected = None

                for option in control.find_all("option"):
                    value = option.get("value")

                    if value is None:
                        value = option.get_text(strip=True)

                    options.append(value)

                    if option.has_attr("selected"):
                        selected = value

                field = {
                    "name": name,
                    "type": "select",
                    "options": options[:MAX_OPTIONS],
                    "selected": selected,
                }

            else:
                field = {"name": name, "type": "textarea", "value": ""}

            if control.has_attr("required"):
                field["required"] = True

            if name or field["type"] == "select":
                fields.append(field)

        names = {(f.get("name") or "").lower() for f in fields}

        forms.append(
            {
                "action": action,
                "method": method,
                "fields": fields,
                "has_password": has_password,
                "has_file": has_file,
                "is_search": (
                    form.get("role") == "search"
                    or bool(names & SEARCH_FIELD_NAMES)
                ),
            }
        )

    return forms[:20]
