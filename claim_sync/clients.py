import asyncio
import hashlib
import json
import re
import ssl
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

import httpx

from .config import Settings
from .diagnostics import RequestDiagnostics, error_body
from .http_log import HTTPExchange, previous_delivery
from .mapping import PRODUCT_FIELDS


class UpstreamError(Exception):
    pass


class OversizedResponse(UpstreamError):
    pass


class AmbiguousDelivery(UpstreamError):
    pass


class DuplicateTarget(UpstreamError):
    """The create was explicitly rejected for the FAR/sample composite key."""


def duplicate_target(body: str) -> bool:
    # Both the error code and column names must match. A generic 400 or a different
    # UNIQUE constraint must never cause an update to an existing record.
    try:
        result = json.loads(body)
    except (ValueError, RecursionError):
        return False
    if not isinstance(result, dict) or result.get("ok") is not False:
        return False
    error = result.get("error")
    if not isinstance(error, dict):
        return False
    code = error.get("code")
    if not isinstance(code, str) or re.sub(r"\s+", "", code.upper()) not in {
        "CREATE_FAIELD,UNIQUE",
        "CREATE_FAILED,UNIQUE",
    }:
        return False
    for name in ("massage", "message"):
        message = error.get(name)
        if not isinstance(message, str):
            continue
        match = re.fullmatch(r"UNIQUE\s+constraint\s+failed:\s*(.+)", message.strip(), re.IGNORECASE)
        if not match:
            continue
        columns = [column.strip().lower() for column in match[1].split(",")]
        if (
            len(columns) == 2
            and all(re.fullmatch(r"far_tabl(?:e)?\.(?:far_no|sample_no)", column) for column in columns)
            and {column.split(".")[1] for column in columns} == {"far_no", "sample_no"}
        ):
            return True
    return False


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
    def __init__(self, settings: Settings, store, event, transport=None, *, job_id=None):
        self.settings = settings
        self.store, self.job_id = store, job_id
        self.event = event
        self.diagnostics = RequestDiagnostics(
            settings.claims_headers, settings.product_headers, settings.target_headers
        )
        if transport is None and settings.app_mode == "mock":
            from .mock import create_mock_app

            transport = httpx.ASGITransport(app=create_mock_app(settings, store))
        verify = settings.tls_verify
        if settings.ca_bundle:
            if Path(settings.ca_bundle).suffix.lower() in {".pfx", ".p12"}:
                raise UpstreamError(
                    "CA_BUNDLE에는 PEM 형식의 CA 인증서가 필요합니다. "
                    "Windows에서 export-ca.bat로 PFX/P12의 CA 인증서를 추출한 뒤 "
                    "CA_BUNDLE=certs/corporate-ca.pem으로 지정하고 재시작하세요."
                )
            try:
                verify = ssl.create_default_context(cafile=settings.ca_bundle)
            except (OSError, ValueError) as exc:
                raise UpstreamError(
                    f"CA_BUNDLE 인증서를 읽을 수 없습니다: {settings.ca_bundle}\n"
                    f"원인: {type(exc).__name__}: {exc}\n"
                    "PEM 파일 경로, 읽기 권한과 CA 인증서 내용을 확인하세요."
                ) from exc
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

    @asynccontextmanager
    async def exchange(self, label, method, url, *, attempt=1, record_key=None, **kwargs):
        request = self.client.build_request(method, url, **kwargs)
        log = HTTPExchange(
            self.store,
            self.diagnostics,
            request,
            job_id=self.job_id,
            record_key=record_key,
            stage=label,
            attempt=attempt,
            limit=self.settings.http_log_body_bytes,
        )
        response = None
        try:
            response = await self.client.send(request, stream=True)
            log.receive(response)
            yield response, log
        except BaseException as exc:
            log.finish(exc)
            raise
        else:
            log.finish()
        finally:
            if response is not None:
                await response.aclose()

    async def error_preview(self, response, log):
        return await error_body(response, limit=self.settings.http_log_body_bytes, capture=log.capture)

    def blocked(self, values, record_key, delivery):
        request = self.client.build_request("POST", self.settings.target_url, json={"values": values})
        log = HTTPExchange(
            self.store,
            self.diagnostics,
            request,
            job_id=self.job_id,
            record_key=record_key,
            stage="이전 전송 확인 필요",
            attempt=0,
            limit=self.settings.http_log_body_bytes,
        )
        log.details["original"] = previous_delivery(
            self.store, self.diagnostics, delivery, self.settings.http_log_body_bytes
        )
        message = (
            "이 업무 키의 이전 전송 결과가 불확실하여 이번 POST는 보내지 않았습니다.\n"
            f"업무 키: {record_key}\n이전 실행: {delivery['job_id']} · {delivery['updated_at']}\n"
            f"원래 오류: {log.details['original']['error']}\n"
            "우측 요청·응답 상세에서 이전 기록을 확인하고 전송 확인 대기 목록에 반영 여부를 기록하세요."
        )
        log.finish(UpstreamError(message), state="blocked")
        return message

    async def get(self, label: str, url: str, headers: dict, params=None):
        request = self.client.build_request("GET", url, headers=headers, params=params)
        for attempt in range(self.settings.get_retries + 1):
            try:
                async with self.exchange(
                    label, "GET", url, headers=headers, params=params, attempt=attempt + 1
                ) as (response, log):
                    request = response.request
                    if not 200 <= response.status_code < 300:
                        message = self.diagnostics.message(
                            label,
                            request,
                            f"HTTP {response.status_code}",
                            response=response,
                            body=await self.error_preview(response, log),
                        )
                    if response.status_code in {408, 429, 500, 502, 503, 504}:
                        raise httpx.HTTPStatusError(message, request=request, response=response)
                    if not 200 <= response.status_code < 300:
                        raise UpstreamError(message)
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        log.capture(chunk)
                        data.extend(chunk)
                        if len(data) > self.settings.max_response_bytes:
                            raise OversizedResponse(
                                self.diagnostics.message(
                                    label,
                                    request,
                                    "응답 크기가 MAX_RESPONSE_BYTES를 초과했습니다.",
                                    response=response,
                                )
                            )
                    try:
                        return json.loads(data)
                    except (ValueError, UnicodeError) as exc:
                        raise UpstreamError(
                            self.diagnostics.message(
                                label,
                                request,
                                "유효한 JSON 응답이 아닙니다.",
                                response=response,
                                body=data.decode("utf-8", errors="replace"),
                            )
                        ) from exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                message = (
                    str(exc)
                    if isinstance(exc, httpx.HTTPStatusError)
                    else self.diagnostics.message(label, request, f"{type(exc).__name__}: {exc}")
                )
                if attempt == self.settings.get_retries:
                    raise UpstreamError(f"{message}\n조회 재시도 소진 (총 {attempt + 1}회 요청)") from exc
                self.event(
                    "warning",
                    "retry",
                    f"{message}\n조회 재시도 {attempt + 1}/{self.settings.get_retries}",
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

    async def send(self, values: dict, fingerprint: str, *, record_key=None):
        s = self.settings
        if not s.can_write:
            raise UpstreamError(
                "실제 전송 잠금: ALLOW_LIVE_WRITES 및 TARGET_UPSERT_CONFIRMED 설정을 확인하세요."
            )
        try:
            await self._write("POST", {"values": values}, fingerprint, record_key=record_key)
        except DuplicateTarget as exc:
            where = {field: values.get(field) for field in ("far_no", "sample_no")}
            if any(not isinstance(value, str) or not value.strip() for value in where.values()):
                raise UpstreamError(
                    f"{exc}\nPATCH 수정 조건인 far_no와 sample_no가 모두 필요합니다. 수정 요청은 보내지 않았습니다."
                ) from exc
            updates = {field: value for field, value in values.items() if field not in where}
            if not updates:
                raise UpstreamError(
                    f"{exc}\nPATCH로 수정할 컬럼이 없습니다. 수정 요청은 보내지 않았습니다."
                ) from exc
            self.event("info", "patch", f"{exc}\n동일한 far_no·sample_no의 기존 행을 PATCH로 갱신합니다.")
            # A server may share its idempotency cache across methods. PATCH must not
            # reuse the rejected POST's response or request fingerprint.
            patch_key = hashlib.sha256(f"{fingerprint}:PATCH".encode()).hexdigest()
            await self._write("PATCH", {"where": where, "values": updates}, patch_key, record_key=record_key)

    async def _write(self, method: str, payload: dict, operation_key: str, *, record_key=None):
        s = self.settings
        headers = dict(s.target_headers)
        if s.target_idempotency_header:
            headers[s.target_idempotency_header] = operation_key
        request = self.client.build_request(method, s.target_url, headers=headers)
        label = "수정 전송" if method == "PATCH" else "전송"
        try:
            # Neither write method is retried after an uncertain response. Only the
            # explicitly rejected duplicate POST may transition once to PATCH.
            async with self.exchange(
                "FAR 수정" if method == "PATCH" else "FAR 전송",
                method,
                s.target_url,
                headers=headers,
                json=payload,
                record_key=record_key,
            ) as (response, log):
                request = response.request
                if response.status_code >= 500 or response.status_code in {202, 408, 429}:
                    raise AmbiguousDelivery(
                        self.diagnostics.message(
                            label,
                            request,
                            "수신 여부를 운영 서버에서 확인하세요. 자동 재전송 보류.",
                            response=response,
                            body=await self.error_preview(response, log),
                        )
                    )
                if not 200 <= response.status_code < 300:
                    preview = await self.error_preview(response, log)
                    rejection = (
                        DuplicateTarget
                        if method == "POST" and response.status_code == 400 and duplicate_target(preview)
                        else UpstreamError
                    )
                    raise rejection(
                        self.diagnostics.message(
                            f"{label} 거절",
                            request,
                            f"HTTP {response.status_code}",
                            response=response,
                            body=preview,
                        )
                    )

                def uncertain(detail, body=None):
                    return AmbiguousDelivery(
                        self.diagnostics.message(
                            label,
                            request,
                            detail,
                            response=response,
                            body=body,
                        )
                    )

                data = bytearray()
                async for chunk in response.aiter_bytes():
                    log.capture(chunk)
                    data.extend(chunk)
                    if len(data) > s.max_response_bytes:
                        raise uncertain("전송 응답 크기 초과: 수신 여부 확인 필요")
                if not data:
                    if s.target_success_path:
                        raise uncertain("전송 확인 응답이 비어 있습니다.", "")
                    return
                try:
                    body = json.loads(data)
                except (ValueError, UnicodeError) as exc:
                    raise uncertain(
                        "전송 후 JSON 확인 응답을 해석할 수 없습니다.", data.decode("utf-8", errors="replace")
                    ) from exc
                if isinstance(body, dict) and (
                    body.get("success") is False or body.get("ok") is False or body.get("error")
                ):
                    raise uncertain(
                        "전송 응답에 애플리케이션 오류가 있습니다. 반영 여부 확인 필요", json.dumps(body)
                    )
                if s.target_success_path:
                    try:
                        accepted = at_path(body, s.target_success_path) == json.loads(s.target_success_value)
                    except (UpstreamError, ValueError):
                        accepted = False
                    if not accepted:
                        raise uncertain("전송 성공 확인 조건이 일치하지 않습니다.", json.dumps(body))
        except httpx.TransportError as exc:
            raise AmbiguousDelivery(
                self.diagnostics.message(
                    label, request, f"{type(exc).__name__}: {exc}\n자동 재전송을 보류했습니다."
                )
            ) from exc
