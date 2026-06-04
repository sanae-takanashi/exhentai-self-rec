from __future__ import annotations

import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from typing import Any


PROXY_ENV_KEYS = ("EXH_REC_PROXY", "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")
SUPPORTED_PROXY_SCHEMES = {"http", "https", "socks5", "socks5h"}
_SOCKS_LOCK = threading.Lock()


def default_proxy_url() -> str:
    for key in PROXY_ENV_KEYS:
        value = os.environ.get(key, "").strip()
        if value:
            return normalize_proxy_url(value)
    return ""


def normalize_proxy_url(raw: object) -> str:
    value = str(raw or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = f"http://{value}"
    parsed = urllib.parse.urlparse(value)
    scheme = parsed.scheme.lower()
    if scheme not in SUPPORTED_PROXY_SCHEMES:
        raise ValueError("Proxy must use http://, https://, socks5://, or socks5h://")
    if not parsed.hostname:
        raise ValueError("Proxy URL must include a host")
    if parsed.path not in {"", "/"} or parsed.params or parsed.query or parsed.fragment:
        raise ValueError("Proxy URL must not include a path, query, or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("Proxy URL has an invalid port") from exc
    return urllib.parse.urlunparse(
        (
            scheme,
            parsed.netloc,
            "",
            "",
            "",
            "",
        )
    )


def proxy_preview(proxy_url: str) -> str:
    proxy_url = normalize_proxy_url(proxy_url)
    if not proxy_url:
        return ""
    parsed = urllib.parse.urlparse(proxy_url)
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port is not None else ""
    user = f"{parsed.username}@" if parsed.username else ""
    return f"{parsed.scheme}://{user}{host}{port}"


def apply_proxy_environment(proxy_url: str) -> None:
    proxy_url = environment_proxy_url(normalize_proxy_url(proxy_url))
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        if proxy_url:
            os.environ[key] = proxy_url
        else:
            os.environ.pop(key, None)


def environment_proxy_url(proxy_url: str) -> str:
    # Libraries that read these env vars (requests, and through it huggingface_hub
    # for DINOv2 downloads) resolve DNS locally for a plain socks5:// proxy, which
    # fails when the target host is only reachable through the proxy. socks5h://
    # resolves the hostname on the proxy side, matching how this app's own SOCKS
    # requests already behave.
    if proxy_url.startswith("socks5://"):
        return "socks5h://" + proxy_url[len("socks5://"):]
    return proxy_url


def open_url_with_retry(
    request: urllib.request.Request,
    timeout: int,
    proxy_url: str = "",
    attempts: int = 3,
    backoff: float = 0.5,
    retry_statuses: tuple[int, ...] = (500, 502, 503, 504),
    sleep=time.sleep,
) -> Any:
    """Open a URL, retrying transient failures with exponential backoff.

    Retries on connection errors, timeouts, and the given HTTP status codes
    (server-side errors). Client errors such as 403/404 are never retried — a
    stale ExHentai thumbnail URL fails fast so callers can fall back to a
    refresh. Returns the live response object, so callers still use it as a
    context manager.
    """
    last_exc: Exception | None = None
    for index in range(max(1, attempts)):
        try:
            return open_url(request, timeout=timeout, proxy_url=proxy_url)
        except urllib.error.HTTPError as exc:
            if exc.code not in retry_statuses or index == attempts - 1:
                raise
            last_exc = exc
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            if index == attempts - 1:
                raise
            last_exc = exc
        sleep(backoff * (2 ** index))
    # Unreachable: the loop either returns or raises on the final attempt.
    raise last_exc if last_exc else RuntimeError("open_url_with_retry exhausted")


def open_url(request: urllib.request.Request, timeout: int, proxy_url: str = "") -> Any:
    proxy_url = normalize_proxy_url(proxy_url or "")
    if not proxy_url:
        return urllib.request.urlopen(request, timeout=timeout)
    parsed = urllib.parse.urlparse(proxy_url)
    if parsed.scheme in {"http", "https"}:
        handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
        opener = urllib.request.build_opener(handler)
        return opener.open(request, timeout=timeout)
    if parsed.scheme in {"socks5", "socks5h"}:
        return open_url_with_socks_proxy(request, timeout, parsed)
    raise ValueError("unsupported proxy scheme")


def open_url_with_socks_proxy(
    request: urllib.request.Request,
    timeout: int,
    parsed_proxy: urllib.parse.ParseResult,
) -> Any:
    try:
        import socks
    except Exception as exc:
        raise RuntimeError("SOCKS5 proxy support requires PySocks: python3 -m pip install PySocks") from exc

    proxy_host = parsed_proxy.hostname
    proxy_port = parsed_proxy.port or 1080
    username = urllib.parse.unquote(parsed_proxy.username) if parsed_proxy.username else None
    password = urllib.parse.unquote(parsed_proxy.password) if parsed_proxy.password else None
    rdns = parsed_proxy.scheme == "socks5h" or parsed_proxy.scheme == "socks5"

    with _SOCKS_LOCK, patched_socks_create_connection(socks, proxy_host, proxy_port, username, password, rdns):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)


@contextmanager
def patched_socks_create_connection(
    socks: Any,
    proxy_host: str | None,
    proxy_port: int,
    username: str | None,
    password: str | None,
    rdns: bool,
):
    original = socket.create_connection

    def create_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
        sock = socks.socksocket()
        sock.set_proxy(socks.SOCKS5, proxy_host, proxy_port, rdns=rdns, username=username, password=password)
        if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
            sock.settimeout(timeout)
        if source_address:
            sock.bind(source_address)
        sock.connect(address)
        return sock

    socket.create_connection = create_connection
    try:
        yield
    finally:
        socket.create_connection = original
