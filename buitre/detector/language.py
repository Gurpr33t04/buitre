import re


# (code, first code point, last code point)
_SCRIPTS = [
    ("pa", 0x0A00, 0x0A7F),  # Gurmukhi
    ("gu", 0x0A80, 0x0AFF),
    ("hi", 0x0900, 0x097F),  # Devanagari (hi / mr / ne share it)
    ("bn", 0x0980, 0x09FF),
    ("ta", 0x0B80, 0x0BFF),
    ("te", 0x0C00, 0x0C7F),
    ("kn", 0x0C80, 0x0CFF),
    ("ml", 0x0D00, 0x0D7F),
    ("ar", 0x0600, 0x06FF),
    ("he", 0x0590, 0x05FF),
    ("th", 0x0E00, 0x0E7F),
    ("el", 0x0370, 0x03FF),
    ("ru", 0x0400, 0x04FF),
    ("ko", 0xAC00, 0xD7AF),
    ("kana", 0x3040, 0x30FF),
    ("han", 0x3400, 0x9FFF),
]

_STOPWORDS = {
    "en": "the and of to in is that for it with as on was are this be by "
          "from or an at not have you we our your".split(),
    "fr": "le la les des et en un une est que pour dans qui sur pas avec "
          "au du ce nous vous sont".split(),
    "de": "der die das und ist nicht mit den von zu ein eine für auf im "
          "sich auch wir sie".split(),
    "es": "el la los las y de en que es un una por con para no se del al "
          "su como nos".split(),
    "it": "il lo la i gli le e di che un una per con non è sono del della "
          "nel in si".split(),
    "pt": "o a os as e de que em um uma para com não se do da dos das na "
          "no por".split(),
    "nl": "de het een en van in is dat op te voor met niet zijn aan ook "
          "als bij".split(),
}

_LANG_TAG_RE = re.compile(r"^[A-Za-z]{2,3}(?:[-_][A-Za-z0-9]{2,8})*$")
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def normalize_declared_language(value: str | None) -> str | None:
    """Return a clean BCP-47-ish tag from lang="..." or None if junk."""

    if not value:
        return None

    value = value.strip()

    if not _LANG_TAG_RE.match(value):
        return None

    return value.replace("_", "-")


def detect_language(
    text: str,
    declared: str | None = None,
) -> str | None:
    """
    Best-effort language code without external models.

    A valid declared language (<html lang>) always wins. Otherwise the
    dominant Unicode script decides (this reliably separates Gurmukhi,
    Devanagari, CJK, Arabic, Cyrillic...), and Latin-script text is
    resolved by stop-word voting. Returns None when unsure.
    """

    declared = normalize_declared_language(declared)

    if declared:
        return declared

    sample = (text or "")[:6000]

    counts: dict[str, int] = {}
    latin = 0

    for char in sample:
        if not char.isalpha():
            continue

        code = ord(char)

        if code < 0x250:
            latin += 1
            continue

        for name, low, high in _SCRIPTS:
            if low <= code <= high:
                counts[name] = counts.get(name, 0) + 1
                break

    total = latin + sum(counts.values())

    if total < 8:
        return None

    if counts:
        script, count = max(counts.items(), key=lambda item: item[1])

        if count / total >= 0.3:
            if script in {"kana", "han"}:
                kana = counts.get("kana", 0)
                han = counts.get("han", 0)

                if kana and kana / max(kana + han, 1) >= 0.05:
                    return "ja"

                return "zh"

            return script

    words = [w.lower() for w in _WORD_RE.findall(sample)]

    if len(words) < 6:
        return None

    scores = {
        lang: sum(1 for w in words if w in vocabulary)
        for lang, vocabulary in (
            (lang, set(vocab)) for lang, vocab in _STOPWORDS.items()
        )
    }

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best, best_score = ranked[0]
    second_score = ranked[1][1]

    if best_score >= 3 and best_score >= second_score * 1.3:
        return best

    return None
