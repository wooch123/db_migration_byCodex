"use strict";

const inspector = {
  jobId: null, recordKey: null, offset: 0, selectedId: null, detail: null,
  listKey: "", detailVersion: 0, listVersion: 0,
  legacy: [],
};
const httpStates = {
  sending: "응답 대기", completed: "응답 수신", failed: "실패",
  blocked: "전송 보류", interrupted: "중단됨",
};

function resetInspector() {
  inspector.jobId = null;
  inspector.recordKey = null;
  inspector.offset = 0;
  inspector.selectedId = null;
  inspector.detail = null;
  inspector.detailVersion++;
  inspector.listVersion++;
  inspector.listKey = "";
  inspector.legacy = [];
  $("inspector-follow").checked = true;
}
function setInspectorOpen(open) {
  $("http-inspector").hidden = !open;
  document.body.classList.toggle("inspector-open", open);
  $("open-inspector").setAttribute("aria-expanded", String(open));
}
async function openInspector(jobId = selectedJob, recordKey = null, scope = "selected", exchangeId = null) {
  resetInspector();
  inspector.jobId = jobId;
  inspector.recordKey = recordKey;
  $("inspector-scope").value = scope;
  $("inspector-errors").checked = false;
  setInspectorOpen(true);
  if (exchangeId !== null) {
    inspector.selectedId = exchangeId;
    $("inspector-follow").checked = false;
  }
  await refreshInspector();
}
function inspectorQuery() {
  const query = new URLSearchParams({ offset: inspector.offset, limit: 30 });
  const scope = $("inspector-scope").value;
  const jobId = inspector.jobId || selectedJob;
  if (scope === "selected") {
    if (!jobId) return null;
    query.set("job_id", jobId);
  }
  if (scope === "checks") query.set("checks_only", "true");
  if (inspector.recordKey !== null) query.set("record_key", inspector.recordKey);
  if ($("inspector-errors").checked) query.set("errors_only", "true");
  return query.toString();
}
function httpStatus(row) {
  if (row.state === "blocked") return "전송 안 함";
  return row.status_code !== null ? `HTTP ${row.status_code}` : row.state === "sending" ? "응답 대기" : "응답 없음";
}
function emptyInspector(message) {
  $("http-request-list").innerHTML = `<p class="inspector-empty">${esc(message)}</p>`;
  $("http-request-detail").innerHTML = `<p class="inspector-empty">${esc(message)}</p>`;
  inspector.selectedId = null;
  inspector.detail = null;
  inspector.detailVersion++;
  $("copy-http-detail").disabled = true;
  $("download-http-detail").disabled = true;
}
async function refreshInspector() {
  if ($("http-inspector").hidden) return;
  const version = ++inspector.listVersion;
  const query = inspectorQuery();
  if (query === null) {
    emptyInspector("실행을 선택하거나 조회 범위를 전체 실행으로 바꾸세요.");
    return;
  }
  try {
    const data = await api(`/api/http-exchanges?${query}`);
    if (version !== inspector.listVersion || query !== inspectorQuery() || $("http-inspector").hidden) return;
    $("inspector-error").hidden = true;
    const scope = $("inspector-scope").value;
    const jobId = inspector.jobId || selectedJob;
    $("inspector-context").textContent =
      (scope === "selected" ? `실행 #${jobId.slice(0, 8)}` : scope === "checks" ? "연결 확인 요청" : "모든 실행의 요청") +
      (inspector.recordKey ? ` · 업무 키 ${inspector.recordKey}` : "") + " · 최신순";
    $("inspector-count").textContent = `${number(data.total)}건 · ${data.total ? inspector.offset + 1 : 0}–${Math.min(inspector.offset + 30, data.total)}`;
    $("inspector-prev").disabled = inspector.offset === 0;
    $("inspector-next").disabled = inspector.offset + 30 >= data.total;
    if (!data.items.length) {
      if (scope === "selected" && jobId === selectedJob &&
          selectedJobSpec?.source_type === "csv" && selectedJobSpec.dry_run) {
        emptyInspector("CSV 검증 실행은 외부 API를 호출하지 않습니다. 행별 전송 JSON은 데이터 미리보기에서 확인하세요.");
        return;
      }
      if (scope === "selected") {
        const filter = inspector.recordKey !== null ? `?record_key=${encodeURIComponent(inspector.recordKey)}` : "";
        const legacy = await api(`/api/jobs/${jobId}/delivery-context${filter}`);
        if (version !== inspector.listVersion || query !== inspectorQuery()) return;
        inspector.legacy = legacy;
        if (legacy.length) {
          const key = `legacy:${query}:${JSON.stringify(legacy)}`;
          if (inspector.listKey !== key) {
            inspector.listKey = key;
            $("http-request-list").innerHTML = legacy.map((row, index) =>
              `<button class="http-request blocked" data-legacy-index="${index}"><strong>이전 전송 기록 · ${esc(row.record_key)}</strong><span class="http-request-meta">${formatDate(row.updated_at)} · 기존 오류 확인</span></button>`).join("");
            renderLegacy(legacy[0]);
          }
          return;
        }
      }
      if (scope === "selected" && data.total === 0 && jobId === selectedJob &&
          selectedJobSpec?.source_type === "csv" && selectedJobSummary?.id === jobId &&
          selectedJobSummary.status === "completed" && selectedJobSummary.fetched > 0 &&
          selectedJobSummary.processed === selectedJobSummary.fetched &&
          selectedJobSummary.skipped === selectedJobSummary.fetched &&
          selectedJobSummary.succeeded === 0 && selectedJobSummary.failed === 0 &&
          selectedJobSummary.uncertain === 0) {
        emptyInspector("모든 CSV 행이 변경 없음 또는 전송 제외로 처리되어 API를 호출하지 않았습니다. 행별 결과에서 제외 사유와 입력값을 확인하세요.");
        return;
      }
      emptyInspector("저장된 HTTP 기록이 없습니다. 업데이트 이전 실행은 기존 오류와 전송 데이터만 남아 있을 수 있습니다.");
      return;
    }
    if ($("inspector-follow").checked || inspector.selectedId === null) {
      inspector.selectedId = data.items[0].id;
    }
    const listKey = JSON.stringify([data.items, inspector.selectedId]);
    if (listKey !== inspector.listKey) {
      inspector.listKey = listKey;
      $("http-request-list").innerHTML = data.items.map((row) =>
        `<button class="http-request ${row.state} ${row.id === inspector.selectedId ? "selected" : ""}" data-http-id="${row.id}" aria-pressed="${row.id === inspector.selectedId}">
          <span class="http-request-title"><strong>${esc(row.method)} · ${esc(row.stage)}</strong><span>${esc(httpStatus(row))}</span></span>
          <span class="http-request-url">${esc(row.url)}</span>
          <span class="http-request-meta">${formatDate(row.started_at, { second: "2-digit" })} · ${esc(httpStates[row.state])}${row.duration_ms !== null ? ` · ${number(row.duration_ms)} ms` : ""}</span>
        </button>`).join("");
    }
    if (inspector.detail?.id !== inspector.selectedId || inspector.detail?.state === "sending") {
      await loadHTTPDetail(inspector.selectedId);
    }
  } catch (error) {
    $("inspector-error").hidden = false;
    $("inspector-error").textContent = error.message;
  }
}
function traceBlock(title, body, extra = "", open = true) {
  return `<details class="trace-block" ${open ? "open" : ""}><summary>${esc(title)}</summary>${extra}<pre>${esc(body || "(없음)")}</pre></details>`;
}
function renderLegacy(original) {
  inspector.detail = { legacy: true, record_key: original.record_key, details: { original } };
  $("http-request-detail").innerHTML =
    `<section class="trace-original"><h3>이전 전송의 원래 오류</h3><p>실행 #${esc(original.job_id.slice(0, 8))} · ${formatDate(original.updated_at)}</p>
    <p>이전 버전의 보관 기록입니다. 저장되지 않은 HTTP 응답 코드·본문은 복원할 수 없습니다.</p>
    <pre>${esc(original.error)}</pre>${original.exchange_id ? '<button class="button secondary small" id="inspect-original">원래 요청·응답 열기 →</button>' : ""}</section>` +
    traceBlock("기존 전송 대상", `${original.method || "POST"} ${original.url}`) +
    traceBlock(`이전 실행의 ${original.method || "POST"} 데이터`, original.request_body,
      original.body_truncated ? '<p class="trace-body-note">저장 한도에 따라 본문 일부만 표시합니다.</p>' : "");
  $("copy-http-detail").disabled = false;
  $("download-http-detail").disabled = false;
}
function bodyNote(part, request = false) {
  if (!part) return "";
  const bytes = request ? part.body_bytes : part.received_bytes;
  return `<p class="trace-body-note">${request ? "요청 본문" : "수신한 본문"} ${number(bytes)} bytes${part.body_truncated ? " · 저장 한도 초과로 일부만 표시합니다. HTTP_LOG_BODY_BYTES로 한도를 조정할 수 있습니다." : ""}</p>`;
}
async function loadHTTPDetail(id) {
  const version = ++inspector.detailVersion;
  const row = await api(`/api/http-exchanges/${id}`);
  if (version !== inspector.detailVersion || id !== inspector.selectedId) return;
  const changed = inspector.detail?.id !== row.id;
  inspector.detail = row;
  const d = row.details, req = d.request, res = d.response;
  const original = d.original;
  const queryText = req.query.map(([key, value]) => `${key} = ${value}`).join("\n");
  $("http-request-detail").innerHTML =
    `<div class="trace-summary ${row.state}"><strong>${esc(httpStatus(row))}${res?.reason ? ` ${esc(res.reason)}` : ""}</strong>
      <p>${esc(httpStates[row.state])} · ${row.duration_ms !== null ? `${number(row.duration_ms)} ms` : "진행 중"} · ${d.attempt ? `${d.attempt}번째 요청` : "이번 실행에서 요청하지 않음"}</p>
      <p>기록 #${row.id} · ${row.job_id ? `실행 #${esc(row.job_id.slice(0, 8))}` : "연결 확인"}${row.record_key ? ` · ${esc(row.record_key)}` : ""}</p></div>` +
    (original ? `<section class="trace-original"><h3>원래 전송에서 발생한 오류</h3><p>실행 #${esc(original.job_id.slice(0, 8))} · ${formatDate(original.updated_at)}</p>
      <pre>${esc(original.error)}</pre>${original.exchange_id ? '<button class="button secondary small" id="inspect-original">원래 요청·응답 열기 →</button>' : '<p>이전 버전에서 HTTP 상세를 저장하지 않았습니다. 남아 있는 원래 오류와 전송 데이터를 표시합니다.</p>'}
      ${traceBlock(`이전 실행의 ${original.method || "POST"} 데이터`, original.request_body, original.body_truncated ? '<p class="trace-body-note">본문 일부만 표시합니다.</p>' : "", false)}</section>` : "") +
    (d.error ? traceBlock("오류 상세", d.error) : "") +
    traceBlock(row.state === "blocked" ? "이번에 보내려던 요청 URL" : "요청 URL", `${req.method} ${req.url}`) +
    traceBlock("GET / URL 조회 조건", queryText, "", !!req.query.length) +
    traceBlock(row.state === "blocked" ? `보류한 ${req.method} 데이터 (미전송)` : `${req.method} 요청 데이터`, req.body || (req.method === "GET" ? "GET 요청은 본문 없이 위 URL 조회 조건으로 전달했습니다." : "(빈 본문)"), bodyNote(req, true), req.method !== "GET") +
    traceBlock("요청 헤더", JSON.stringify(req.headers, null, 2), "", false) +
    (res ? traceBlock("실제 서버 응답 본문", res.body || "(빈 응답 본문)", bodyNote(res)) +
      traceBlock("서버 응답 헤더", JSON.stringify(res.headers, null, 2), "", false)
      : `<p class="trace-no-response">${row.state === "blocked" ? "이번 실행에서는 API를 호출하지 않아 응답 코드와 본문이 없습니다." : row.state === "sending" ? "서버 응답을 기다리고 있습니다." : "HTTP 응답을 받지 못했습니다. 위 연결·SSL 오류를 확인하세요."}</p>`);
  if (changed) $("http-request-detail").scrollTop = 0;
  $("copy-http-detail").disabled = false;
  $("download-http-detail").disabled = false;
}

$("open-inspector").addEventListener("click", () => openInspector());
$("inspect-job").addEventListener("click", () => openInspector());
$("close-inspector").addEventListener("click", () => {
  setInspectorOpen(false);
  $("open-inspector").focus();
});
$("toggle-http-list").addEventListener("click", () => {
  const collapsed = $("http-inspector").classList.toggle("list-collapsed");
  $("toggle-http-list").textContent = collapsed ? "목록 펼치기" : "목록 접기";
  $("toggle-http-list").setAttribute("aria-expanded", String(!collapsed));
});
$("http-inspector").addEventListener("keydown", (event) => {
  if (event.key === "Escape") $("close-inspector").click();
});
$("inspector-scope").addEventListener("change", () => { resetInspector(); refreshInspector(); });
$("inspector-reset").addEventListener("click", () => openInspector());
$("inspector-errors").addEventListener("change", () => {
  inspector.offset = 0; inspector.selectedId = null; refreshInspector();
});
$("inspector-follow").addEventListener("change", () => {
  if ($("inspector-follow").checked) inspector.offset = 0;
  refreshInspector();
});
$("inspector-prev").addEventListener("click", () => {
  inspector.offset = Math.max(0, inspector.offset - 30); inspector.selectedId = null;
  $("inspector-follow").checked = false; refreshInspector();
});
$("inspector-next").addEventListener("click", () => {
  inspector.offset += 30; inspector.selectedId = null;
  $("inspector-follow").checked = false; refreshInspector();
});
$("http-request-list").addEventListener("click", (event) => {
  const legacy = event.target.closest("[data-legacy-index]");
  if (legacy) { renderLegacy(inspector.legacy[Number(legacy.dataset.legacyIndex)]); return; }
  const button = event.target.closest("[data-http-id]");
  if (!button) return;
  inspector.selectedId = Number(button.dataset.httpId);
  $("inspector-follow").checked = false;
  refreshInspector();
});
$("http-request-detail").addEventListener("click", (event) => {
  if (!event.target.closest("#inspect-original")) return;
  const original = inspector.detail?.details.original;
  if (original) openInspector(original.job_id, inspector.detail.record_key, "selected", original.exchange_id);
});
$("inspect-payload").addEventListener("click", () => {
  if (!payloadTraceContext) return;
  $("payload-dialog").close();
  openInspector(payloadTraceContext.jobId, payloadTraceContext.recordKey);
});
$("unresolved-list").addEventListener("click", (event) => {
  const button = event.target.closest("[data-unresolved-trace]");
  if (!button) return;
  const row = unresolved[Number(button.dataset.unresolvedTrace)];
  openInspector(row.job_id, row.record_key);
});
$("copy-http-detail").addEventListener("click", () => action($("copy-http-detail"), async () => {
  await navigator.clipboard.writeText(JSON.stringify(inspector.detail, null, 2));
  toast("요청·응답 상세를 복사했습니다.");
}));
$("download-http-detail").addEventListener("click", () => {
  if (!inspector.detail) return;
  const url = URL.createObjectURL(new Blob([JSON.stringify(inspector.detail, null, 2)], { type: "application/json" }));
  const link = document.createElement("a"); link.href = url;
  link.download = `http-request-${inspector.detail.id || "previous"}.json`; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
setInspectorOpen(window.matchMedia("(min-width: 1200px)").matches);
if (state) refreshInspector();
