"""Convert captured network requests into a Postman Collection v2.1 document."""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from typing import Any
from urllib.parse import parse_qsl, unquote, urlparse

from apiextracter.filters import should_keep_header
from apiextracter.flow import POSTMAN_SAVE_TOKEN_SCRIPT, FlowAnalysis, SequenceStep
from apiextracter.parser import FormParam, Header, NetworkRequest, ParseResult

POSTMAN_SCHEMA = "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"

MAX_RESPONSE_CHARS = 500_000


def build_collection(
    result: ParseResult,
    *,
    name: str | None = None,
    group_by: str = "host",
    include_responses: bool = True,
    include_browser_headers: bool = False,
    dedupe: bool = False,
    extract_variables: bool = True,
    flow: FlowAnalysis | None = None,
) -> dict[str, Any]:
    requests = list(flow.requests if flow else result.requests)
    if dedupe:
        requests = _dedupe(requests)

    step_by_id = {}
    if flow:
        for step, req in zip(flow.steps, flow.requests):
            step_by_id[id(req)] = step

    variables: list[dict[str, str]] = []
    token_vars: dict[str, str] = {}
    if extract_variables:
        if flow and flow.access_token:
            token_vars, variables = _flow_token_variables(flow)
        else:
            token_vars, variables = _auth_variables(requests)

    folders: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for index, req in enumerate(requests, start=1):
        item = _request_item(
            req,
            index,
            include_responses=include_responses,
            include_browser_headers=include_browser_headers,
            token_vars=token_vars,
            step=step_by_id.get(id(req)),
        )
        key = _folder_key(req, group_by)
        folders[key].append(item)

    if group_by in {"none", "sequence"} or (len(folders) == 1 and group_by == "host"):
        items = [item for group in folders.values() for item in group]
    else:
        items = [
            {
                "name": folder_name,
                "item": folder_items,
            }
            for folder_name, folder_items in folders.items()
        ]

    collection_name = name or result.title or "Playwright API Collection"
    start = "access token fetch" if flow and flow.token_found else "first captured API"
    description = (
        f"API sequence captured from Playwright trace `{result.title or 'trace'}`.\n\n"
        f"- Starts from: {start}\n"
        f"- API requests: {len(requests)}\n"
        f"- Network events seen: {result.total_seen}\n"
        f"- Filtered out (static/preflight): {result.filtered_out}\n"
        f"- Sources: {', '.join(result.sources) or 'n/a'}\n\n"
        "Import this JSON in Postman via **Import → File**. "
        "Run the collection in order so the token fetch can populate `{{accessToken}}`."
    )

    collection: dict[str, Any] = {
        "info": {
            "_postman_id": str(uuid.uuid4()),
            "name": collection_name,
            "description": description,
            "schema": POSTMAN_SCHEMA,
        },
        "item": items,
    }
    if variables:
        collection["variable"] = variables
    return collection


def _dedupe(requests: list[NetworkRequest]) -> list[NetworkRequest]:
    seen: set[tuple] = set()
    unique: list[NetworkRequest] = []
    for req in requests:
        key = (req.method, req.url, req.body_text or "")
        if key in seen:
            continue
        seen.add(key)
        unique.append(req)
    return unique


def _folder_key(req: NetworkRequest, group_by: str) -> str:
    parsed = urlparse(req.url)
    if group_by == "path":
        first = next((part for part in parsed.path.split("/") if part), "")
        return f"{parsed.netloc} / {first}" if first else parsed.netloc or "unknown"
    if group_by in {"none", "sequence"}:
        return "requests"
    return parsed.netloc or "unknown"


def _request_item(
    req: NetworkRequest,
    index: int,
    *,
    include_responses: bool,
    include_browser_headers: bool,
    token_vars: dict[str, str],
    step: SequenceStep | None = None,
) -> dict[str, Any]:
    parsed = urlparse(req.url)
    path = parsed.path or "/"
    role_note = ""
    if step and step.role == "access_token_fetch":
        role_note = " [access token fetch]"
    elif step and step.uses_token_from_step:
        role_note = f" [uses token from step {step.uses_token_from_step}]"
    name = f"{index:03d} {req.method} {path}{role_note}"
    request_obj = _postman_request(req, include_browser_headers, token_vars, step)
    item: dict[str, Any] = {
        "name": name,
        "request": request_obj,
        "response": [],
    }
    if step and step.role == "access_token_fetch":
        item["event"] = [
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": POSTMAN_SAVE_TOKEN_SCRIPT.splitlines(),
                },
            }
        ]
    if include_responses and req.status:
        item["response"] = [_example_response(req, name, request_obj, include_browser_headers)]
    return item


def _postman_request(
    req: NetworkRequest,
    include_browser_headers: bool,
    token_vars: dict[str, str],
    step: SequenceStep | None = None,
) -> dict[str, Any]:
    headers = []
    for header in req.headers:
        if not should_keep_header(header.name, include_browser_headers):
            continue
        value = token_vars.get(f"{header.name.lower()}:{header.value}", header.value)
        headers.append({"key": header.name, "value": value, "type": "text"})

    request: dict[str, Any] = {
        "method": req.method,
        "header": headers,
        "url": _postman_url(req),
        "description": _request_description(req, step),
    }
    body = _postman_body(req)
    if body:
        request["body"] = body
    return request


def _postman_url(req: NetworkRequest) -> dict[str, Any]:
    parsed = urlparse(req.url)
    host = [part for part in parsed.hostname.split(".")] if parsed.hostname else []
    path_parts = [unquote(part) for part in parsed.path.split("/") if part != ""]
    query = [{"key": q.name, "value": q.value} for q in req.query]
    if not query and parsed.query:
        query = [{"key": k, "value": v} for k, v in parse_qsl(parsed.query, keep_blank_values=True)]

    url: dict[str, Any] = {
        "raw": req.url,
        "protocol": parsed.scheme or "https",
        "host": host,
        "path": path_parts,
    }
    if parsed.port:
        url["port"] = str(parsed.port)
    if query:
        url["query"] = query
    if parsed.fragment:
        url["hash"] = parsed.fragment
    return url


def _postman_body(req: NetworkRequest) -> dict[str, Any] | None:
    mime = (req.body_mime or _content_type(req.headers) or "").lower()
    text = req.body_text

    if "application/x-www-form-urlencoded" in mime:
        params = req.body_params or [
            FormParam(name=k, value=v) for k, v in parse_qsl(text or "", keep_blank_values=True)
        ]
        if not params and not text:
            return None
        return {
            "mode": "urlencoded",
            "urlencoded": [{"key": p.name, "value": p.value, "type": "text"} for p in params],
        }

    if "multipart/form-data" in mime:
        formdata = []
        for param in req.body_params:
            entry: dict[str, Any] = {"key": param.name, "type": "file" if param.file_name else "text"}
            if param.file_name:
                entry["src"] = param.file_name
            else:
                entry["value"] = param.value
            formdata.append(entry)
        if not formdata and text:
            return {"mode": "raw", "raw": text, "options": {"raw": {"language": "text"}}}
        if not formdata:
            return None
        return {"mode": "formdata", "formdata": formdata}

    if not text:
        return None

    if _looks_like_graphql(req.url, mime, text):
        graphql = _graphql_body(text)
        if graphql:
            return {"mode": "graphql", "graphql": graphql}

    language = "json" if "json" in mime or _looks_like_json(text) else "text"
    pretty = _pretty_json(text) if language == "json" else text
    return {
        "mode": "raw",
        "raw": pretty,
        "options": {"raw": {"language": language}},
    }


def _example_response(
    req: NetworkRequest,
    name: str,
    original_request: dict[str, Any],
    include_browser_headers: bool,
) -> dict[str, Any]:
    headers = [
        {"key": h.name, "value": h.value}
        for h in req.response_headers
        if should_keep_header(h.name, include_browser_headers)
    ]
    body = req.response_body or ""
    if len(body) > MAX_RESPONSE_CHARS:
        body = body[:MAX_RESPONSE_CHARS] + "\n...[truncated]"
    elif _looks_like_json(body):
        body = _pretty_json(body)

    preview = "json" if "json" in (req.response_mime or "").lower() or _looks_like_json(body) else "text"
    status_text = req.status_text or _status_text(req.status)
    return {
        "name": f"{name} [{req.status}]",
        "originalRequest": original_request,
        "status": status_text,
        "code": req.status,
        "header": headers,
        "_postman_previewlanguage": preview,
        "cookie": [],
        "body": body,
    }


def _request_description(req: NetworkRequest, step: SequenceStep | None = None) -> str:
    bits = [f"Captured from `{req.source}`."]
    if step and step.role == "access_token_fetch":
        bits.append("This is the access-token fetch. The test script writes `{{accessToken}}` from the response.")
    if step and step.uses_token_from_step:
        bits.append(f"Uses the access token written from step {step.uses_token_from_step}.")
    if req.status:
        bits.append(f"Trace response: `{req.status} {req.status_text or _status_text(req.status)}`.")
    if req.time_ms:
        bits.append(f"Duration: {req.time_ms:.0f} ms.")
    if req.started:
        bits.append(f"Started: {req.started}.")
    return " ".join(bits)


def _flow_token_variables(flow: FlowAnalysis) -> tuple[dict[str, str], list[dict[str, str]]]:
    token = flow.access_token or ""
    token_type = flow.token_type or "Bearer"
    token_map: dict[str, str] = {}
    for req in flow.requests:
        for header in req.headers:
            if header.name.lower() == "authorization" and token and token in header.value:
                if header.value.lower().startswith("bearer "):
                    token_map[f"{header.name.lower()}:{header.value}"] = "Bearer {{accessToken}}"
                else:
                    token_map[f"{header.name.lower()}:{header.value}"] = "{{accessToken}}"
    variables = [
        {"key": "accessToken", "value": token},
        {"key": "tokenType", "value": token_type},
    ]
    extra_map, extra_vars = _auth_variables(flow.requests)
    for key, value in extra_map.items():
        token_map.setdefault(key, value)
    for var in extra_vars:
        if var["key"] not in {"accessToken", "bearerToken", "tokenType"}:
            variables.append(var)
    return token_map, variables


def _auth_variables(requests: list[NetworkRequest]) -> tuple[dict[str, str], list[dict[str, str]]]:
    """Replace repeated auth header values with collection variables."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    interesting = {"authorization", "x-api-key", "api-key", "x-auth-token", "x-access-token"}
    for req in requests:
        for header in req.headers:
            if header.name.lower() in interesting and header.value:
                counts[(header.name.lower(), header.value)] += 1

    token_map: dict[str, str] = {}
    variables: list[dict[str, str]] = []
    used_keys: set[str] = set()
    for (header_name, value), count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        if count < 1:
            continue
        key = _variable_key(header_name, value, used_keys)
        used_keys.add(key)
        token_map[f"{header_name}:{value}"] = _variable_substitution(header_name, key, value)
        stored = value[7:].strip() if header_name == "authorization" and value.lower().startswith("bearer ") else value
        variables.append({"key": key, "value": stored})
    return token_map, variables


def _variable_key(header_name: str, value: str, used: set[str]) -> str:
    if header_name == "authorization" and value.lower().startswith("bearer "):
        base = "bearerToken"
    elif header_name in {"x-api-key", "api-key"}:
        base = "apiKey"
    else:
        base = re.sub(r"[^a-zA-Z0-9]+", "_", header_name).strip("_") or "token"
    key = base
    index = 2
    while key in used:
        key = f"{base}{index}"
        index += 1
    return key


def _variable_substitution(header_name: str, key: str, original: str) -> str:
    if header_name == "authorization" and original.lower().startswith("bearer "):
        return f"Bearer {{{{{key}}}}}"
    return f"{{{{{key}}}}}"


def _content_type(headers: list[Header]) -> str | None:
    for header in headers:
        if header.name.lower() == "content-type":
            return header.value
    return None


def _looks_like_json(text: str) -> bool:
    stripped = (text or "").lstrip()
    if not stripped or stripped[0] not in "{[":
        return False
    try:
        json.loads(text)
        return True
    except (TypeError, json.JSONDecodeError):
        return False


def _pretty_json(text: str) -> str:
    try:
        return json.dumps(json.loads(text), indent=2, ensure_ascii=False)
    except (TypeError, json.JSONDecodeError):
        return text


def _looks_like_graphql(url: str, mime: str, text: str) -> bool:
    if "graphql" in (url or "").lower() or "graphql" in mime:
        return True
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and ("query" in payload or "mutation" in payload)


def _graphql_body(text: str) -> dict[str, str] | None:
    try:
        payload = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    variables = payload.get("variables")
    if variables is not None and not isinstance(variables, str):
        variables = json.dumps(variables, indent=2, ensure_ascii=False)
    return {
        "query": payload.get("query") or payload.get("mutation") or "",
        "variables": variables or "",
    }


def _status_text(code: int) -> str:
    return {
        200: "OK",
        201: "Created",
        204: "No Content",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        500: "Internal Server Error",
    }.get(code, "")
