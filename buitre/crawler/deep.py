from urllib.parse import urlencode


MAX_SELECT_OPTIONS = 30


def enumerate_form_urls(
    forms: list[dict],
    per_form: int = 20,
    max_forms: int = 3,
) -> list[str]:
    """
    URLs reachable only through a form: one per option of a <select>.

    Conservative by design. Only GET forms are used (they are safe
    to repeat; nothing is submitted by POST), never forms with password
    or file inputs, and never forms that need free text we cannot know.
    Typical hits: "Department: [Physics, Chemistry, ...]", year/category
    filters, A-Z directories. This is "deep web" content a link crawler
    never sees, found without guessing credentials or bypassing anything.
    """

    urls: list[str] = []

    for form in forms[:max_forms]:
        if form.get("method") != "get":
            continue

        if form.get("has_password") or form.get("has_file"):
            continue

        fields = form.get("fields", [])

        selects = [
            f
            for f in fields
            if f.get("type") == "select"
            and f.get("name")
            and 2 <= len(f.get("options", [])) <= MAX_SELECT_OPTIONS
        ]

        if not selects:
            continue

        # Required free text we cannot fill: skip the whole form.
        if any(
            f.get("required")
            and f.get("type") in ("text", "search", "email", "textarea")
            and not f.get("value")
            for f in fields
        ):
            continue

        target = selects[0]

        base: list[tuple[str, str]] = []

        for field in fields:
            name = field.get("name")

            if not name or field is target:
                continue

            if field.get("type") == "select":
                if field.get("selected") not in (None, ""):
                    base.append((name, field["selected"]))

            elif field.get("type") in ("hidden", "text", "search"):
                if field.get("value"):
                    base.append((name, field["value"]))

        action = form["action"]
        separator = "&" if "?" in action else "?"

        count = 0

        for option in target["options"]:
            if option in (None, ""):
                continue

            query = urlencode(base + [(target["name"], option)])
            urls.append(f"{action}{separator}{query}")
            count += 1

            if count >= per_form:
                break

    return urls
