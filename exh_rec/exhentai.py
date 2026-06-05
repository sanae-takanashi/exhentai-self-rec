from __future__ import annotations

import html
import json
import random
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

from .net import open_url, open_url_with_retry


BASE_URL = "https://exhentai.org/"
EH_API_URL = "https://api.e-hentai.org/api.php"
GDATA_BATCH_SIZE = 25
GDATA_BATCH_PAUSE = 5.0  # seconds between batches to respect the API rate limit
GALLERY_RE = re.compile(r"(?:https?:)?(?://(?:exhentai|e-hentai)\.org)?/g/(\d+)/([0-9a-fA-F]+)/?")
TAG_RE = re.compile(r"https?://(?:exhentai|e-hentai)\.org/tag/([^\"'#?]+)|/tag/([^\"'#?]+)")
TAG_ATTR_RE = re.compile(
    r"\b(?:title|id)=[\"'](?:ta_)?((?:artist|character|cosplayer|female|group|language|male|mixed|other|parody|reclass):[^\"']+)[\"']",
    re.I,
)
TAG_OPEN_RE = re.compile(r"<[a-z0-9]+\b(?P<attrs>[^>]*)>", re.I)
CLASS_ATTR_RE = re.compile(r"\bclass=[\"']([^\"']+)[\"']", re.I)
TAG_POWER_CLASS_STRENGTHS = {
    "gt": 1.15,  # solid border: high cumulative tag power
    "gtl": 0.9,  # dashed/light tag
    "gtw": 0.55,  # dotted/weak tag
}
IMG_TAG_RE = re.compile(r"<img\b[^>]*>", re.I)
IMG_ATTR_RE = re.compile(r"\b(data-src|src)=[\"']([^\"']+)[\"']", re.I)
CSS_URL_RE = re.compile(r"url\(\s*[\"']?([^\"')\s]+)[\"']?\s*\)", re.I)
CAT_RE = re.compile(r"class=[\"'][^\"']*\bcn\b[^\"']*[\"'][^>]*>(.*?)</", re.I | re.S)
UPLOADER_RE = re.compile(r"class=[\"'][^\"']*\bglhide\b[^\"']*[\"'][^>]*>(.*?)</", re.I | re.S)
RATING_RE = re.compile(r"Rating:\s*([0-9.]+)", re.I)
DETAIL_TITLE_RE = re.compile(r"id=[\"']gn[\"'][^>]*>(.*?)</", re.I | re.S)
DETAIL_CATEGORY_RE = re.compile(r"id=[\"']gdc[\"'][^>]*>.*?class=[\"'][^\"']*\bcn\b[^\"']*[\"'][^>]*>(.*?)</", re.I | re.S)
DETAIL_UPLOADER_RE = re.compile(r"id=[\"']gdn[\"'][^>]*>(.*?)</", re.I | re.S)
DETAIL_POSTED_RE = re.compile(r"<td[^>]*>\s*Posted:\s*</td>\s*<td[^>]*>(.*?)</td>", re.I | re.S)
DETAIL_AVERAGE_RE = re.compile(r"Average:\s*([0-9.]+)", re.I)
DETAIL_LENGTH_RE = re.compile(r"Length:\s*</td>\s*<td[^>]*>\s*([\d,]+)", re.I)
DETAIL_LENGTH_FALLBACK_RE = re.compile(r"([\d,]+)\s*pages", re.I)
GDT_START_RE = re.compile(r"<div[^>]*\bid=[\"']gdt[\"']", re.I)
GDT_END_RE = re.compile(r"<div[^>]*\bid=[\"'](?:gdb|gtb|cdiv|chd)[\"']", re.I)
SAMPLE_THUMB_RE = re.compile(
    r"(https?:)?//(?:[a-z0-9.-]*\bs\.exhentai\.org|[a-z0-9.-]*\behgt\.org|[a-z0-9.-]+\.hath\.network)/[^\"')\s]+",
    re.I,
)
STYLE_ATTR_RE = re.compile(r"style=[\"']([^\"']*)[\"']", re.I)
SPRITE_POS_RE = re.compile(r"\)\s*(-?\d+)(?:px)?\s+(-?\d+)(?:px)?", re.I)
SPRITE_WIDTH_RE = re.compile(r"\bwidth:\s*(\d+)\s*px", re.I)
SPRITE_HEIGHT_RE = re.compile(r"\bheight:\s*(\d+)\s*px", re.I)


@dataclass
class SampleThumb:
    """One page preview cropped from a sprite sheet (E-Hentai "Normal" mode)."""

    url: str
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0


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
    tag_weights: dict[str, float] = field(default_factory=dict)
    source_query: str | None = None
    page_count: int | None = None
    # Each entry is either a plain URL string (individual/"Large" previews) or a
    # ``{"url", "x", "y", "w", "h"}`` sprite-frame dict ("Normal" mode).
    sample_thumbs: list = field(default_factory=list)


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
    header_pairs = parse_cookie_header_fragments(raw)
    if header_pairs:
        return "; ".join(f"{name}={value}" for name, value in header_pairs)
    return " ".join(raw.split())


def parse_cookie_header_fragments(raw: str) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for part in re.split(r"[;\n]+", raw):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            return []
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not is_cookie_name(name) or not value:
            return []
        pairs.append((name, value))
    return pairs


def valid_cookie_header(cookie_header: str) -> bool:
    parts = [part.strip() for part in cookie_header.split(";") if part.strip()]
    if not parts:
        return False
    for part in parts:
        if "=" not in part:
            return False
        name, value = part.split("=", 1)
        if not is_cookie_name(name.strip()) or not value.strip():
            return False
    return True


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
        tab_delimited = "\t" in line
        parts = line.split("\t")
        if len(parts) < 2:
            parts = line.split()
        if len(parts) < 2:
            return []
        if looks_like_cookie_table_header(parts):
            continue
        if len(parts) >= 7 and looks_like_netscape_cookie_row(parts):
            name = parts[5].strip()
            value = parts[6].strip()
        else:
            if not tab_delimited and not looks_like_browser_cookie_row(parts):
                return []
            name = parts[0].strip()
            value = parts[1].strip()
        if not is_cookie_name(name) or not value:
            return []
        pairs.append((name, value))
    return pairs if pairs else []


def looks_like_cookie_table_header(parts: list[str]) -> bool:
    if len(parts) < 2:
        return False
    first = parts[0].strip().lower()
    second = parts[1].strip().lower()
    return first == "name" and second == "value"


def looks_like_browser_cookie_row(parts: list[str]) -> bool:
    if len(parts) < 4:
        return False
    domain = parts[2].strip()
    path = parts[3].strip()
    return ("." in domain or domain in {"localhost", "127.0.0.1"}) and path.startswith("/")


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


def fetch_page(cookie_header: str, url: str, timeout: int = 30, proxy_url: str = "") -> str:
    request = urllib.request.Request(
        url,
        headers={
            "Cookie": normalize_cookie_header(cookie_header),
            "User-Agent": "exhentai-self-recommend/0.1 (+local personal recommender)",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    try:
        with open_url_with_retry(request, timeout=timeout, proxy_url=proxy_url) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return response.read().decode(charset, errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} while fetching {url}: {body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not fetch {url}: {exc.reason}") from exc


def fetch_galleries(
    cookie_header: str,
    query: str | None,
    pages: int = 1,
    delay: float = 1.0,
    proxy_url: str = "",
) -> list[Gallery]:
    galleries: list[Gallery] = []
    seen: set[str] = set()
    for page in range(max(1, pages)):
        page_html = fetch_page(cookie_header, build_search_url(query, page), proxy_url=proxy_url)
        for gallery in parse_gallery_list(page_html, source_query=query):
            if gallery.url not in seen:
                galleries.append(gallery)
                seen.add(gallery.url)
        if page + 1 < pages:
            time.sleep(delay)
    return galleries


def post_api_json(cookie_header: str, payload: dict, timeout: int = 30, proxy_url: str = "") -> dict:
    """POST a JSON request to the E-Hentai API and return the decoded response."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        EH_API_URL,
        data=body,
        method="POST",
        headers={
            "Cookie": normalize_cookie_header(cookie_header),
            "User-Agent": "exhentai-self-recommend/0.1 (+local personal recommender)",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with open_url_with_retry(request, timeout=timeout, proxy_url=proxy_url) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            return json.loads(response.read().decode(charset, errors="replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} from gdata API: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach gdata API: {exc.reason}") from exc


def fetch_gallery_metadata(
    cookie_header: str,
    gid_token_pairs: list[tuple[str | int, str]],
    delay: float = GDATA_BATCH_PAUSE,
    proxy_url: str = "",
    sleep=time.sleep,
) -> dict[str, dict]:
    """Return ``{canonical_gallery_url: metadata}`` via the E-Hentai gdata API.

    Galleries are looked up in batches of 25 (the API limit), with a pause
    between batches to respect the rate limit. The ``thumb`` field is a stable
    cover URL on the ehgt.org CDN, unlike the time-sensitive ``s.exhentai.org``
    cover URLs scraped from HTML. A failing batch is skipped so the others still
    return; the result may therefore be partial.
    """
    seen: set[tuple[int, str]] = set()
    pairs: list[tuple[int, str]] = []
    for gid, token in gid_token_pairs:
        try:
            key = (int(gid), str(token))
        except (TypeError, ValueError):
            continue
        if key not in seen:
            seen.add(key)
            pairs.append(key)

    metadata: dict[str, dict] = {}
    for index in range(0, len(pairs), GDATA_BATCH_SIZE):
        chunk = pairs[index : index + GDATA_BATCH_SIZE]
        try:
            response = post_api_json(
                cookie_header,
                {"method": "gdata", "gidlist": [list(pair) for pair in chunk], "namespace": 1},
                proxy_url=proxy_url,
            )
        except Exception:
            continue
        for entry in response.get("gmetadata") or []:
            if not isinstance(entry, dict) or entry.get("error"):
                continue
            gid = entry.get("gid")
            token = entry.get("token")
            if gid is None or not token:
                continue
            metadata[canonical_gallery_url(str(gid), str(token))] = parse_gdata_entry(entry)
        if index + GDATA_BATCH_SIZE < len(pairs):
            sleep(delay)
    return metadata


def parse_gdata_entry(entry: dict) -> dict:
    """Map one gdata ``gmetadata`` entry onto the fields the app stores."""
    tags: list[str] = []
    for raw in entry.get("tags") or []:
        tag = normalize_tag(str(raw))
        if tag and tag not in tags:
            tags.append(tag)
    return {
        "thumb": str(entry.get("thumb") or "") or None,
        "title": html.unescape(str(entry.get("title") or "")) or None,
        "title_jpn": html.unescape(str(entry.get("title_jpn") or "")) or None,
        "category": str(entry.get("category") or "") or None,
        "uploader": html.unescape(str(entry.get("uploader") or "")) or None,
        "posted": str(entry.get("posted") or "") or None,
        "page_count": coerce_int(entry.get("filecount")),
        "rating": coerce_float(entry.get("rating")),
        "tags": tags,
    }


def coerce_int(value: object) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def coerce_float(value: object) -> float | None:
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def apply_gallery_metadata(galleries: list[Gallery], metadata: dict[str, dict]) -> int:
    """Fill cover/title/category/etc. on ``galleries`` from gdata ``metadata``.

    Prefers the ehgt.org ``thumb`` cover when present; otherwise leaves the
    existing (HTML-scraped) values in place. Returns the number of galleries
    whose ``thumb_url`` was set or changed.
    """
    updated = 0
    for gallery in galleries:
        meta = metadata.get(gallery.url)
        if not meta:
            continue
        thumb = meta.get("thumb")
        if thumb and thumb != gallery.thumb_url:
            gallery.thumb_url = thumb
            updated += 1
        title = meta.get("title")
        if title and (not gallery.title or gallery.title.startswith("Gallery ")):
            gallery.title = title
        gallery.category = gallery.category or meta.get("category")
        gallery.uploader = gallery.uploader or meta.get("uploader")
        if gallery.rating is None and meta.get("rating") is not None:
            gallery.rating = meta["rating"]
        if gallery.page_count is None and meta.get("page_count") is not None:
            gallery.page_count = meta["page_count"]
        if not gallery.posted_at and meta.get("posted"):
            gallery.posted_at = format_posted(meta["posted"])
        for tag in meta.get("tags") or []:
            if tag not in gallery.tags:
                gallery.tags.append(tag)
    return updated


def format_posted(posted: str) -> str:
    """Format the gdata unix-timestamp ``posted`` field as a readable date."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.gmtime(int(posted)))
    except (TypeError, ValueError, OSError):
        return posted


def check_access(cookie_header: str, proxy_url: str = "") -> dict:
    page_html = fetch_page(cookie_header, BASE_URL, timeout=15, proxy_url=proxy_url)
    galleries = parse_gallery_list(page_html)
    lower_html = page_html.lower()
    if galleries:
        return {"ok": True, "gallery_count": len(galleries), "message": f"Access ok; saw {len(galleries)} galleries"}
    if "exhentai.org" in lower_html and ("login" in lower_html or "sad panda" in lower_html):
        return {"ok": False, "gallery_count": 0, "message": "Cookie did not expose gallery listings"}
    return {"ok": False, "gallery_count": 0, "message": "Fetched page, but no gallery listings were found"}


def fetch_gallery_detail(cookie_header: str, gallery: Gallery, delay: float = 1.0, proxy_url: str = "") -> Gallery:
    if delay:
        time.sleep(delay)
    page_html = fetch_page(cookie_header, gallery.url, proxy_url=proxy_url)
    detail = parse_gallery_detail(page_html, gallery.url)
    return merge_gallery(gallery, detail)


def fetch_gallery_sample_pages(
    cookie_header: str,
    gallery: Gallery,
    extra_pages: int,
    delay: float = 1.0,
    proxy_url: str = "",
) -> list[str]:
    """Fetch up to ``extra_pages`` additional image-list pages and return their thumbnails.

    Only used when a gallery spans more list pages than the detail page (``p=0``)
    already covers, so samples can be drawn from across the whole gallery.
    """
    if extra_pages <= 0 or not gallery.page_count or not gallery.sample_thumbs:
        return []
    per_page = len(gallery.sample_thumbs)
    if per_page <= 0:
        return []
    total_pages = (gallery.page_count + per_page - 1) // per_page
    candidate_pages = list(range(1, total_pages))
    if not candidate_pages:
        return []
    chosen = sorted(random.sample(candidate_pages, min(extra_pages, len(candidate_pages))))
    thumbs: list = []
    for page in chosen:
        if delay:
            time.sleep(delay)
        page_html = fetch_page(cookie_header, sample_page_url(gallery.url, page), proxy_url=proxy_url)
        _, page_entries = parse_gallery_pages_rich(page_html)
        thumbs.extend(sample_storage(entry) for entry in page_entries)
    return thumbs


def sample_page_url(gallery_url: str, page: int) -> str:
    if page <= 0:
        return gallery_url
    separator = "&" if "?" in gallery_url else "?"
    return f"{gallery_url}{separator}p={page}"


def parse_gallery_pages(page_html: str) -> tuple[int | None, list[str]]:
    """Page count and per-page thumbnail URLs (individual/"Large" previews only).

    Backward-compatible wrapper: sprite-sheet ("Normal" mode) previews are
    dropped here. Use :func:`parse_gallery_pages_rich` to also get sprite frames.
    """
    page_count, entries = parse_gallery_pages_rich(page_html)
    return page_count, [entry for entry in entries if isinstance(entry, str)]


def parse_gallery_pages_rich(page_html: str) -> tuple[int | None, list]:
    """Page count and per-page previews, including sprite-sheet ("Normal") mode.

    Returns individual previews as URL strings; when the gallery only exposes
    sprite-sheet previews, returns :class:`SampleThumb` crop frames instead. A
    gallery uses one mode or the other, so the list is homogeneous in practice.
    """
    page_count = parse_page_count(page_html)
    block = gdt_block(page_html)

    # "Normal" mode renders previews as sprite-sheet frames; detect those first so
    # the large-preview passes below never mistake a sprite sheet for an
    # individual image.
    sprites = parse_sprite_previews(block)
    if sprites:
        return page_count, list(sprites)

    large: list[str] = []
    seen: set[str] = set()

    def add(url: str) -> None:
        url = normalize_sample_thumb(url)
        if usable_thumb(url) and sample_thumb_host(url) and url not in seen:
            seen.add(url)
            large.append(url)

    for match in SAMPLE_THUMB_RE.finditer(block):
        if not css_url_looks_like_sprite(block, match):
            add(match.group(0))
    for candidate in img_src_candidates(block):
        add(candidate)
    for match in CSS_URL_RE.finditer(block):
        if not css_url_looks_like_sprite(block, match):
            add(match.group(1))

    return page_count, large


def parse_sprite_previews(block: str) -> list[SampleThumb]:
    """Parse "Normal" mode sprite previews into per-frame crop boxes.

    Each preview is an inner ``<div>`` whose inline style paints one frame of a
    sprite sheet via ``background:... url(SHEET) -Xpx -Ypx`` with a fixed
    ``width``/``height``. The negative background-position becomes a positive
    crop offset into the sheet.
    """
    previews: list[SampleThumb] = []
    for style in STYLE_ATTR_RE.findall(block):
        if "url(" not in style.lower():
            continue
        url_match = CSS_URL_RE.search(style)
        pos = SPRITE_POS_RE.search(style)
        width = SPRITE_WIDTH_RE.search(style)
        height = SPRITE_HEIGHT_RE.search(style)
        if not (url_match and pos and width and height):
            continue
        url = normalize_sample_thumb(url_match.group(1))
        if not (usable_thumb(url) and sample_thumb_host(url)):
            continue
        previews.append(
            SampleThumb(
                url=url,
                x=max(0, -int(pos.group(1))),
                y=max(0, -int(pos.group(2))),
                w=int(width.group(1)),
                h=int(height.group(1)),
            )
        )
    return previews


def sample_storage(entry) -> str | dict:
    """Convert a parsed preview into its JSON-serializable storage form."""
    if isinstance(entry, SampleThumb):
        return {"url": entry.url, "x": entry.x, "y": entry.y, "w": entry.w, "h": entry.h}
    return entry


def sample_entry_url(entry) -> str:
    """Return the image URL for a sample entry (string, dict, or SampleThumb)."""
    if isinstance(entry, SampleThumb):
        return entry.url
    if isinstance(entry, dict):
        return str(entry.get("url") or "")
    return str(entry)


def parse_page_count(page_html: str) -> int | None:
    match = DETAIL_LENGTH_RE.search(page_html) or DETAIL_LENGTH_FALLBACK_RE.search(page_html)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def gdt_block(page_html: str) -> str:
    return id_block(page_html, "gdt", end_ids=("gdb", "gtb", "cdiv", "chd"))


def id_block(page_html: str, element_id: str, end_ids: tuple[str, ...] = ()) -> str:
    start_re = re.compile(rf"<div[^>]*\bid=[\"']{re.escape(element_id)}[\"']", re.I)
    start_match = GDT_START_RE.search(page_html) if element_id == "gdt" else start_re.search(page_html)
    if not start_match:
        return ""
    start = start_match.start()
    if end_ids:
        end_re = re.compile(rf"<div[^>]*\bid=[\"'](?:{'|'.join(re.escape(item) for item in end_ids)})[\"']", re.I)
        end_match = end_re.search(page_html, start_match.end())
    else:
        end_match = re.search(r"</div>", page_html[start_match.end() :], re.I)
        if end_match:
            end = start_match.end() + end_match.end()
            return page_html[start:end]
    end = end_match.start() if end_match else len(page_html)
    return page_html[start:end]


def normalize_sample_thumb(url: str) -> str:
    url = html.unescape(url).strip()
    if url.startswith("//"):
        url = f"https:{url}"
    return url


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
        tag_weights = extract_tag_weights(block)
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
            tag_weights=tag_weights,
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
    page_count, rich_entries = parse_gallery_pages_rich(page_html)
    sample_thumbs = [sample_storage(entry) for entry in rich_entries]
    thumb_url = usable_thumb(extract_thumb(id_block(page_html, "gd1")))
    if not thumb_url:
        # Fall back to the first individual preview; a sprite frame is not a usable
        # standalone cover, so skip those (the gdata API supplies covers instead).
        thumb_url = next((entry for entry in sample_thumbs if isinstance(entry, str)), None)
    tags = extract_tags(page_html)
    tag_weights = extract_tag_weights(page_html)
    return Gallery(
        url=gallery_url,
        gid=gid,
        token=token,
        title=title or f"Gallery {gid or ''}".strip(),
        category=strip_html_match(DETAIL_CATEGORY_RE.search(page_html)),
        uploader=strip_html_match(DETAIL_UPLOADER_RE.search(page_html)),
        posted_at=strip_html_match(DETAIL_POSTED_RE.search(page_html)),
        thumb_url=thumb_url,
        rating=rating,
        tags=tags,
        tag_weights=tag_weights,
        page_count=page_count,
        sample_thumbs=sample_thumbs,
    )


def merge_gallery(base: Gallery, detail: Gallery) -> Gallery:
    tags = list(base.tags)
    for tag in detail.tags:
        if tag not in tags:
            tags.append(tag)
    tag_weights = dict(base.tag_weights)
    tag_weights.update(detail.tag_weights)
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
        tag_weights=tag_weights,
        source_query=base.source_query,
        page_count=detail.page_count if detail.page_count is not None else base.page_count,
        sample_thumbs=detail.sample_thumbs or base.sample_thumbs,
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


def extract_tag_weights(block: str) -> dict[str, float]:
    weights: dict[str, float] = {}
    for match in TAG_OPEN_RE.finditer(block):
        attrs = match.group("attrs") or ""
        strength = tag_power_strength(attrs)
        if strength is None:
            continue
        nearby = block[match.end() : min(len(block), match.end() + 500)]
        tag = first_tag_in_html(f"{attrs} {nearby}")
        if not tag:
            continue
        weights[tag] = max(weights.get(tag, 0.0), strength)
    return weights


def tag_power_strength(attrs: str) -> float | None:
    class_match = CLASS_ATTR_RE.search(attrs)
    if not class_match:
        return None
    classes = set(class_match.group(1).lower().split())
    for class_name in ("gt", "gtl", "gtw"):
        if class_name in classes:
            return TAG_POWER_CLASS_STRENGTHS[class_name]
    return None


def first_tag_in_html(value: str) -> str | None:
    attr_match = TAG_ATTR_RE.search(value)
    if attr_match:
        return normalize_tag(attr_match.group(1))
    link_match = TAG_RE.search(value)
    if link_match:
        return normalize_tag(next(group for group in link_match.groups() if group))
    return None


def normalize_tag(raw: str) -> str:
    tag = html.unescape(raw)
    tag = urllib.parse.unquote_plus(tag).strip().lower()
    tag = tag.replace("_", " ")
    return " ".join(tag.split())


def usable_thumb(url: str | None) -> str | None:
    """Drop placeholder cover images (e.g. the lazy-load ``blank.gif``) so callers fall back to a real page image."""
    if not url:
        return None
    lower = url.lower()
    if "blank.gif" in lower or lower.endswith("/mr.gif") or lower.endswith("/roller.gif"):
        return None
    return url


def img_src_candidates(block: str) -> list[str]:
    """Yield image URLs from ``<img>`` tags, preferring ``data-src`` (the real lazy-load target) over the ``src`` placeholder within each tag."""
    candidates: list[str] = []
    for tag in IMG_TAG_RE.findall(block):
        attrs = {name.lower(): value for name, value in IMG_ATTR_RE.findall(tag)}
        for key in ("data-src", "src"):
            value = attrs.get(key)
            if value:
                candidates.append(value)
    return candidates


def extract_thumb(block: str) -> str | None:
    for img in img_src_candidates(block):
        url = usable_thumb(html.unescape(img))
        if url:
            return url
    for match in CSS_URL_RE.finditer(block):
        if css_url_looks_like_sprite(block, match):
            continue
        url = usable_thumb(html.unescape(match.group(1)))
        if url:
            return url
    return None


def css_url_looks_like_sprite(block: str, match: re.Match[str]) -> bool:
    """Reject CSS sprite sheets that need background-position cropping to match one gallery."""
    style_start = block.rfind("style=", 0, match.start())
    style_end = block.find(">", match.end())
    style = block[style_start : style_end if style_end >= 0 else min(len(block), match.end() + 160)]
    after_url = block[match.end() : min(len(block), match.end() + 120)]
    return bool(re.search(r"(?<![\w.])-\d+(?:\.\d+)?(?:px|em|rem|%)?", style + after_url, re.I))


def sample_thumb_host(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return (
        hostname in {"s.exhentai.org", "ehgt.org"}
        or hostname.endswith(".hath.network")
        or hostname.endswith(".ehgt.org")
    )


def strip_html_match(match: re.Match[str] | None) -> str | None:
    if not match:
        return None
    value = strip_tags(match.group(1))
    return value or None


def strip_tags(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(value).split()).strip()
