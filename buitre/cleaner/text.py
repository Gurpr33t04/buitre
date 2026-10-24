import copy
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, NavigableString, Tag

from buitre.utils.cfemail import decode_cfemail


# NOTE: "form" is intentionally NOT here. Legacy ASP.NET (WebForms) pages
# wrap the entire page in a single <form>, so forms are pruned selectively
# by _prune_forms() instead of being removed wholesale.
REMOVE_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "nav",
    "header",
    "footer",
    "aside",
}

# Elements whose text is never page content.
SKIP_TEXT_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "head",
    "textarea",
    "select",
    "option",
    "button",
}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

# Tags that start a new line/paragraph of text. Everything else is inline.
BLOCK_TAGS = HEADING_TAGS | {
    "html", "body", "p", "div", "li", "ul", "ol", "dl", "dt", "dd",
    "table", "caption", "thead", "tbody", "tfoot", "tr", "td", "th",
    "section", "article", "main", "blockquote", "pre", "form",
    "fieldset", "legend", "address", "center", "hr", "figure",
    "figcaption", "details", "summary", "menu",
}

# Inline formatting tags are glued to their neighbours ("H<sub>2</sub>O"
# -> "H2O"). Any other inline tag (span, a, label, ...) is treated as a
# soft word boundary, because minified modern markup routinely puts
# <span>Title</span><span>Description</span> side by side.
INLINE_JOIN_TAGS = {
    "b", "i", "em", "strong", "u", "s", "strike", "small", "big", "mark",
    "sub", "sup", "abbr", "acronym", "code", "tt", "kbd", "samp", "var",
    "font", "cite", "q", "dfn", "ins", "del", "bdi", "bdo", "wbr",
}

# Strict, semantic markers for "this is the page's main content". Generic
# wrappers (id="content", class="container") are deliberately NOT here:
# they cover the whole page and would carry no signal.
MAIN_TAGS = {"main", "article"}
MAIN_IDS = {
    "main", "main-content", "maincontent", "article-body",
    "post-content", "entry-content", "story-body",
}
MAIN_CLASSES = {
    "entry-content", "post-content", "article-body", "article__body",
    "article-content", "story-body", "post-body", "mw-parser-output",
}


def _is_main_marker(tag: Tag) -> bool:
    if tag.name in MAIN_TAGS:
        return True

    attrs = tag.attrs or {}

    if str(attrs.get("role", "")).lower() == "main":
        return True

    if str(attrs.get("itemprop", "")).lower() == "articlebody":
        return True

    if str(attrs.get("id", "")).lower() in MAIN_IDS:
        return True

    classes = attrs.get("class") or []

    if isinstance(classes, str):
        classes = classes.split()

    return any(c.lower() in MAIN_CLASSES for c in classes)


# Elements whose class/id says "this is not the page's content". Exact
# token matches only ("sidebar", not "sidebar-wrapper-content"), so a
# content wrapper is never removed by accident.
NOISE_TOKENS = {
    # discussion / social
    "comments", "comment-list", "comments-area", "comment-respond",
    "comment-form", "respond", "disqus_thread", "sharedaddy",
    "share-buttons", "social-share", "social-links", "addtoany",
    # recommendations / ads / consent
    "related-posts", "jp-relatedposts", "related-articles",
    "advertisement", "adsbygoogle", "ad-container", "ad-slot",
    "sponsored", "cookie-banner", "cookie-notice", "cookie-consent",
    "cookie-bar", "cc-window", "gdpr", "newsletter", "newsletter-signup",
    # page chrome
    "sidebar", "widget-area", "breadcrumb", "breadcrumbs", "pagination",
    "footer", "site-footer", "page-footer", "site-header", "topbar",
    "navbar", "navigation", "menu", "main-menu", "skip-link",
    "screen-reader-text", "sr-only", "visually-hidden",
}

# ARIA landmarks that are page chrome (same meaning as nav/aside/...).
NOISE_ROLES = {
    "navigation", "banner", "contentinfo", "complementary", "search",
}

_HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden", re.IGNORECASE
)


def _is_noise_region(tag: Tag) -> bool:
    attrs = tag.attrs or {}

    if str(attrs.get("role", "")).lower() in NOISE_ROLES:
        return True

    if "hidden" in attrs or _HIDDEN_STYLE_RE.search(
        str(attrs.get("style", ""))
    ):
        return True

    if str(attrs.get("id", "")).lower() in NOISE_TOKENS:
        return True

    classes = attrs.get("class") or []

    if isinstance(classes, str):
        classes = classes.split()

    return any(c.lower() in NOISE_TOKENS for c in classes)


_BR = "\ue000"  # private-use marker for <br>; never appears in real text

# Sentence terminators: Latin, CJK full-width, Devanagari/Gurmukhi danda,
# Arabic question mark.
SENTENCE_END_CHARS = ".!?\u3002\uff01\uff1f\u0964\u0965\u061f"
_SENTENCE_END_RE = re.compile(f"[{SENTENCE_END_CHARS}]")
_SENTENCE_SPLIT_RE = re.compile(
    r"(?<=[.!?])\s+|(?<=[\u3002\uff01\uff1f\u0964\u0965\u061f])\s*"
)

_CJK_RE = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]"
)

# "Label: value", e-mail addresses, or a run of digits that looks like a
# phone/ID number. Short lines carrying these are data, not UI labels.
_DATA_SIGNAL_RE = re.compile(
    r"@"
    r"|\d[\d\s().+-]{5,}\d"
    r"|\b\w+(?: \w+){0,3}\s*:\s*\S{2,}"
)

MIN_TEXT_LENGTH = 35
MIN_DATA_LINE_LENGTH = 12
MIN_CJK_LENGTH = 12
MAX_BLOCK_LENGTH = 400

# Page-chrome phrases. Matched on word boundaries; see is_noise_block().
NOISE_PHRASES = [
    "skip to content",
    "skip navigation",
    "cookie settings",
    "privacy policy",
    "terms of use",
    "terms of service",
    "subscribe to our newsletter",
    "sign in",
    "log in",
    "menu",
    "navigation",
    "back to top",
    # Legacy-site chrome
    "best viewed (?:in|with|using)",
    "(?:hit|visitor|page) counter",
    "you are visitor",
    "visitors?\\s*:\\s*[\\d,]+",
    "(?:page )?last (?:updated|modified)",
    "powered by",
    # "All rights reserved" in common languages
    "all rights reserved",
    "tous droits r\u00e9serv\u00e9s",
    "todos los derechos reservados",
    "alle rechte vorbehalten",
    "\u7248\u6743\u6240\u6709",
    "\u0a38\u0a3e\u0a30\u0a47 \u0a05\u0a27\u0a3f\u0a15\u0a3e\u0a30 \u0a30\u0a3e\u0a16\u0a35\u0a47\u0a02 \u0a39\u0a28",
    "\u0938\u0930\u094d\u0935\u093e\u0927\u093f\u0915\u093e\u0930 \u0938\u0941\u0930\u0915\u094d\u0937\u093f\u0924",
]
_NOISE_PHRASE_RE = re.compile(
    r"(?<!\w)(?:" + "|".join(NOISE_PHRASES) + r")(?!\w)",
    re.IGNORECASE,
)

# Rendering-fallback notices: always noise, whatever the block length.
NOISE_ALWAYS = [
    "fallback because interactive scripts did not run",
    "possible causes include disabled javascript",
    "failure to load scripts or stylesheets",
]

# A block that is short and contains a chrome phrase is page chrome.
# A long block that merely *mentions* "menu" or "navigation" is content.
NOISE_SHORT_WORDS = 10


def normalize_text(text: str) -> str:
    """
    Normalize whitespace, broken dates, and punctuation spacing.
    """

    # Normalize common Unicode spaces.
    text = text.replace("\xa0", " ")

    # Collapse repeated whitespace.
    text = re.sub(r"\s+", " ", text)

    # Normalize broken ISO-style dates.
    # Handles:
    # 2026- 10-01
    # 2026- 10- 01
    # 2026 - 10 - 01
    # 2026– 10 – 01
    text = re.sub(
        r"\b(\d{4})\s*[-–—]\s*"
        r"(\d{1,2})\s*[-–—]\s*"
        r"(\d{1,2})\b",
        r"\1-\2-\3",
        text,
    )

    # Remove spaces before punctuation.
    text = re.sub(
        r"\s+([,.;!?])",
        r"\1",
        text,
    )

    return text.strip()


@dataclass
class _Run:
    """One paragraph-like run of text plus link statistics."""

    text: str
    link_ratio: float = 0.0
    link_count: int = 0
    is_heading: bool = False
    in_main: bool = False


def _visible_controls(form: Tag) -> int:
    count = 0

    for control in form.find_all(["input", "select", "textarea", "button"]):
        if (
            control.name == "input"
            and (control.get("type") or "text").lower() == "hidden"
        ):
            continue

        count += 1

    return count


def _form_text_length(form: Tag) -> int:
    total = 0

    for string in form.find_all(string=True):
        if type(string) is not NavigableString:
            continue

        if string.find_parent(
            ["script", "style", "select", "textarea", "button", "option"]
        ):
            continue

        total += len(string.strip())

    return total


def _is_data_entry_form(form: Tag) -> bool:
    """
    Decide whether a <form> is UI (login box, search box, filter bar)
    rather than a wrapper around real page content.

    ASP.NET WebForms pages put the whole page inside one <form> that
    holds hidden state fields (__VIEWSTATE) and lots of text, so:

    * no visible controls at all -> it cannot be a data-entry form, keep;
    * otherwise it is UI unless there is plenty of text per control
      (150+ characters each).
    """

    controls = _visible_controls(form)

    if controls == 0:
        return False

    return controls * 150 > _form_text_length(form)


def _prune_forms(document: BeautifulSoup) -> None:
    for form in document.find_all("form"):
        if getattr(form, "decomposed", False):
            continue

        if _is_data_entry_form(form):
            form.decompose()


def _clean_document(soup: BeautifulSoup) -> BeautifulSoup:
    """Return a copy of the soup with page chrome removed."""

    # A structural copy is ~2.5x faster than serialising and re-parsing,
    # and the cleaner runs twice per page.
    document = copy.copy(soup)

    for tag in document.find_all(REMOVE_TAGS):
        if getattr(tag, "decomposed", False):
            continue

        tag.decompose()

    for tag in document.find_all(True):
        if getattr(tag, "decomposed", False) or tag.name in ("html", "body"):
            continue

        if _is_noise_region(tag):
            tag.decompose()

    _prune_forms(document)

    return document


def _iter_runs(
    root: Tag,
    include_headings: bool = False,
) -> list[_Run]:
    """
    Walk the DOM once and return runs of text in document order.

    A run is the text between two block-level boundaries, so text that
    sits directly inside a <td>, <div>, <center> or <body> (legacy table
    layouts) is captured exactly like text inside a <p>. Consecutive
    <br><br> start a new run; a single <br> is just a line wrap.
    """

    runs: list[_Run] = []
    parts: list[str] = []
    link_chars = 0
    link_count = 0
    anchor_depth = 0
    main_depth = 0

    def flush(heading: bool = False) -> None:
        nonlocal parts, link_chars, link_count

        raw = "".join(parts)
        chars = link_chars
        links = link_count

        parts = []
        link_chars = 0
        link_count = 0

        if not raw.strip():
            return

        total = max(len(normalize_text(raw.replace(_BR, " "))), 1)
        ratio = min(chars / total, 1.0)

        for paragraph in re.split(rf"(?:{_BR}\s*){{2,}}", raw):
            text = normalize_text(paragraph.replace(_BR, " "))

            if text:
                runs.append(
                    _Run(
                        text=text,
                        link_ratio=ratio,
                        link_count=links,
                        is_heading=heading,
                        in_main=main_depth > 0,
                    )
                )

    sentinel = object()

    # Iterative walk: legacy pages can nest deeper than Python's
    # recursion limit.
    stack = [(iter(root.children), None, False)]

    while stack:
        iterator, name, is_main = stack[-1]
        child = next(iterator, sentinel)

        if child is sentinel:
            stack.pop()

            if name is None:
                continue

            if name == "a":
                anchor_depth -= 1

            if name in BLOCK_TAGS:
                flush(heading=name in HEADING_TAGS)
            elif name not in INLINE_JOIN_TAGS:
                parts.append(" ")

            if is_main:
                main_depth -= 1

            continue

        if isinstance(child, Tag):
            tag_name = child.name

            if tag_name in SKIP_TEXT_TAGS:
                continue

            if tag_name in HEADING_TAGS and not include_headings:
                flush()
                continue

            if tag_name == "br":
                parts.append(_BR)
                continue

            encoded = child.attrs.get("data-cfemail") if child.attrs else None

            if encoded:
                # Cloudflare replaced the address with "[email protected]".
                parts.append(" " + (decode_cfemail(encoded) or "") + " ")
                continue

            if tag_name in BLOCK_TAGS:
                flush()
            elif tag_name not in INLINE_JOIN_TAGS:
                parts.append(" ")

            if tag_name == "a":
                anchor_depth += 1
                link_count += 1

            marker = _is_main_marker(child)

            if marker:
                main_depth += 1

            stack.append((iter(child.children), tag_name, marker))
            continue

        # Plain text only: comments, doctypes, CDATA etc. are subclasses.
        if type(child) is NavigableString:
            text = str(child)
            parts.append(text)

            if anchor_depth:
                link_chars += len(text.strip())

    flush()

    return runs


def extract_readable_text(
    soup: BeautifulSoup,
) -> str:
    """
    Extract readable page text for the HTML parser.

    Text is block-aware: one line per paragraph/cell/heading, and inline
    markup does not split words ("H<sub>2</sub>O" -> "H2O").
    """

    document = _clean_document(soup)

    runs = _iter_runs(document, include_headings=True)

    return "\n".join(run.text for run in runs)


def clean_content_suffixes(text: str) -> str:
    """
    Remove common UI fragments attached to useful text.
    """

    suffixes = [
        "Learn more",
        "Read more",
        "View more",
        "More information",
        "Get started",
        "Visit the Documentation page",
    ]

    text = text.strip()

    changed = True

    while changed:
        changed = False

        for suffix in suffixes:
            pattern = rf"\s*{re.escape(suffix)}\s*\.?$"

            cleaned = re.sub(
                pattern,
                "",
                text,
                flags=re.IGNORECASE,
            ).strip()

            if cleaned != text:
                text = cleaned
                changed = True

    return text


def split_long_text(
    text: str,
    max_length: int = MAX_BLOCK_LENGTH,
) -> list[str]:
    """
    Split long paragraphs into readable sentence groups.
    """

    if len(text) <= max_length:
        return [text]

    sentences = _SENTENCE_SPLIT_RE.split(text)

    blocks: list[str] = []
    current = ""

    for sentence in sentences:
        sentence = sentence.strip()

        if not sentence:
            continue

        if not current:
            current = sentence
            continue

        candidate = f"{current} {sentence}"

        if len(candidate) <= max_length:
            current = candidate
        else:
            blocks.append(current)
            current = sentence

    if current:
        blocks.append(current)

    return blocks


def is_link_heavy(tag: Tag) -> bool:
    """
    Detect navigation-like content.
    """

    text = normalize_text(
        tag.get_text(
            " ",
            strip=True,
        )
    )

    if not text:
        return True

    links = tag.find_all("a")

    if not links:
        return False

    link_text_length = sum(
        len(
            normalize_text(
                link.get_text(
                    " ",
                    strip=True,
                )
            )
        )
        for link in links
    )

    ratio = link_text_length / max(len(text), 1)

    return (
        len(links) >= 3
        and ratio >= 0.45
    )


def _word_count(text: str) -> int:
    """
    Word count that also works for scripts written without spaces
    (Chinese, Japanese, Korean): about two characters per word.
    """

    count = 0

    for token in text.split():
        cjk_chars = len(_CJK_RE.findall(token))

        if cjk_chars:
            count += max(1, round(cjk_chars / 2))
        else:
            count += 1

    return count


def _has_data_signal(text: str) -> bool:
    """True for short lines such as 'Phone: 0161 496 0000'."""

    return bool(_DATA_SIGNAL_RE.search(text))


def _min_length(text: str) -> int:
    if _CJK_RE.search(text):
        return MIN_CJK_LENGTH

    if _has_data_signal(text):
        return MIN_DATA_LINE_LENGTH

    return MIN_TEXT_LENGTH


def is_noise_block(text: str) -> bool:
    """
    Remove known UI, rendering, and boilerplate text.

    Chrome phrases such as "menu" or "privacy policy" only mark a block
    as noise when the block is short, or when several of them appear
    together (a footer link row). A full sentence that merely mentions a
    restaurant menu or a car's navigation system is content.
    """

    lowered = text.casefold()

    for phrase in NOISE_ALWAYS:
        if phrase in lowered:
            return True

    words = _word_count(text)

    phrase_hits = {
        match.group(0).casefold()
        for match in _NOISE_PHRASE_RE.finditer(text)
    }

    if len(phrase_hits) >= 2:
        return True

    if phrase_hits and words <= NOISE_SHORT_WORDS:
        return True

    # Short fragments without sentence punctuation are usually labels or
    # navigation -- unless they carry data (phone, e-mail, "Label: value").
    if (
        words <= 8
        and not _SENTENCE_END_RE.search(text)
        and not _has_data_signal(text)
    ):
        return True

    return False


@dataclass
class Block:
    """A content block with the structural signals the scorer needs."""

    text: str
    index: int = 0
    in_main: bool = False
    link_ratio: float = 0.0


def extract_blocks(
    soup: BeautifulSoup,
) -> list[Block]:
    """
    Extract useful body-content blocks in document order, avoiding
    navigation, headings, rendering notices and link lists.

    Works on <p>/<li> markup and on legacy layouts where text sits
    directly in <td>, <div>, <center> or <body>. Each block records
    whether it sits inside the page's main region (<main>, <article>,
    role=main, itemprop=articleBody ...).
    """

    document = _clean_document(soup)

    blocks: list[Block] = []

    for run in _iter_runs(document, include_headings=False):

        if run.link_count >= 3 and run.link_ratio >= 0.45:
            continue

        text = normalize_text(run.text)
        text = clean_content_suffixes(text)
        text = normalize_text(text)

        if len(text) < _min_length(text):
            continue

        if is_noise_block(text):
            continue

        # A line that is entirely link text and not a sentence is a
        # teaser or menu entry ("Related: Why X is so hard"), even when
        # it holds only one link.
        if run.link_ratio >= 0.9 and not _SENTENCE_END_RE.search(text):
            continue

        for part in split_long_text(text):

            part = normalize_text(part)
            part = clean_content_suffixes(part)
            part = normalize_text(part)

            if len(part) < _min_length(part):
                continue

            if is_noise_block(part):
                continue

            blocks.append(
                Block(
                    text=part,
                    index=len(blocks),
                    in_main=run.in_main,
                    link_ratio=run.link_ratio,
                )
            )

    return blocks


def extract_content_blocks(
    soup: BeautifulSoup,
) -> list[str]:
    """Text-only view of extract_blocks()."""

    return [block.text for block in extract_blocks(soup)]
