import asyncio
import json
import ssl
from urllib.parse import quote

import httpx

from .config import Settings
from .mapping import PRODUCT_FIELDS


class UpstreamError(Exception):
    pass


class OversizedResponse(UpstreamError):
    pass


class AmbiguousDelivery(UpstreamError):
    pass


def at_path(value, path: str):
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            raise UpstreamError(f"JSON 경로를 찾을 수 없습니다: {path}")
        value = value[key]
    return value


def claim_rows(body, path="auto") -> tuple[list[dict], bool]:
    value = body
    envelopes = [body]
    if path != "auto":
        for key in path.split("."):
            value = at_path(value, key)
            envelopes.append(value)
    else:
        for _ in range(4):
            if isinstance(value, list):
                break
            if not isinstance(value, dict):
                break
            for key in ("data", "items", "records", "results", "claims"):
                if key in value:
                    value = value[key]
                    envelopes.append(value)
                    break
            else:
                break
    if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
        raise UpstreamError("Claim 응답은 객체 배열이어야 합니다. CLAIMS_RECORDS_PATH를 확인하세요.")
    truncated = False
    for envelope in envelopes:
        if not isinstance(envelope, dict):
            continue
        if envelope.get("hasMore") is True or envelope.get("truncated") is True:
            truncated = True
        for key in ("total", "totalCount", "totalElements"):
            total = envelope.get(key)
            if isinstance(total, (int, float)) and total > len(value):
                truncated = True
    return value, truncated


def product_record(body, path="auto") -> dict:
    value = at_path(body, path) if path != "auto" else body
    for _ in range(4):
        if isinstance(value, dict) and set(PRODUCT_FIELDS) <= value.keys():
            return value
        if isinstance(value, list) and len(value) == 1:
            value = value[0]
        elif isinstance(value, dict):
            for key in ("data", "record", "result", "records", "items"):
                if key in value:
                    value = value[key]
                    break
            else:
                break
        else:
            break
    raise UpstreamError(
        "제품 응답은 필수 8개 필드를 가진 단일 객체여야 합니다. PRODUCT_RECORDS_PATH를 확인하세요."
    )


def schema_fields(body) -> set[str]:
    if isinstance(body, dict):
        if isinstance(body.get("properties"), dict):
            return set(body["properties"])
        for key in ("data", "schema", "fields", "columns"):
            if key in body:
                return schema_fields(body[key])
    if isinstance(body, list):
        fields = set()
        for item in body:
            if isinstance(item, str):
                fields.add(item)
            elif isinstance(item, dict):
                fields.add(item.get("name") or item.get("field") or item.get("column_name") or "")
        return fields
    raise UpstreamError(
        "제품 schema 형식을 해석할 수 없습니다. JSON Schema properties 또는 fields/columns 배열이 필요합니다."
    )


class APIClients:
    def __init__(self, settings: Settings, store, event, transport=None):
        self.settings = settings
        self.event = event
        if transport is None and settings.app_mode == "mock":
            from .mock import create_mock_app

            transport = httpx.ASGITransport(app=create_mock_app(settings, store))
        verify = (
            ssl.create_default_context(cafile=settings.ca_bundle)
            if settings.ca_bundle
            else settings.tls_verify
        )
        self.client = httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            verify=verify,
            trust_env=settings.trust_env_proxy,
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        await self.client.aclose()

    async def get(self, label: str, url: str, headers: dict, params=None):
        for attempt in range(self.settings.get_retries + 1):
            try:
                async with self.client.stream("GET", url, headers=headers, params=params) as response:
                    if response.status_code in {408, 429, 500, 502, 503, 504}:
                        raise httpx.HTTPStatusError("retryable", request=response.request, response=response)
                    if not 200 <= response.status_code < 300:
                        raise UpstreamError(f"{label}: HTTP {response.status_code}")
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > self.settings.max_response_bytes:
                            raise OversizedResponse(
                                f"{label}: 응답 크기가 MAX_RESPONSE_BYTES를 초과했습니다."
                            )
                    try:
                        return json.loads(data)
                    except (ValueError, UnicodeError) as exc:
                        raise UpstreamError(f"{label}: 유효한 JSON 응답이 아닙니다.") from exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                if attempt == self.settings.get_retries:
                    detail = (
                        f"HTTP {exc.response.status_code}"
                        if isinstance(exc, httpx.HTTPStatusError)
                        else type(exc).__name__
                    )
                    raise UpstreamError(f"{label}: {detail}, 조회 재시도 소진") from exc
                self.event(
                    "warning", "retry", f"{label} 조회 재시도 {attempt + 1}/{self.settings.get_retries}"
                )
                delay = min(30, self.settings.retry_backoff_seconds * 2**attempt)
                if isinstance(exc, httpx.HTTPStatusError):
                    hint = exc.response.headers.get("Retry-After", "")
                    if hint.isdigit():
                        delay = min(60, max(delay, int(hint)))
                await asyncio.sleep(delay)

    async def schema(self):
        s = self.settings
        body = await self.get(
            "제품 schema", s.product_base_url.rstrip("/") + s.product_schema_path, s.product_headers
        )
        missing = set(PRODUCT_FIELDS) - schema_fields(body)
        if missing:
            raise UpstreamError(f"제품 schema 필드 누락: {', '.join(sorted(missing))}")
        return body

    async def claims(self, start, end):
        s = self.settings
        body = await self.get(
            "Claim",
            s.claims_base_url.rstrip("/") + s.claims_path,
            s.claims_headers,
            {s.claims_from_param: str(start), s.claims_to_param: str(end), "limit": s.claims_limit},
        )
        return claim_rows(body, s.claims_records_path)

    async def product(self, prefix):
        s = self.settings
        path = s.product_record_path.replace("{part_id}", quote(prefix, safe=""))
        body = await self.get("제품 정보", s.product_base_url.rstrip("/") + path, s.product_headers)
        return product_record(body, s.product_records_path)

    async def send(self, values: dict, fingerprint: str):
        s = self.settings
        if not s.can_write:
            raise UpstreamError(
                "실제 전송 잠금: ALLOW_LIVE_WRITES 및 TARGET_UPSERT_CONFIRMED 설정을 확인하세요."
            )
        headers = dict(s.target_headers)
        if s.target_idempotency_header:
            headers[s.target_idempotency_header] = fingerprint
        try:
            # POST is never retried automatically: a lost response may follow a committed write.
            async with self.client.stream(
                "POST", s.target_url, headers=headers, json={"values": values}
            ) as response:
                if response.status_code >= 500 or response.status_code in {202, 408, 429}:
                    raise AmbiguousDelivery(
                        f"전송 HTTP {response.status_code}: 수신 여부를 운영 서버에서 확인하세요."
                    )
                if not 200 <= response.status_code < 300:
                    raise UpstreamError(f"전송 거절: HTTP {response.status_code}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > s.max_response_bytes:
                        raise AmbiguousDelivery("전송 응답 크기 초과: 수신 여부 확인 필요")
                if not data:
                    if s.target_success_path:
                        raise AmbiguousDelivery("전송 확인 응답이 비어 있습니다.")
                    return
                try:
                    body = json.loads(data)
                except (ValueError, UnicodeError) as exc:
                    raise AmbiguousDelivery("전송 후 JSON 확인 응답을 해석할 수 없습니다.") from exc
                if isinstance(body, dict) and (body.get("success") is False or body.get("error")):
                    raise AmbiguousDelivery("전송 응답에 애플리케이션 오류가 있습니다. 반영 여부 확인 필요")
                if s.target_success_path:
                    try:
                        accepted = at_path(body, s.target_success_path) == json.loads(s.target_success_value)
                    except (UpstreamError, ValueError):
                        accepted = False
                    if not accepted:
                        raise AmbiguousDelivery("전송 성공 확인 조건이 일치하지 않습니다.")
        except httpx.TransportError as exc:
            raise AmbiguousDelivery(f"전송 {type(exc).__name__}: 자동 재전송을 보류했습니다.") from exc
