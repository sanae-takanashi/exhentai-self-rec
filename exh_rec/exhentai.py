from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser


BASE_URL = "https://exhentai.org/"
GALLERY_RE = re.compile(r"(?:https?:)?(?://(?:exhentai|e-hentai)\.org)?/g/(\d+)/([0-9a-fA-F]+)/?")
TAG_RE = re.compile(r"https?://(?:exhentai|e-hentai)\.org/tag/([^\"'#?]+)|/tag/([^\"'#?]+)")
TAG_ATTR_RE = re.compile(
    r"\b(?:title|id)=[\"'](?:ta_)?((?:artist|character|cosplayer|female|group|language|male|mixed|other|parody|reclass):[^\"']+)[\"']",
    re.I,
)
IMG_RE = re.compile(r"<img\b[^>]*(?:data-src|src)=[\"']([^\"']+)[\"']", re.I)
CSS_URL_RE = re.compile(r"url\(\s*[\"']?([^\"')\s]+)[\"']?\s*\)", re.I)
CAT_RE = re.compile(r"class=[\"'][^\"']*\bcn\b[^\"']*[\"'][^>]*>(.*?)</", re.I | re.S)
UPLOADER_RE = re.compile(r"class=[\"'][^\"']*\bglhide\b[^\"']*[\"'][^>]*>(.*?)</", re.I | re.S)
RATING_RE = re.compile(r"Rating:\s*([0-9.]+)", re.I)
DETAIL_TITLE_RE = re.compile(r"id=[\"']gn[\"'][^>]*>(.*?)</", re.I | re.S)
DETAIL_CATEGORY_RE = re.compile(r"id=[\"']gdc[\"'][^>]*>.*?class=[\"'][^\"']*\bcn\b[^\"']*[\"'][^>]*>(.*?)</", re.I | re.S)
DETAIL_UPLOADER_RE = re.compile(r"id=[\"']gdn[\"'][^>]*>(.*?)</", re.I | re.S)
DETAIL_POSTED_RE = re.compile(r"<td[^>]*>\s*Posted:\s*</td>\s*<td[^>]*>(.*?)</td>", re.I | re.S)
DETAIL_AVERAGE_RE = re.compile(r"Average:\s*([0-9.]+)", re.I)


@dataclass
class Gallery:
    url: str
    title: str
    gid: str | None = None
    token: str | None = None
    category: str | None = None
    uploader: str | None = None
    posted_at: str | None = None
    thumb_url: str | None = None
    rating: float | None = None
    tags: list[str] = field(default_factory=list)
    source_query: str | None = None


class LinkTitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str, str]] = []
        self._href: str | None = None
        self._classes = ""
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attr = dict(attrs)
        href = attr.get("href")
        if href and "/g/" in href:
            self._href = href
            self._classes = attr.get("class") or ""
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self._href:
            text = html.unescape(" ".join(self._text)).strip()
            self.links.append((self._href, text, self._classes))
            self._href = None
            self._classes = ""
            self._text = []


def normalize_cookie_header(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw.lower().startswith("cookie:"):
        raw = raw.split(":", 1)[1].strip()
    table_pairs = parse_cookie_export(raw)
    if table_pairs:
        return "; ".join(f"{name}={value}" for name, value in table_pairs)
    return " ".join(raw.split())


def parse_cookie_export(raw: str) -> list[tuple[str, str]]:
    if ";" in raw and "\n" not in raw:
        return []
    pairs: list[tuple[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#") and not line.startswith("#HttpOnly_"):
            continue
        if "=" in line.split(None, 1)[0]:
            return []
        parts = line.split("\t")
        if len(parts) < 2:
            parts = line.split()
        if len(parts) < 2:
            return []
        if len(parts) >= 7 and looks_like_netscape_cookie_row(parts):
            name = parts[5].strip()
            value = parts[6].strip()
        else:
            name = parts[0].strip()
            value = parts[1].strip()
        if not is_cookie_name(name) or not value:
            return []
        pairs.append((name, value))
    return pairs if pairs else []


def looks_like_netscape_cookie_row(parts: list[str]) -> bool:
    domain = parts[0].removeprefix("#HttpOnly_").strip()
    include_subdomains = parts[1].strip().upper()
    path = parts[2].strip()
    secure = parts[3].strip().upper()
    expires = parts[4].strip()
    return (
        ("." in domain or domain in {"localhost", "127.0.0.1"})
        and include_subdomains in {"TRUE", "FALSE"}
        and path.startswith("/")
        and secure in {"TRUE", "FALSE"}
        and expires.isdigit()
    )


def is_cookie_name(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9_!#$%&'*+\-.^`|~]+", value))


def build_search_url(query: str | None = None, page: int = 0) -> str:
    params: dict[str, str] = {}
    if query:
        params["f_search"] = query
    if page:
        params["page"] = str(page)
    return BASE_URL + ("?" + urllib.parse.urlencode(params) if params else "")


def fetch_page(cookie_header: str, url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": normalize_cookie_header(cookie_header),
            "User-Agent": "exhentai-self-recommend/0.1 (+local personal recommender)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not fetch {url}: {exc.reason}") from exc


def fetch_galleries(cookie_header: str, query: str | None, pages: int = 1, delay: float = 1.0) -> list[Gallery]:
    galleries: list[Gallery] = []
    seen: set[str] = set()
    for page in range(max(1, pages)):
        page_html = fetch_page(cookie_header, build_search_url(query, page))
        for gallery in parse_gallery_list(page_html, source_query=query):
            if gallery.url not in seen:
                galleries.append(gallery)
                seen.add(gallery.url)
        if page + 1 < pages:
            time.sleep(delay)
    return galleries


def check_access(cookie_header: str) -> dict:
    page_html = fetch_page(cookie_header, BASE_URL, timeout=15)
    galleries = parse_gallery_list(page_html)
    lower_html = page_html.lower()
    if galleries:
        return {"ok": True, "gallery_count": len(galleries), "message": f"Access ok; saw {len(galleries)} galleries"}
    if "exhentai.org" in lower_html and ("login" in lower_html or "sad panda" in lower_html):
        return {"ok": False, "gallery_count": 0, "message": "Cookie did not expose gallery listings"}
    return {"ok": False, "gallery_count": 0, "message": "Fetched page, but no gallery listings were found"}


def fetch_gallery_detail(cookie_header: str, gallery: Gallery, delay: float = 1.0) -> Gallery:
    if delay:
        time.sleep(delay)
    page_html = fetch_page(cookie_header, gallery.url)
    detail = parse_gallery_detail(page_html, gallery.url)
    return merge_gallery(gallery, detail)


def parse_gallery_list(page_html: str, source_query: str | None = None) -> list[Gallery]:
    parser = LinkTitleParser()
    parser.feed(page_html)

    titles_by_url: dict[str, str] = {}
    for href, text, classes in parser.links:
        url_match = GALLERY_RE.search(href)
        if not url_match:
            continue
        url = canonical_gallery_url(url_match.group(1), url_match.group(2))
        if text and ("glink" in classes or url not in titles_by_url):
            titles_by_url[url] = text

    galleries: dict[str, Gallery] = {}
    for match in GALLERY_RE.finditer(page_html):
        gid, token = match.group(1), match.group(2)
        url = canonical_gallery_url(gid, token)
        if url in galleries:
            continue

        block = nearby_gallery_block(page_html, match.start(), match.end())
        title = titles_by_url.get(url) or extract_title_from_block(block) or f"Gallery {gid}"
        tags = extract_tags(block)
        rating_match = RATING_RE.search(html.unescape(block))
        rating = float(rating_match.group(1)) if rating_match else None
        galleries[url] = Gallery(
            url=url,
            gid=gid,
            token=token,
            title=title,
            category=strip_html_match(CAT_RE.search(block)),
            uploader=strip_html_match(UPLOADER_RE.search(block)),
            thumb_url=extract_thumb(block),
            rating=rating,
            tags=tags,
            source_query=source_query,
        )

    return list(galleries.values())


def parse_gallery_detail(page_html: str, gallery_url: str) -> Gallery:
    match = GALLERY_RE.search(gallery_url)
    gid = match.group(1) if match else None
    token = match.group(2) if match else None
    title = strip_html_match(DETAIL_TITLE_RE.search(page_html))
    rating_match = DETAIL_AVERAGE_RE.search(html.unescape(page_html)) or RATING_RE.search(html.unescape(page_html))
    rating = float(rating_match.group(1)) if rating_match else None
    return Gallery(
        url=gallery_url,
        gid=gid,
        token=token,
        title=title or f"Gallery {gid or ''}".strip(),
        category=strip_html_match(DETAIL_CATEGORY_RE.search(page_html)),
        uploader=strip_html_match(DETAIL_UPLOADER_RE.search(page_html)),
        posted_at=strip_html_match(DETAIL_POSTED_RE.search(page_html)),
        thumb_url=extract_thumb(page_html),
        rating=rating,
        tags=extract_tags(page_html),
    )


def merge_gallery(base: Gallery, detail: Gallery) -> Gallery:
    tags = list(base.tags)
    for tag in detail.tags:
        if tag not in tags:
            tags.append(tag)
    return Gallery(
        url=base.url,
        gid=base.gid or detail.gid,
        token=base.token or detail.token,
        title=detail.title if detail.title and not detail.title.startswith("Gallery ") else base.title,
        category=detail.category or base.category,
        uploader=detail.uploader or base.uploader,
        posted_at=detail.posted_at or base.posted_at,
        thumb_url=detail.thumb_url or base.thumb_url,
        rating=detail.rating if detail.rating is not None else base.rating,
        tags=tags,
        source_query=base.source_query,
    )


def canonical_gallery_url(gid: str, token: str) -> str:
    return f"https://exhentai.org/g/{gid}/{token}/"


def nearby_gallery_block(page_html: str, start: int, end: int) -> str:
    before_markers = ["<tr", "<div class=\"gl", "<div class='gl"]
    after_markers = ["</tr>", "</div>"]
    left = max(0, start - 5000)
    for marker in before_markers:
        idx = page_html.rfind(marker, left, start)
        if idx >= 0:
            left = idx
            break
    right = min(len(page_html), end + 5000)
    for marker in after_markers:
        idx = page_html.find(marker, end, right)
        if idx >= 0:
            right = idx + len(marker)
            break
    return page_html[left:right]


def extract_title_from_block(block: str) -> str | None:
    glink = re.search(r"class=[\"'][^\"']*\bglink\b[^\"']*[\"'][^>]*>(.*?)</", block, re.I | re.S)
    if glink:
        return strip_tags(glink.group(1))
    title_attr = re.search(r"title=[\"']([^\"']+)[\"']", block, re.I)
    if title_attr:
        return html.unescape(title_attr.group(1)).strip()
    return None


def extract_tags(block: str) -> list[str]:
    tags: list[str] = []
    link_tags = [next(group for group in groups if group) for groups in TAG_RE.findall(block)]
    for raw in [*link_tags, *TAG_ATTR_RE.findall(block)]:
        tag = normalize_tag(raw)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def normalize_tag(raw: str) -> str:
    tag = html.unescape(raw)
    tag = urllib.parse.unquote_plus(tag).strip().lower()
    tag = tag.replace("_", " ")
    return " ".join(tag.split())


def extract_thumb(block: str) -> str | None:
    img = IMG_RE.search(block)
    if img:
        return html.unescape(img.group(1))
    css_url = CSS_URL_RE.search(block)
    if css_url:
        return html.unescape(css_url.group(1))
    return None


def strip_html_match(match: re.Match[str] | None) -> str | None:
    if not match:
        return None
    value = strip_tags(match.group(1))
    return value or None


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split()).strip()
