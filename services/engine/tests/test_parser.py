"""Unit tests for HTML parsing and body decoding."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup
from seoagent.fetcher import decode_body, normalize_url, same_site
from seoagent.models import PageData
from seoagent.parser import (
    count_words,
    extract_main_text,
    parse_page,
    rel_tokens,
    strip_tracking_params,
)


def build(html: str, url: str = "https://example.com/page") -> PageData:
    page = PageData(url=url, final_url=url, status_code=200, content_type="text/html", html=html)
    return parse_page(page, "https://example.com")


# ------------------------------------------------------------------ decoding


def test_utf8_meta_beats_the_iso_8859_1_header_default():
    """Servers that send `text/html` with no charset make requests guess
    ISO-8859-1. The document's own declaration has to win, or Persian text
    turns into mojibake."""
    body = '<html><head><meta charset="utf-8"><title>سئو</title></head></html>'.encode()
    assert "سئو" in decode_body(body, "text/html")


def test_header_charset_is_honoured_when_present():
    body = "<html><body>café</body></html>".encode("windows-1252")
    assert "café" in decode_body(body, "text/html; charset=windows-1252")


def test_undecodable_body_degrades_instead_of_raising():
    assert isinstance(decode_body(b"\xff\xfe\x00bad", "text/html"), str)


# ---------------------------------------------------------------------- urls


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("HTTPS://Example.COM", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com/a#frag", "https://example.com/a"),
        ("https://example.com", "https://example.com/"),
    ],
)
def test_normalize_url(raw, expected):
    assert normalize_url(raw) == expected


def test_same_site_treats_www_as_the_same_site():
    assert same_site("https://www.example.com/a", "https://example.com")
    assert same_site("https://example.com/a", "https://www.example.com")
    assert not same_site("https://other.com/a", "https://example.com")


def test_subdomains_only_match_when_requested():
    assert not same_site("https://blog.example.com/a", "https://example.com")
    assert same_site("https://blog.example.com/a", "https://example.com", follow_subdomains=True)


def test_tracking_parameters_are_stripped():
    assert strip_tracking_params("https://e.com/a?utm_source=x&id=7") == "https://e.com/a?id=7"
    assert strip_tracking_params("https://e.com/a") == "https://e.com/a"


# ---------------------------------------------------------------- rel tokens


@pytest.mark.parametrize("markup", [
    '<link rel="canonical" href="/x">',
    "<link rel='canonical' href='/x'>",
    '<link rel="canonical alternate" href="/x">',
    '<link REL="CANONICAL" href="/x">',
])
def test_rel_tokens_handles_every_spelling(markup):
    tag = BeautifulSoup(markup, "lxml").find("link")
    assert "canonical" in rel_tokens(tag)


def test_canonical_is_resolved_to_an_absolute_url():
    page = build('<html><head><link rel="canonical" href="/real"></head><body>x</body></html>')
    assert page.canonical == "https://example.com/real"


def test_missing_rel_is_an_empty_set():
    tag = BeautifulSoup("<link href='/x'>", "lxml").find("link")
    assert rel_tokens(tag) == set()


# -------------------------------------------------------------- word counting


@pytest.mark.parametrize(
    "text,expected",
    [
        ("سلام دنیا", 2),
        ("hello world", 2),
        ("سئو یعنی search engine optimization", 5),
        ("", 0),
        ("!!! ??? ...", 0),
    ],
)
def test_count_words_handles_mixed_scripts(text, expected):
    assert count_words(text) == expected


# ------------------------------------------------------------ main extraction


def test_main_content_wins_over_navigation_boilerplate():
    html = (
        "<html><body>"
        "<nav>" + "منو " * 60 + "</nav>"
        "<main><p>" + "محتوای اصلی " * 60 + "</p></main>"
        "<footer>" + "فوتر " * 60 + "</footer>"
        "</body></html>"
    )
    text = extract_main_text(BeautifulSoup(html, "lxml"))
    assert "محتوای اصلی" in text
    assert "منو" not in text


def test_scripts_and_styles_never_count_as_content():
    page = build(
        "<html><body><script>var x = 'کلمه کلیدی جعلی';</script>"
        "<style>.a{color:red}</style><p>متن واقعی صفحه</p></body></html>"
    )
    assert "جعلی" not in page.text
    assert "متن واقعی" in page.text


# ------------------------------------------------------------------- parsing


def test_empty_heading_is_recorded_so_it_can_be_flagged():
    page = build("<html><body><h1></h1><h2>دو</h2></body></html>")
    assert page.h1s == [""]
    assert (2, "دو") in page.headings


def test_broken_json_ld_is_reported_not_dropped():
    page = build('<html><head><script type="application/ld+json">{oops,}</script></head><body>x</body></html>')
    assert page.jsonld and "__parse_error__" in page.jsonld[0]


def test_json_ld_graph_is_flattened():
    page = build(
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":[{"@type":"Article"},{"@type":"Person"}]}'
        "</script></head><body>x</body></html>"
    )
    types = {block.get("@type") for block in page.jsonld}
    assert {"Article", "Person"} <= types


def test_non_http_and_placeholder_links_are_skipped():
    page = build(
        '<html><body><a href="mailto:a@b.c">m</a><a href="javascript:void(0)">j</a>'
        '<a href="#x">f</a><a href="/real">r</a></body></html>'
    )
    assert [link.href for link in page.links] == ["https://example.com/real"]


def test_base_tag_changes_link_resolution():
    page = build('<html><head><base href="https://example.com/sub/"></head><body><a href="x">l</a></body></html>')
    assert page.links[0].href == "https://example.com/sub/x"


def test_deferred_scripts_are_not_render_blocking():
    page = build(
        "<html><head><script src='/a.js'></script><script src='/b.js' defer></script>"
        "<script src='/c.js' async></script></head><body>x</body></html>"
    )
    assert page.render_blocking_scripts == 1
    assert page.external_script_count == 3


def test_preloaded_stylesheet_is_not_render_blocking():
    page = build(
        "<html><head><link rel='stylesheet' href='/a.css'>"
        "<link rel='preload stylesheet' href='/b.css'>"
        "<link rel='stylesheet' href='/p.css' media='print'></head><body>x</body></html>"
    )
    assert page.render_blocking_styles == 1


def test_non_html_response_is_left_alone():
    page = PageData(url="https://example.com/a.pdf", content_type="application/pdf", html="%PDF")
    parse_page(page, "https://example.com")
    assert page.title is None
    assert page.word_count == 0


def test_noindex_is_detected_in_meta_and_header():
    meta = build('<html><head><meta name="robots" content="noindex, follow"></head><body>x</body></html>')
    assert meta.is_noindex and not meta.is_indexable

    header = PageData(
        url="https://example.com/", content_type="text/html", status_code=200,
        headers={"x-robots-tag": "noindex"}, html="<html><body>x</body></html>",
    )
    assert header.is_noindex
