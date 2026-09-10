"""Bounded HTTP diagnostics shared by the web console and headless runner."""

import json
import re
from urllib.parse import quote, quote_plus, unquote_plus

import httpx

ERROR_BODY_BYTES = 8192
ERROR_TEXT_CHARS = 4096
REDACTED = "[REDACTED]"
SECRET_KEY = r"[\w-]*(?:token|secret|password|passwd|authorization|cookie|api[_-]?key|credential)[\w-]*"
SECRET_ASSIGNMENT = re.compile(
    rf"""(?i)(?<![\w-])(["']?{SECRET_KEY}["']?\s*[:=]\s*)(?:"[^"\n]*(?:"|$)|'[^'\n]*(?:'|$)|[^\s,;&<>}}]+)"""
)


def sensitive(key: str) -> bool:
    return re.fullmatch(SECRET_KEY, key, re.IGNORECASE) is not None


def secret_values(headers: dict, *, configured=False) -> set[str]:
    values = set()
    for key, value in headers.items():
        if not configured and not sensitive(key):
            continue
        if key.lower() in {"accept", "content-type", "user-agent"}:
            continue
        if value:
            values.add(value)
            if key.lower() in {"authorization", "proxy-authorization"}:
                values.add(value.split(" ", 1)[-1])
            if key.lower() == "cookie":
                values.update(part.split("=", 1)[-1].strip() for part in value.split(";"))
    return values - {""}


class RequestDiagnostics:
    def __init__(self, *header_sets: dict):
        self.secrets = set().union(*(secret_values(headers, configured=True) for headers in header_sets))

    def cleaner(self, request):
        secrets = self.secrets | secret_values(dict(request.headers))
        # Keep the actual encoded path, query order and repeated parameters intact.
        url = request.url.copy_with(username=None, password=None, fragment=None)
        query = []
        for part in url.query.decode("ascii").split("&"):
            key, separator, value = part.partition("=")
            if sensitive(unquote_plus(key)):
                secrets.add(unquote_plus(value))
                part = key + separator + REDACTED
            query.append(part)
        if url.query:
            url = url.copy_with(query="&".join(query).encode("ascii"))
        url = str(url)
        variants = set()
        for value in secrets - {""}:
            variants.update((value, quote(value, safe=""), quote_plus(value), json.dumps(value)[1:-1]))

        def clean(text):
            text = str(text)
            for value in sorted(variants, key=len, reverse=True):
                text = text.replace(value, REDACTED)
            text = re.sub(r"(?i)(https?://)[^/\s@]+@", rf"\1{REDACTED}@", text)

            def hide_assignment(match):
                quote_char = match[0][len(match[1]) :][:1]
                value = f"{quote_char}{REDACTED}{quote_char}" if quote_char in {'"', "'"} else REDACTED
                return match[1] + value

            text = SECRET_ASSIGNMENT.sub(hide_assignment, text)
            text = re.sub(r"(?i)\b(Bearer|Basic)\s+[^\s,;<>\"']+", rf"\1 {REDACTED}", text)
            # Prevent terminal escape sequences while retaining readable newlines.
            return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", text)

        def clean_json(value):
            if isinstance(value, dict):
                return {key: REDACTED if sensitive(key) else clean_json(item) for key, item in value.items()}
            if isinstance(value, list):
                return [clean_json(item) for item in value]
            return value

        return clean(url), clean, clean_json

    def body_text(self, request, text):
        _, clean, clean_json = self.cleaner(request)
        try:
            text = json.dumps(clean_json(json.loads(text)), ensure_ascii=False, indent=2)
        except (ValueError, RecursionError):
            pass
        return clean(text)

    def message(self, label, request, detail, *, response=None, body=None):
        url, clean, _ = self.cleaner(request)
        lines = [clean(f"{label}: {detail}"), f"요청: {request.method} {clean(url)}"]
        if response is not None:
            lines.append(f"상태: HTTP {response.status_code} {clean(response.reason_phrase)}")
            for key in ("content-type", "x-request-id", "x-correlation-id", "traceparent"):
                if response.headers.get(key):
                    lines.append(f"{key}: {clean(response.headers[key])[:512]}")
        if body is not None:
            body = self.body_text(request, body)
            if len(body) > ERROR_TEXT_CHARS:
                body = body[:ERROR_TEXT_CHARS] + "\n[응답 일부 생략]"
            lines.append("서버 응답:\n" + (body or "(빈 응답)"))
        return "\n".join(lines)


async def error_body(response: httpx.Response, *, limit=ERROR_BODY_BYTES, capture=None) -> str:
    """Read only a bounded error preview; a broken body must not hide a known HTTP status."""
    data = bytearray()
    suffix = ""
    try:
        async for chunk in response.aiter_bytes(chunk_size=1024):
            if capture:
                capture(chunk)
            remaining = limit - len(data)
            data.extend(chunk[:remaining])
            if len(chunk) > remaining:
                suffix = "\n[응답 일부 생략]"
                break
    except httpx.RequestError as exc:
        suffix = f"\n[응답 본문 수신 실패: {type(exc).__name__}]"
    try:
        text = data.decode(response.encoding or "utf-8", errors="replace")
    except LookupError:
        text = data.decode("utf-8", errors="replace")
    return text + suffix
