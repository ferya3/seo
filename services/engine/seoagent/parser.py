"""Turn raw HTML into the structured :class:`PageData` the rules consume."""

from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from .fetcher import same_site
from .models import ImageRef, LinkRef, PageData

NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "svg", "iframe")
BOILERPLATE_TAGS = ("nav", "header", "footer", "aside", "form")

# Matches Latin words, Persian/Arabic words, and CJK characters (each counts as one).
_WORD_RE = re.compile(r"[A-Za-z0-9'’\-]+|[؀-ۿݐ-ݿ]+|[一-鿿]")

_TRACKING_PARAMS = re.compile(
    r"^(utm_[a-z]+|gclid|fbclid|msclkid|yclid|mc_[a-z]+|ref|source)$", re.I
)


def count_words(text: str) -> int:
    return len(_WORD_RE.findall(text))


def clean_text(node: Tag | BeautifulSoup) -> str:
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()


def _strip_noise(soup: BeautifulSoup) -> BeautifulSoup:
    for tag in soup.find_all(NON_CONTENT_TAGS):
        tag.decompose()
    return soup


def extract_main_text(soup: BeautifulSoup) -> str:
    """Best-effort main-content extraction, so word counts aren't inflated by
    navigation and footers. Falls back to the whole body."""
    body = soup.body or soup
    candidates: list[Tag] = []
    for selector in ("main", "article", "[role=main]", "#content", ".content", "#main"):
        candidates.extend(body.select(selector))

    best: Tag | None = None
    best_score = 0
    for node in candidates:
        score = count_words(clean_text(node))
        if score > best_score:
            best, best_score = node, score

    if best is not None and best_score >= 50:
        return clean_text(best)

    # No obvious main container: drop the usual boilerplate and use what's left.
    clone = BeautifulSoup(str(body), "lxml")
    for tag in clone.find_all(BOILERPLATE_TAGS):
        tag.decompose()
    text = clean_text(clone)
    return text if count_words(text) >= 30 else clean_text(body)


def rel_tokens(tag: Tag) -> set[str]:
    """Normalise a `rel` attribute to lowercase tokens.

    BeautifulSoup hands multi-valued attributes back as a list *or* a plain
    string depending on the parser and how the tag was written, so filtering on
    `rel` with a lambda silently misses half the cases. Always go through here.
    """
    value = tag.get("rel")
    if value is None:
        return set()
    if isinstance(value, str):
        return {token.lower() for token in value.split() if token}
    return {str(token).lower() for token in value if str(token)}


def _links_with_rel(soup: BeautifulSoup, rel: str) -> list[Tag]:
    return [tag for tag in soup.find_all("link") if rel in rel_tokens(tag)]


def _meta(soup: BeautifulSoup, **attrs: str) -> str | None:
    tag = soup.find("meta", attrs=attrs)
    if isinstance(tag, Tag):
        content = tag.get("content")
        if isinstance(content, str):
            return content.strip()
    return None


def _parse_jsonld(soup: BeautifulSoup) -> list[dict]:
    blocks: list[dict] = []
    for script in soup.find_all("script", attrs={"type": re.compile("application/ld\\+json", re.I)}):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # A trailing-comma / single-quote soup is common; record it as broken.
            blocks.append({"__parse_error__": raw[:300]})
            continue
        if isinstance(data, list):
            blocks.extend(d for d in data if isinstance(d, dict))
        elif isinstance(data, dict):
            if "@graph" in data and isinstance(data["@graph"], list):
                blocks.extend(d for d in data["@graph"] if isinstance(d, dict))
                meta = {k: v for k, v in data.items() if k != "@graph"}
                if len(meta) > 1:
                    blocks.append(meta)
            else:
                blocks.append(data)
    return blocks


def strip_tracking_params(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url
    kept = [
        pair
        for pair in parsed.query.split("&")
        if pair and not _TRACKING_PARAMS.match(pair.split("=", 1)[0])
    ]
    return parsed._replace(query="&".join(kept)).geturl()


def parse_page(page: PageData, origin: str, follow_subdomains: bool = False) -> PageData:
    """Populate every parsed field on ``page`` from ``page.html``."""
    if not page.html or not page.is_html:
        return page

    soup = BeautifulSoup(page.html, "lxml")
    base_url = page.final_url or page.url
    base_tag = soup.find("base", href=True)
    if isinstance(base_tag, Tag):
        base_url = urljoin(base_url, str(base_tag["href"]))

    # ------------------------------------------------------------------ head
    if soup.title and soup.title.string:
        page.title = soup.title.string.strip()
    page.meta_description = _meta(soup, name=re.compile("^description$", re.I))
    page.meta_robots = _meta(soup, name=re.compile("^robots$", re.I)) or ""
    if not page.meta_robots:
        page.meta_robots = _meta(soup, name=re.compile("^googlebot$", re.I)) or ""
    page.viewport = _meta(soup, name=re.compile("^viewport$", re.I))

    canonicals = _links_with_rel(soup, "canonical")
    if canonicals and canonicals[0].get("href"):
        page.canonical = urljoin(base_url, str(canonicals[0]["href"]).strip())

    html_tag = soup.find("html")
    if isinstance(html_tag, Tag):
        lang = html_tag.get("lang")
        page.lang = str(lang).strip() if lang else None

    charset_tag = soup.find("meta", attrs={"charset": True})
    if isinstance(charset_tag, Tag):
        page.charset = str(charset_tag["charset"])

    for link in _links_with_rel(soup, "alternate"):
        hreflang = link.get("hreflang")
        href = link.get("href")
        if hreflang and href:
            page.hreflang.append((str(hreflang).strip(), urljoin(base_url, str(href).strip())))

    for link in soup.find_all("link"):
        if any("icon" in token for token in rel_tokens(link)) and link.get("href"):
            page.favicon = urljoin(base_url, str(link["href"]))
            break

    for tag in soup.find_all("meta", attrs={"property": re.compile("^og:", re.I)}):
        page.og[str(tag["property"]).lower()] = str(tag.get("content", "")).strip()
    for tag in soup.find_all("meta", attrs={"name": re.compile("^twitter:", re.I)}):
        page.twitter[str(tag["name"]).lower()] = str(tag.get("content", "")).strip()

    page.jsonld = _parse_jsonld(soup)
    page.microdata_types = [
        str(t["itemtype"]).rsplit("/", 1)[-1]
        for t in soup.find_all(attrs={"itemtype": True})
        if isinstance(t, Tag)
    ]

    # ------------------------------------------------------ performance hints
    for script in soup.find_all("script"):
        src = script.get("src")
        if src:
            page.external_script_count += 1
            in_head = script.find_parent("head") is not None
            if in_head and not script.has_attr("async") and not script.has_attr("defer") \
                    and str(script.get("type", "")).lower() != "module":
                page.render_blocking_scripts += 1
        else:
            page.inline_script_bytes += len(script.get_text() or "")
    for style in soup.find_all("style"):
        page.inline_style_bytes += len(style.get_text() or "")
    for link in soup.find_all("link"):
        tokens = rel_tokens(link)
        if "stylesheet" not in tokens or "preload" in tokens:
            continue
        media = str(link.get("media", "")).lower()
        if media in ("", "all", "screen"):
            page.render_blocking_styles += 1
    page.iframe_count = len(soup.find_all("iframe"))

    # ------------------------------------------------------------------ body
    for index, img in enumerate(soup.find_all("img")):
        src = img.get("src") or img.get("data-src") or ""
        alt = img.get("alt")
        page.images.append(
            ImageRef(
                src=urljoin(base_url, str(src)) if src else "",
                alt=str(alt) if alt is not None else None,
                width=str(img["width"]) if img.get("width") else None,
                height=str(img["height"]) if img.get("height") else None,
                loading=str(img["loading"]) if img.get("loading") else None,
                is_in_first_viewport=index < 3,
            )
        )

    for anchor in soup.find_all("a", href=True):
        raw = str(anchor["href"]).strip()
        if raw.startswith(("javascript:", "mailto:", "tel:", "#", "sms:", "data:")):
            continue
        absolute = urljoin(base_url, raw)
        if urlparse(absolute).scheme not in ("http", "https"):
            continue
        rel = anchor.get("rel") or []
        page.links.append(
            LinkRef(
                href=absolute,
                raw_href=raw,
                anchor=clean_text(anchor)[:200],
                rel=" ".join(rel) if isinstance(rel, list) else str(rel),
                is_internal=same_site(absolute, origin, follow_subdomains),
            )
        )

    headings_soup = BeautifulSoup(page.html, "lxml")
    _strip_noise(headings_soup)
    for tag in headings_soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        # Empty headings are recorded too — an empty <h1> is its own finding,
        # and dropping it would make the page look like it has no H1 at all.
        page.headings.append((int(tag.name[1]), clean_text(tag)))

    page.text = extract_main_text(_strip_noise(BeautifulSoup(page.html, "lxml")))
    page.word_count = count_words(page.text)
    # A change detector, not a signature: two crawls of the same text produce
    # the same hash so consumers can skip work. `usedforsecurity=False` says
    # that out loud, and lets this run on a FIPS build where sha1 is refused.
    page.content_hash = hashlib.sha1(
        re.sub(r"\W+", " ", page.text.lower()).strip().encode("utf-8"),
        usedforsecurity=False,
    ).hexdigest()

    return page
