"""One spelling per Persian word, in one place.

Persian arrives spelled several ways for the same word: Arabic yeh and kaf are
the common ones, and a zero-width non-joiner sits inside words that a naive
split treats as one token. A page titled "کفش ورزشي" (Arabic yeh) would never
match the keyword "کفش ورزشی" (Persian yeh), and a coverage report would invent
work that is already done.

This started inside the content service. It moved here when the competitor
service needed the same rule: two copies of a normalisation table is two
answers to "does this page target this keyword", and the second copy is the one
nobody remembers to update. `services/content/analyze.py` re-exports these
names, so its own tests and callers still see them where they were.

Pure functions of a string: no network, no database, no clock.
"""

from __future__ import annotations

import re
import unicodedata

FOLD = {
    "ي": "ی",   # Arabic yeh    -> Persian yeh
    "ى": "ی",   # alef maksura  -> Persian yeh
    "ك": "ک",   # Arabic kaf    -> Persian kaf
    "ۀ": "ه",   # heh with yeh  -> heh
    "‌": " ",        # zero-width non-joiner: a word boundary for matching
    "‏": "",
    "‎": "",
    "ـ": "",         # tatweel, purely decorative
}
DIACRITICS = re.compile(r"[ً-ْٰ]")

# Persian punctuation lives inside the Arabic block, and the block is kept
# whole by NON_WORD below — so without this a comma stays glued to the word it
# follows and "مسابقه،" never matches "مسابقه". Latin punctuation never had
# this problem, which is why it went unnoticed until a real page was compared.
PUNCTUATION = re.compile(r"[،؍؛؞؟٪-٭۔۝]")

NON_WORD = re.compile(r"[^\w؀-ۿ]+", re.UNICODE)

# Words too common to carry intent. Kept short on purpose: an aggressive stop
# list starts eating real query terms.
STOPWORDS = {
    "و", "در", "به", "از", "با", "برای", "را", "که", "این", "آن", "است", "های", "ها",
    "the", "a", "an", "of", "for", "and", "in", "on", "to",
}


def normalise(text: str | None) -> str:
    """One spelling per word, so matching means what it looks like it means."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    for source, target in FOLD.items():
        text = text.replace(source, target)
    text = DIACRITICS.sub("", text)
    text = PUNCTUATION.sub(" ", text)
    return NON_WORD.sub(" ", text).strip().casefold()


def words(text: str | None) -> list[str]:
    return [w for w in normalise(text).split() if w and w not in STOPWORDS]
