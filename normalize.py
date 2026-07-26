"""Title and company-name normalization for dedup comparison.

Job titles and company names drift between sources — "Sr." vs "Senior",
"Scribd Inc" vs "Scribd", "CNN (Warner Bros. Discovery)" vs "CNN". These
helpers canonicalize both sides so an exact-string match actually works.
"""

import re

_ABBREV = [
    (r"\bsr\.?\b", "senior"),
    (r"\bjr\.?\b", "junior"),
    (r"\bmgr\b", "manager"),
    (r"\btpm\b", "technical program manager"),
    (r"\bepm\b", "engineering program manager"),
    (r"\beng\b", "engineering"),
    (r"\bops\b", "operations"),
    (r"\binfra\b", "infrastructure"),
    (r"\bml\b", "machine learning"),
    (r"\bai/ml\b", "ai machine learning"),
]
_PUNCT_RX = re.compile(r"[.,\-/()&'\":;]+")
_LEVEL_SUFFIX_RX = re.compile(r"\s+(?:ii|iii|iv|1|2|3)$")
_WS_RX = re.compile(r"\s+")


def normalize_title(raw: str) -> str:
    """Lowercase, expand abbreviations, strip punctuation and trailing level
    suffixes so title strings from different sources compare equal."""
    s = (raw or "").lower().strip()
    for pat, repl in _ABBREV:
        s = re.sub(pat, repl, s)
    s = _PUNCT_RX.sub(" ", s)
    s = _WS_RX.sub(" ", s).strip()
    s = _LEVEL_SUFFIX_RX.sub("", s)
    return s


# Trailing corporate-suffix tokens stripped from company names before dedup.
_COMPANY_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "ltd", "limited",
    "co", "corp", "corporation", "company",
    "technologies", "technology", "tech",
    "software", "systems", "solutions", "labs", "app", "apps",
    "group", "holdings",
}

# Trailing parenthetical qualifier, e.g. "CNN (Warner Bros. Discovery)".
# Must be stripped BEFORE _PUNCT_RX runs, which would otherwise eat the
# brackets and leave the qualifier behind as bare tokens. Anchored to the end
# only: a leading/mid-name parenthetical is left alone.
_COMPANY_TRAILING_PAREN_RX = re.compile(r"\s*\([^()]*\)\s*$")


def normalize_company(raw: str) -> str:
    """Canonicalize a company name for dedup comparison.

    Lowercases, strips trailing parenthetical qualifiers, collapses
    punctuation/whitespace, then strips trailing corporate-suffix tokens so
    "Scribd Inc" == "Scribd" and "Smart Technologies" == "Smart". Falls back
    to the punctuation-collapsed string if stripping would leave nothing.
    """
    s = (raw or "").lower().strip()
    while True:  # iterative so "Foo (Bar) (Baz)" collapses fully
        stripped = _COMPANY_TRAILING_PAREN_RX.sub("", s).strip()
        if not stripped or stripped == s:
            break
        s = stripped
    s = _PUNCT_RX.sub(" ", s)
    s = _WS_RX.sub(" ", s).strip()
    if not s:
        return s
    tokens = s.split()
    while len(tokens) > 1 and tokens[-1] in _COMPANY_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)
