"""Persist HTTP attempts before sending, including bounded, redacted response previews."""

import asyncio
import json
from time import perf_counter

import httpx

from .diagnostics import REDACTED, sensitive
from .store import encode, utcnow


def previous_delivery(store, diagnostics, delivery, limit):
    original = store.one(
        """SELECT id FROM http_exchanges WHERE job_id=? AND record_key=? AND method IN ('POST','PATCH')
        AND state!='blocked' ORDER BY id DESC LIMIT 1""",
        (delivery["job_id"], delivery["record_key"]),
    )
    url = delivery["destination"].split("|")[1]
    request = httpx.Request("POST", url, json={"values": json.loads(delivery["payload"])})
    safe_url, clean, _ = diagnostics.cleaner(request)
    saved = store.exchange(original["id"])["details"]["request"] if original else None
    return {
        "job_id": delivery["job_id"],
        "record_key": delivery["record_key"],
        "updated_at": delivery["updated_at"],
        "url": saved["url"] if saved else safe_url,
        "method": saved["method"] if saved else "POST",
        "error": clean(delivery["note"] or "이전 실행에서 상세 오류를 저장하지 않았습니다."),
        "request_body": saved["body"]
        if saved
        else diagnostics.body_text(request, request.content[:limit].decode("utf-8", errors="replace")),
        "body_truncated": saved["body_truncated"] if saved else len(request.content) > limit,
        "exchange_id": original["id"] if original else None,
    }


class HTTPExchange:
    def __init__(self, store, diagnostics, request, *, job_id, record_key, stage, attempt, limit):
        self.store, self.diagnostics, self.request = store, diagnostics, request
        self.limit = limit
        self.started = perf_counter()
        self.body = bytearray()
        self.received_bytes = 0
        self.response = None
        url, self.clean, _ = diagnostics.cleaner(request)
        content = request.content
        self.details = {
            "attempt": attempt,
            "request": {
                "method": request.method,
                "url": url,
                "query": [
                    [key, REDACTED if sensitive(key) else self.clean(value)]
                    for key, value in request.url.params.multi_items()
                ],
                "headers": self.headers(request.headers),
                "body": self.body_text(content[:limit]),
                "body_bytes": len(content),
                "body_truncated": len(content) > limit,
            },
            "response": None,
            "error": None,
        }
        self.id = store.execute(
            """INSERT INTO http_exchanges(job_id,record_key,started_at,stage,method,url,state,details)
            VALUES(?,?,?,?,?,?,'sending',?)""",
            (job_id, record_key, utcnow(), stage, request.method, url, encode(self.details)),
        )

    def headers(self, headers):
        return {key: REDACTED if sensitive(key) else self.clean(value) for key, value in headers.items()}

    def body_text(self, data, encoding="utf-8"):
        try:
            text = bytes(data).decode(encoding, errors="replace")
        except LookupError:
            text = bytes(data).decode("utf-8", errors="replace")
        return self.diagnostics.body_text(self.request, text)

    def receive(self, response):
        self.response = response
        self.details["response"] = {
            "status_code": response.status_code,
            "reason": self.clean(response.reason_phrase),
            "headers": self.headers(response.headers),
            "body": "",
            "received_bytes": 0,
            "body_truncated": False,
        }
        # Preserve the received HTTP status even if the process stops while reading the body.
        self.store.execute(
            "UPDATE http_exchanges SET status_code=?,details=? WHERE id=?",
            (response.status_code, encode(self.details), self.id),
        )

    def capture(self, chunk):
        self.received_bytes += len(chunk)
        self.body.extend(chunk[: max(0, self.limit - len(self.body))])

    def finish(self, error=None, *, state=None):
        if self.response is not None:
            self.details["response"].update(
                body=self.body_text(self.body, self.response.encoding or "utf-8"),
                received_bytes=self.received_bytes,
                body_truncated=self.received_bytes > self.limit,
            )
        if error:
            self.details["error"] = self.clean(f"{type(error).__name__}: {error}")
        state = state or (
            "interrupted" if isinstance(error, asyncio.CancelledError) else "failed" if error else "completed"
        )
        self.store.execute(
            "UPDATE http_exchanges SET state=?,finished_at=?,duration_ms=?,details=? WHERE id=?",
            (state, utcnow(), round((perf_counter() - self.started) * 1000), encode(self.details), self.id),
        )
