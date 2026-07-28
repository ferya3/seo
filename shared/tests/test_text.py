"""One spelling per Persian word.

This is the quietest thing in the system and the one with the widest blast
radius: every keyword match, every content gap and every competitor theme is
decided by whether two strings normalise to the same thing. When it is wrong
nothing crashes — the reports are simply, invisibly, about the wrong words.

    pytest shared/tests/test_text.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from shared.text import normalise, words  # noqa: E402


def test_arabic_letters_fold_to_persian_ones():
    # The whole reason this module exists: the same word, two keyboards.
    assert normalise("کفش ورزشي") == normalise("کفش ورزشی")
    assert normalise("كفش") == normalise("کفش")


def test_a_zero_width_non_joiner_is_a_word_boundary():
    assert words("نیم‌فاصله") == ["نیم", "فاصله"]


def test_diacritics_and_tatweel_are_decoration_not_spelling():
    assert normalise("کِتاب") == normalise("کتاب")
    assert normalise("کـــتاب") == normalise("کتاب")


def test_persian_punctuation_does_not_stay_glued_to_the_word():
    # Persian punctuation sits inside the Arabic unicode block, which the word
    # split keeps whole — so a comma used to travel with the word in front of
    # it and "مسابقه،" matched nothing. Found in a real competitor report.
    assert words("دویدن و مسابقه، با مقایسه") == ["دویدن", "مسابقه", "مقایسه"]
    assert words("چطور؟ اینطور!") == ["چطور", "اینطور"]
    assert words("۵۰٪ تخفیف") == ["۵۰", "تخفیف"]


def test_stopwords_are_dropped_but_real_terms_are_not():
    assert words("کفش برای دویدن در پارک") == ["کفش", "دویدن", "پارک"]


def test_latin_and_persian_are_normalised_the_same_way():
    assert words("Running Shoes, Reviewed") == ["running", "shoes", "reviewed"]


def test_nothing_in_is_nothing_out_rather_than_an_exception():
    assert normalise(None) == ""
    assert normalise("") == ""
    assert words(None) == []
