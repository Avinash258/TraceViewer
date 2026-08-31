"""Break a captured API flow into sequence, starting at the access-token fetch."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlparse

from apiextracter.filters import should_keep_header
from apiextracter.parser import Header, NetworkRequest

TOKEN_RESPONSE_KEYS = (
    "access_token",
    "accessToken",
    "id_token",
    "idToken",
    "refresh_token",
    "refreshToken",
    "token",
    "jwt",
    "bearerToken",
)

TOKEN_URL_HINTS = (
    "token",
    "oauth",
    "login",
    "signin",
    "sign-in",
    "authenticate",
    "authorization",
    "connect/token",
    "auth/token",
    "gettoken",
    "accesstoken",
)

TOKEN_BODY_HINTS = (
    "grant_type",
    "client_id",
    "client_secret",
    "refresh_token",
    "username",
    "password",
)

JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")


@dataclass
class ExtractedToken:
    key: str
    value: str
    json_path: str
    token_type: str = "Bearer"


@dataclass
class SequenceStep:
    seq: int
    role: str
    method: str
    url: str
    path: str
    host: str
    status: int
    status_text: str
    time_ms: float
    started: str | None
    values_used: dict[str, Any]
    extracted_tokens: list[ExtractedToken] = field(default_factory=list)
    uses_token_from_step: int | None = None
    auth_header: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "role": self.role,
            "method": self.method,
            "url": self.url,
            "path": self.path,
            "host": self.host,
            "status": self.status,
            "status_text": self.status_text,
            "time_ms": round(self.time_ms, 1),
            "started": self.started,
            "values_used": self.values_used,
            "extracted_tokens": [
                {
                    "key": t.key,
                    "value": t.value,
                    "json_path": t.json_path,
                    "token_type": t.token_type,
                }
                for t in self.extracted_tokens
            ],
            "uses_token_from_step": self.uses_token_from_step,
            "auth_header": self.auth_header,
        }


@dataclass
class FlowAnalysis:
    requests: list[NetworkRequest]
    steps: list[SequenceStep]
    token_step_index: int | None
    access_token: str | None
    token_type: str
    skipped_before_token: int
    token_found: bool

    def to_dict(self) -> dict[str, Any]:
        token_step = self.steps[self.token_step_index] if self.token_step_index is not None else None
        return {
            "token_found": self.token_found,
            "flow_starts_at": "access_token_fetch" if self.token_found else "first_api",
            "skipped_before_token": self.skipped_before_token,
            "access_token": self.access_token,
            "token_type": self.token_type,
            "token_fetch": token_step.to_dict() if token_step else None,
            "api_count": len(self.steps),
            "sequence": [step.to_dict() for step in self.steps],
        }


def analyze_flow(requests: list[NetworkRequest], *, from_token: bool = True) -> FlowAnalysis:
    """Build a sequential breakup of APIs, optionally starting at the token fetch."""
    token_index, extracted = _find_token_fetch(requests)
    skipped = 0
    sliced = list(requests)
    local_token_index = token_index

    if from_token and token_index is not None:
        skipped = token_index
        sliced = requests[token_index:]
        local_token_index = 0

    access_token = None
    token_type = "Bearer"
    if extracted:
        primary = _primary_token(extracted)
        access_token = primary.value
        token_type = primary.token_type or "Bearer"

    token_by_value = {t.value: t for t in extracted}
    if access_token:
        token_by_value.setdefault(access_token, extracted[0])

    steps: list[SequenceStep] = []
    for offset, req in enumerate(sliced):
        tokens_here = _tokens_from_response(req) if (local_token_index is not None and offset == local_token_index) else _tokens_from_response(req)
        role = _role(req, offset, local_token_index, tokens_here)
        auth = _header_value(req.headers, "authorization")
        uses_from = None
        if access_token and auth and access_token in auth:
            uses_from = (local_token_index + 1) if local_token_index is not None else None

        steps.append(
            SequenceStep(
                seq=offset + 1,
                role=role,
                method=req.method,
                url=req.url,
                path=urlparse(req.url).path or "/",
                host=urlparse(req.url).netloc,
                status=req.status,
                status_text=req.status_text,
                time_ms=req.time_ms,
                started=req.started,
                values_used=_values_used(req, access_token, uses_from),
                extracted_tokens=tokens_here,
                uses_token_from_step=uses_from,
                auth_header=auth,
            )
        )

    return FlowAnalysis(
        requests=sliced,
        steps=steps,
        token_step_index=local_token_index,
        access_token=access_token,
        token_type=token_type,
        skipped_before_token=skipped,
        token_found=token_index is not None,
    )


def render_sequence_markdown(flow: FlowAnalysis, title: str | None = None) -> str:
    lines = [
        f"# {title or 'API sequence'}",
        "",
        f"- APIs in sequence: **{len(flow.steps)}**",
        f"- Access token fetch found: **{'yes' if flow.token_found else 'no'}**",
    ]
    if flow.skipped_before_token:
        lines.append(f"- Calls skipped before token fetch: **{flow.skipped_before_token}**")
    if flow.access_token:
        lines.append(f"- Access token (from fetch): `{_short_token(flow.access_token)}`")
    lines += ["", "The flow below starts from the access-token fetch when one exists.", ""]

    for step in flow.steps:
        heading = f"## {step.seq}. {step.method} {step.path}"
        if step.role == "access_token_fetch":
            heading += "  *(access token fetch)*"
        elif step.uses_token_from_step:
            heading += f"  *(uses token from step {step.uses_token_from_step})*"
        lines.append(heading)
        lines.append("")
        lines.append(f"- URL: `{step.url}`")
        lines.append(f"- Status: `{step.status} {step.status_text}`")
        if step.time_ms:
            lines.append(f"- Duration: {step.time_ms:.0f} ms")
        values = step.values_used
        if values.get("query"):
            lines.append("- Query values:")
            for key, value in values["query"].items():
                lines.append(f"  - `{key}` = `{value}`")
        if values.get("body"):
            lines.append("- Body values used:")
            body = values["body"]
            if isinstance(body, dict):
                for key, value in body.items():
                    lines.append(f"  - `{key}` = `{_fmt_value(value)}`")
            else:
                lines.append(f"  - `{_fmt_value(body)}`")
        if values.get("headers"):
            lines.append("- Headers used:")
            for key, value in values["headers"].items():
                lines.append(f"  - `{key}` = `{value}`")
        if step.extracted_tokens:
            lines.append("- Tokens written from this response:")
            for token in step.extracted_tokens:
                lines.append(
                    f"  - `{token.key}` ({token.json_path}) = `{_short_token(token.value)}`"
                )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _find_token_fetch(requests: list[NetworkRequest]) -> tuple[int | None, list[ExtractedToken]]:
    scored: list[tuple[int, int, list[ExtractedToken]]] = []
    for index, req in enumerate(requests):
        tokens = _tokens_from_response(req)
        score = 0
        path = (urlparse(req.url).path or "").lower()
        if any(hint in path for hint in TOKEN_URL_HINTS):
            score += 4
        if req.method.upper() in {"POST", "PUT"}:
            score += 1
        body_keys = set(_flatten_body(req).keys())
        if body_keys & set(TOKEN_BODY_HINTS):
            score += 3
        if tokens:
            score += 6
            if any(t.key.lower() in {"access_token", "accesstoken"} for t in tokens):
                score += 4
        if score >= 6:
            scored.append((score, index, tokens))
    if not scored:
        return None, []
    scored.sort(key=lambda row: (-row[0], row[1]))
    _, index, tokens = scored[0]
    return index, tokens


def _tokens_from_response(req: NetworkRequest) -> list[ExtractedToken]:
    tokens: list[ExtractedToken] = []
    token_type = _token_type_from_payload(req.response_body) or "Bearer"
    payload = _json_payload(req.response_body)
    if payload is not None:
        _walk_for_tokens(payload, "$", tokens, token_type)
    # Form-urlencoded responses are rare but possible.
    if not tokens and req.response_body and "access_token=" in req.response_body:
        parsed = dict(parse_qsl(req.response_body, keep_blank_values=True))
        for key in TOKEN_RESPONSE_KEYS:
            if parsed.get(key):
                tokens.append(ExtractedToken(key, parsed[key], key, parsed.get("token_type") or token_type))
    return tokens


def _walk_for_tokens(node: Any, path: str, out: list[ExtractedToken], token_type: str, depth: int = 0) -> None:
    if depth > 8:
        return
    if isinstance(node, dict):
        local_type = str(node.get("token_type") or node.get("tokenType") or token_type)
        for key, value in node.items():
            child = f"{path}.{key}"
            if key in TOKEN_RESPONSE_KEYS and isinstance(value, str) and value:
                if key.lower() in {"token", "jwt"} and not _looks_like_token(value):
                    continue
                out.append(ExtractedToken(str(key), value, child, local_type))
            else:
                _walk_for_tokens(value, child, out, local_type, depth + 1)
    elif isinstance(node, list):
        for i, item in enumerate(node[:20]):
            _walk_for_tokens(item, f"{path}[{i}]", out, token_type, depth + 1)


def _looks_like_token(value: str) -> bool:
    if JWT_RE.match(value):
        return True
    if value.lower().startswith("bearer "):
        return True
    return len(value) >= 20 and " " not in value.strip()


def _token_type_from_payload(body: str | None) -> str | None:
    payload = _json_payload(body)
    if isinstance(payload, dict):
        raw = payload.get("token_type") or payload.get("tokenType")
        if raw:
            return str(raw)
    return None


def _primary_token(tokens: list[ExtractedToken]) -> ExtractedToken:
    preferred = ("access_token", "accesstoken", "id_token", "idtoken", "jwt", "token")
    for name in preferred:
        for token in tokens:
            if token.key.lower() == name:
                return token
    return tokens[0]


def _role(req: NetworkRequest, offset: int, token_index: int | None, tokens: list[ExtractedToken]) -> str:
    if token_index is not None and offset == token_index:
        return "access_token_fetch"
    if tokens and any(t.key.lower() in {"access_token", "accesstoken"} for t in tokens):
        return "access_token_fetch"
    if _header_value(req.headers, "authorization"):
        return "authenticated"
    return "api"


def _values_used(req: NetworkRequest, access_token: str | None, uses_from: int | None) -> dict[str, Any]:
    headers: dict[str, str] = {}
    for header in req.headers:
        if not should_keep_header(header.name):
            continue
        name = header.name
        value = header.value
        if name.lower() == "authorization" and access_token and access_token in value:
            prefix = "Bearer " if value.lower().startswith("bearer ") else ""
            note = f" (from step {uses_from} access token fetch)" if uses_from else " (from access token fetch)"
            value = f"{prefix}{{{{accessToken}}}}{note}"
        headers[name] = value

    query = {q.name: q.value for q in req.query}
    body: Any
    flattened = _flatten_body(req)
    if flattened:
        body = flattened
    elif req.body_text:
        parsed = _json_payload(req.body_text)
        body = parsed if parsed is not None else req.body_text
    else:
        body = None

    used: dict[str, Any] = {}
    if query:
        used["query"] = query
    if body not in (None, "", {}):
        used["body"] = body
    if headers:
        used["headers"] = headers
    return used


def _flatten_body(req: NetworkRequest) -> dict[str, Any]:
    if req.body_params:
        return {p.name: p.value for p in req.body_params if p.name}
    text = req.body_text or ""
    mime = (req.body_mime or "").lower()
    if "application/x-www-form-urlencoded" in mime or (text and "grant_type=" in text):
        return {k: v for k, v in parse_qsl(text, keep_blank_values=True)}
    payload = _json_payload(text)
    if isinstance(payload, dict):
        return {str(k): v for k, v in payload.items()}
    return {}


def _json_payload(text: str | None) -> Any:
    if not text:
        return None
    stripped = text.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _header_value(headers: list[Header], name: str) -> str | None:
    needle = name.lower()
    for header in headers:
        if header.name.lower() == needle:
            return header.value
    return None


def _short_token(value: str, keep: int = 18) -> str:
    if len(value) <= keep * 2 + 3:
        return value
    return f"{value[:keep]}...{value[-8:]}"


def _fmt_value(value: Any) -> str:
    if isinstance(value, str):
        if len(value) > 80 and _looks_like_token(value):
            return _short_token(value)
        return value
    try:
        text = json.dumps(value, ensure_ascii=False)
    except TypeError:
        text = str(value)
    if len(text) > 240:
        return text[:240] + "..."
    return text


POSTMAN_SAVE_TOKEN_SCRIPT = """\
const keys = ["access_token", "accessToken", "id_token", "idToken", "token", "jwt", "bearerToken"];
function findToken(node, depth) {
  if (!node || depth > 8) return null;
  if (typeof node === "object") {
    if (!Array.isArray(node)) {
      for (const key of keys) {
        if (typeof node[key] === "string" && node[key]) return node[key];
      }
    }
    const values = Array.isArray(node) ? node : Object.values(node);
    for (const value of values) {
      const found = findToken(value, depth + 1);
      if (found) return found;
    }
  }
  return null;
}
try {
  const json = pm.response.json();
  const token = findToken(json, 0);
  if (token) {
    pm.collectionVariables.set("accessToken", token);
    const type = json.token_type || json.tokenType || "Bearer";
    pm.collectionVariables.set("tokenType", type);
  }
} catch (e) {
  const text = pm.response.text() || "";
  const match = text.match(/access_token=([^&]+)/);
  if (match) pm.collectionVariables.set("accessToken", decodeURIComponent(match[1]));
}
"""
