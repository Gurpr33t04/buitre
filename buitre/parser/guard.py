import re


# Real pages rarely nest beyond ~100 levels; legacy table layouts a bit
# more. Parsing cost grows roughly with depth squared, so a hostile or
# broken document with thousands of levels can stall a worker for minutes.
MAX_NESTING = 600

_TAG_RE = re.compile(r"<(/?)([A-Za-z][A-Za-z0-9]*)\b[^>]*?(/?)>")

# Void elements never open a level.
_VOID = {
    "br", "img", "hr", "meta", "link", "input", "area", "base", "col",
    "embed", "param", "source", "track", "wbr", "frame",
}

# Elements whose end tag HTML allows you to omit: old pages leave
# thousands of <p>, <li>, <td> unclosed and that is harmless, so they
# are not counted as nesting.
_IMPLIED_END = {
    "p", "li", "dt", "dd", "td", "th", "tr", "thead", "tbody", "tfoot",
    "option", "optgroup", "colgroup", "html", "head", "body",
}


def max_nesting(html: str) -> int:
    """Deepest tag nesting of `html`, in one linear pass."""

    depth = 0
    deepest = 0

    for closing, name, self_closed in _TAG_RE.findall(html):
        name = name.lower()

        if name in _VOID or name in _IMPLIED_END or self_closed:
            continue

        if closing:
            depth = max(depth - 1, 0)
        else:
            depth += 1
            deepest = max(deepest, depth)

    return deepest
