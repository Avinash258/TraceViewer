"""Parse Playwright trace.zip / extracted traces into network request records."""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qsl, urlparse

from apiextracter.filters import is_api_request


@dataclass
class Header:
    name: str
    value: str


@dataclass
class QueryParam:
    name: str
    value: str


@dataclass
class FormParam:
    name: str
    value: str
    file_name: str | None = None
    content_type: str | None = None


@dataclass
class NetworkRequest:
    method: str
    url: str
    headers: list[Header]
    query: list[QueryParam]
    body_text: str | None
    body_mime: str | None
    body_params: list[FormParam]
    status: int
    status_text: str
    response_headers: list[Header]
    response_body: str | None
    response_mime: str | None
    started: str | None
    time_ms: float
    api_request: bool
    source: str
    monotonic: float = 0.0


@dataclass
class ParseResult:
    requests: list[NetworkRequest]
    total_seen: int
    filtered_out: int
    sources: list[str]
    title: str | None = None


class ResourceStore:
    """Look up request/response blobs stored under resources/ in a zip or folder."""

    def __init__(self, names: Iterable[str], reader) -> None:
        self._names = list(names)
        self._lower = {name.lower(): name for name in self._names}
        self._reader = reader

    def read_text(self, ref: str | None) -> str | None:
        data = self.read_bytes(ref)
        if data is None:
            return None
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return data.decode("latin-1")
            except UnicodeDecodeError:
                return None

    def read_bytes(self, ref: str | None) -> bytes | None:
        if not ref:
            return None
        path = self._resolve(ref)
        if not path:
            return None
        try:
            return self._reader(path)
        except (KeyError, FileNotFoundError, OSError):
            return None

    def _resolve(self, ref: str) -> str | None:
        candidates = [
            ref,
            f"resources/{ref}",
            f"resources/{Path(ref).name}",
            Path(ref).name,
        ]
        for candidate in candidates:
            hit = self._lower.get(candidate.replace("\\", "/").lower())
            if hit:
                return hit

        basename = Path(ref).name.lower()
        prefix = f"resources/{basename}"
        for name in self._names:
            lowered = name.replace("\\", "/").lower()
            if lowered == prefix or lowered.startswith(prefix + "."):
                return name
            if lowered.endswith("/" + basename):
                return name
        return None


def parse_trace(
    path: str | Path,
    *,
    include_static: bool = False,
    include_preflight: bool = False,
) -> ParseResult:
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"Trace not found: {target}")

    if target.is_dir():
        return _parse_directory(target, include_static, include_preflight)
    if target.suffix.lower() == ".zip":
        return _parse_zip(target, include_static, include_preflight)
    if target.suffix.lower() in {".network", ".trace", ".har", ".json"}:
        store = ResourceStore([], lambda _: None)
        requests, total, filtered = _parse_named_file(
            target.name,
            target.read_text(encoding="utf-8", errors="replace"),
            store,
            include_static,
            include_preflight,
        )
        return ParseResult(requests, total, filtered, [target.name], target.stem)
    raise ValueError(f"Unsupported input: {target}. Expected a Playwright trace.zip, folder, or .network file.")


def _parse_zip(zip_path: Path, include_static: bool, include_preflight: bool) -> ParseResult:
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        store = ResourceStore(names, zf.read)
        all_requests: list[NetworkRequest] = []
        total = 0
        filtered = 0
        sources: list[str] = []
        title = zip_path.stem

        for name in _payload_names(names):
            text = zf.read(name).decode("utf-8", errors="replace")
            reqs, seen, skipped = _parse_named_file(
                name, text, store, include_static, include_preflight
            )
            if seen:
                sources.append(name)
            all_requests.extend(reqs)
            total += seen
            filtered += skipped

        _sort_requests(all_requests)
        title = _title_from_zip(zf, names) or title
        return ParseResult(all_requests, total, filtered, sources, title)


def _parse_directory(folder: Path, include_static: bool, include_preflight: bool) -> ParseResult:
    names: list[str] = []
    files: dict[str, Path] = {}
    for item in folder.rglob("*"):
        if item.is_file():
            rel = item.relative_to(folder).as_posix()
            names.append(rel)
            files[rel] = item

    def reader(name: str) -> bytes:
        return files[name].read_bytes()

    store = ResourceStore(names, reader)
    all_requests: list[NetworkRequest] = []
    total = 0
    filtered = 0
    sources: list[str] = []

    for rel in _payload_names(list(files)):
        text = files[rel].read_text(encoding="utf-8", errors="replace")
        reqs, seen, skipped = _parse_named_file(
            rel, text, store, include_static, include_preflight
        )
        if seen:
            sources.append(rel)
        all_requests.extend(reqs)
        total += seen
        filtered += skipped

    _sort_requests(all_requests)
    return ParseResult(all_requests, total, filtered, sources, folder.name)


def _sort_requests(requests: list[NetworkRequest]) -> None:
    """Keep capture order unless Playwright monotonic timestamps can merge files."""
    if any(req.monotonic for req in requests):
        requests.sort(key=lambda req: req.monotonic)


def _payload_names(names: list[str]) -> list[str]:
    """Prefer *.network over *.trace so the same requests are not imported twice."""
    har = [n for n in names if n.lower().endswith(".har")]
    network = [n for n in names if n.lower().endswith(".network")]
    if network:
        return network + har
    trace = [n for n in names if n.lower().endswith(".trace")]
    return trace + har


def _title_from_zip(zf: zipfile.ZipFile, names: list[str]) -> str | None:
    for name in names:
        if name.lower().endswith(".trace"):
            for line in zf.read(name).decode("utf-8", errors="replace").splitlines():
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("type") == "context-options":
                    return event.get("title") or None
    return None


def _parse_named_file(
    name: str,
    text: str,
    store: ResourceStore,
    include_static: bool,
    include_preflight: bool,
) -> tuple[list[NetworkRequest], int, int]:
    stripped = text.strip()
    if not stripped:
        return [], 0, 0

    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("log"), dict):
            return _parse_har_log(name, data["log"], store, include_static, include_preflight)

    return _parse_jsonl(name, text, store, include_static, include_preflight)


def _parse_har_log(
    source: str,
    log: dict,
    store: ResourceStore,
    include_static: bool,
    include_preflight: bool,
) -> tuple[list[NetworkRequest], int, int]:
    requests: list[NetworkRequest] = []
    filtered = 0
    entries = log.get("entries") or []
    for entry in entries:
        parsed = _from_har_entry(entry, store, source)
        if parsed is None:
            continue
        if _keep(parsed, include_static, include_preflight):
            requests.append(parsed)
        else:
            filtered += 1
    return requests, len(entries), filtered


def _parse_jsonl(
    source: str,
    text: str,
    store: ResourceStore,
    include_static: bool,
    include_preflight: bool,
) -> tuple[list[NetworkRequest], int, int]:
    requests: list[NetworkRequest] = []
    seen = 0
    filtered = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        entry = _entry_from_event(event)
        if entry is None:
            continue
        seen += 1
        parsed = _from_har_entry(entry, store, source)
        if parsed is None:
            continue
        if _keep(parsed, include_static, include_preflight):
            requests.append(parsed)
        else:
            filtered += 1
    return requests, seen, filtered


def _entry_from_event(event: dict) -> dict | None:
    if event.get("type") != "resource-snapshot":
        return None
    snapshot = event.get("snapshot")
    if not isinstance(snapshot, dict):
        return None
    if "request" in snapshot:
        return snapshot
    # Very old ResourceSnapshot shape (pre-HAR).
    if "url" in snapshot and "method" in snapshot:
        return {
            "request": {
                "url": snapshot.get("url"),
                "method": snapshot.get("method"),
                "headers": snapshot.get("requestHeaders") or [],
                "postData": {"_sha1": snapshot["requestSha1"]} if snapshot.get("requestSha1") else None,
            },
            "response": {
                "status": snapshot.get("status") or 0,
                "headers": snapshot.get("responseHeaders") or [],
                "content": {
                    "mimeType": snapshot.get("contentType") or "",
                    "_sha1": snapshot.get("responseSha1"),
                },
            },
            "_monotonicTime": snapshot.get("timestamp"),
        }
    return None


def _keep(req: NetworkRequest, include_static: bool, include_preflight: bool) -> bool:
    request_mime = req.body_mime or _header_value(req.headers, "content-type")
    return is_api_request(
        req.url,
        req.method,
        mime_type=req.response_mime or "",
        request_mime=request_mime or "",
        api_request=req.api_request,
        include_static=include_static,
        include_preflight=include_preflight,
    )


def _from_har_entry(entry: dict, store: ResourceStore, source: str) -> NetworkRequest | None:
    request = entry.get("request") or {}
    response = entry.get("response") or {}
    url = request.get("url") or ""
    if not url:
        return None

    method = (request.get("method") or "GET").upper()
    req_headers = _headers(request.get("headers"))
    resp_headers = _headers(response.get("headers"))
    query = _query(request, url)
    body_text, body_mime, body_params = _request_body(request, store)
    content = response.get("content") or {}
    response_body = _response_body(content, store)
    response_mime = content.get("mimeType") or _header_value(resp_headers, "content-type")

    return NetworkRequest(
        method=method,
        url=url,
        headers=req_headers,
        query=query,
        body_text=body_text,
        body_mime=body_mime,
        body_params=body_params,
        status=int(response.get("status") or 0),
        status_text=response.get("statusText") or "",
        response_headers=resp_headers,
        response_body=response_body,
        response_mime=response_mime,
        started=entry.get("startedDateTime"),
        time_ms=float(entry.get("time") or 0),
        api_request=bool(entry.get("_apiRequest")),
        source=source,
        monotonic=float(entry.get("_monotonicTime") or 0),
    )


def _headers(raw) -> list[Header]:
    result: list[Header] = []
    if not raw:
        return result
    if isinstance(raw, dict):
        for name, value in raw.items():
            result.append(Header(str(name), "" if value is None else str(value)))
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        result.append(Header(str(name), "" if item.get("value") is None else str(item.get("value"))))
    return result


def _query(request: dict, url: str) -> list[QueryParam]:
    raw = request.get("queryString") or []
    if raw:
        return [
            QueryParam(str(item.get("name") or ""), "" if item.get("value") is None else str(item.get("value")))
            for item in raw
            if isinstance(item, dict)
        ]
    parsed = urlparse(url)
    return [QueryParam(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)]


def _request_body(request: dict, store: ResourceStore) -> tuple[str | None, str | None, list[FormParam]]:
    post = request.get("postData")
    if not post:
        return None, None, []
    if isinstance(post, str):
        return post, None, []
    if not isinstance(post, dict):
        return None, None, []

    mime = post.get("mimeType")
    params = _form_params(post.get("params") or [])
    text = post.get("text")
    if not text:
        text = store.read_text(post.get("_sha1") or post.get("_file") or post.get("sha1"))
    if isinstance(text, (dict, list)):
        text = json.dumps(text)
    return (None if text is None else str(text)), mime, params


def _form_params(raw) -> list[FormParam]:
    params: list[FormParam] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        params.append(
            FormParam(
                name=str(item.get("name") or item.get("key") or ""),
                value="" if item.get("value") is None else str(item.get("value")),
                file_name=item.get("fileName") or item.get("filename"),
                content_type=item.get("contentType") or item.get("type"),
            )
        )
    return params


def _response_body(content: dict, store: ResourceStore) -> str | None:
    text = content.get("text")
    if text in (None, ""):
        text = store.read_text(content.get("_sha1") or content.get("_file") or content.get("sha1"))
    if text is None:
        return None
    if isinstance(text, (dict, list)):
        return json.dumps(text)
    return str(text)


def _header_value(headers: list[Header], name: str) -> str | None:
    needle = name.lower()
    for header in headers:
        if header.name.lower() == needle:
            return header.value
    return None
