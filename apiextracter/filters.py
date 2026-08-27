"""Decide which captured network requests belong in an API collection."""

from __future__ import annotations

from urllib.parse import urlparse

STATIC_EXTENSIONS = {
    ".js",
    ".mjs",
    ".cjs",
    ".css",
    ".map",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".avif",
    ".bmp",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".mp4",
    ".webm",
    ".mp3",
    ".wav",
    ".ogg",
    ".pdf",
    ".wasm",
    ".html",
    ".htm",
}

STATIC_MIME_PREFIXES = (
    "image/",
    "font/",
    "video/",
    "audio/",
    "text/css",
    "text/html",
    "text/javascript",
    "application/javascript",
    "application/x-javascript",
    "application/font",
    "application/wasm",
    "application/manifest+json",
)

SKIP_SCHEMES = {"data", "blob", "file", "about", "chrome", "chrome-extension", "devtools"}

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "host",
}

HTTP2_PSEUDO_HEADERS = {":method", ":path", ":scheme", ":authority", ":status"}

BROWSER_NOISE_HEADERS = {
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "sec-ch-ua-platform-version",
    "sec-ch-ua-bitness",
    "sec-ch-ua-arch",
    "sec-ch-ua-model",
    "sec-ch-ua-full-version",
    "sec-ch-ua-full-version-list",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "sec-fetch-user",
    "dnt",
    "upgrade-insecure-requests",
    "priority",
}


def _path_extension(url: str) -> str:
    path = urlparse(url).path.lower()
    if "." not in path.rsplit("/", 1)[-1]:
        return ""
    return "." + path.rsplit(".", 1)[-1]


def is_static_asset(url: str, mime_type: str = "") -> bool:
    parsed = urlparse(url)
    if parsed.scheme.lower() in SKIP_SCHEMES:
        return True
    if _path_extension(url) in STATIC_EXTENSIONS:
        return True
    mime = (mime_type or "").split(";")[0].strip().lower()
    return any(mime.startswith(prefix) for prefix in STATIC_MIME_PREFIXES)


def is_api_request(
    url: str,
    method: str,
    mime_type: str = "",
    request_mime: str = "",
    api_request: bool = False,
    include_static: bool = False,
    include_preflight: bool = False,
) -> bool:
    if api_request:
        return True

    parsed = urlparse(url)
    if parsed.scheme.lower() in SKIP_SCHEMES or not parsed.scheme.startswith("http"):
        return False

    method_upper = (method or "GET").upper()
    if method_upper == "OPTIONS" and not include_preflight:
        return False

    if include_static:
        return True

    if is_static_asset(url, mime_type) and not request_mime:
        return False

    if is_static_asset(url, mime_type):
        # Keep HTML/static URLs only when the request itself looks like an API payload.
        request_mime_l = request_mime.split(";")[0].strip().lower()
        return request_mime_l in {
            "application/json",
            "application/xml",
            "application/graphql",
            "application/x-www-form-urlencoded",
            "multipart/form-data",
            "text/xml",
        }

    return True


def should_keep_header(name: str, include_browser_headers: bool = False) -> bool:
    key = name.lower().strip()
    if key in HTTP2_PSEUDO_HEADERS or key in HOP_BY_HOP_HEADERS:
        return False
    if not include_browser_headers and (
        key in BROWSER_NOISE_HEADERS or key.startswith("sec-ch-ua")
    ):
        return False
    return True
