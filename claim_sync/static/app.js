"use strict";
const $ = (id) => document.getElementById(id);
const labels = {
  queued: "실행 대기",
  running: "실행 중",
  completed: "완료",
  partial: "일부 실패",
  failed: "실패",
  needs_attention: "확인 필요",
  cancelled: "중지됨",
  interrupted: "중단됨",
  validated: "검증 완료",
  sent: "전송 완료",
  skipped: "변경 없음",
  uncertain: "확인 대기",
  sending: "전송 중",
  split: "자동 분할",
  fetching: "조회 중",
  processing: "처리 중",
};
const viewInfo = {
  dashboard: [
    "동기화 대시보드",
    "Claim 데이터 동기화",
    "접수 이력을 수집하고 제품 정보를 더해, FAR 데이터를 최신 상태로 유지하세요.",
  ],
  history: [
    "실행 이력",
    "동기화 실행 이력",
    "실행별 처리 결과와 오류를 확인하고 필요한 작업을 다시 실행하세요.",
  ],
  schedule: [
    "자동 갱신 스케줄",
    "자동 갱신 스케줄",
    "원하는 기간의 데이터를 정해진 간격으로 자동 갱신하세요.",
  ],
  connections: [
    "API 연결 및 매핑",
    "API 연결 및 필드 매핑",
    "데이터가 어디에서 오고 어떻게 변환되는지 확인하세요.",
  ],
  attention: [
    "전송 확인 대기",
    "전송 결과 확인 대기",
    "응답이 유실된 전송은 실제 반영 여부를 확인한 뒤 처리하세요.",
  ],
};
let state = null,
  selectedJob = null,
  periodMode = "relative",
  currentView = "dashboard",
  events = [],
  rows = [],
  offset = 0,
  total = 0,
  initialized = false,
  polling = false,
  authRequired = false,
  resolving = null,
  unresolved = [];
let token = sessionStorage.getItem("claim-sync-token") || "";
let toastTimer;
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        char
      ],
  );
const number = (value) => Number(value || 0).toLocaleString("ko-KR");
const badge = (status) =>
  `<span class="pill status-${esc(status)}">${esc(labels[status] || status)}</span>`;
function formatDate(value, options = {}) {
  if (!value) return "—";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: state?.timezone || "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
    ...options,
  }).format(new Date(value));
}
function toast(message, error = false) {
  $("toast").textContent = message;
  $("toast").classList.toggle("error", error);
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 5500);
}
async function api(path, method = "GET", data, raw = false) {
  const response = await fetch(path, {
    method,
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...(method !== "GET" ? { "Content-Type": "application/json" } : {}),
    },
    body: method !== "GET" ? JSON.stringify(data ?? {}) : undefined,
  });
  if (response.status === 401) {
    authRequired = true;
    if (!$("auth-dialog").open) $("auth-dialog").showModal();
    throw new Error("접속 토큰을 확인하세요.");
  }
  if (!response.ok) {
    let body;
    try {
      body = await response.json();
    } catch {
      body = { detail: `요청 오류 (${response.status})` };
    }
    throw new Error(
      Array.isArray(body.detail)
        ? body.detail
            .map((x) => `${x.loc.slice(1).join(".")}: ${x.msg}`)
            .join(" / ")
        : body.detail || "요청을 처리할 수 없습니다.",
    );
  }
  return raw ? response : response.json();
}
function runSpec() {
  return {
    period_mode: periodMode,
    months: Number($("months").value),
    start_date: periodMode === "absolute" ? $("start-date").value : null,
    end_date: periodMode === "absolute" ? $("end-date").value : null,
    chunk_days: Number($("chunk-days").value),
    dry_run: $("execution-mode").value === "validate",
  };
}
async function preview() {
  try {
    const p = await api("/api/preview", "POST", runSpec());
    $("range-preview").innerHTML =
      `<strong>${esc(p.start_date)} → ${esc(p.end_date)}</strong><br>총 ${number(p.days)}일 · 기본 ${number(p.chunks)}개 구간 · ${esc(p.timezone)}`;
    $("schedule-form-summary").innerHTML =
      $("range-preview").innerHTML +
      `<br>${runSpec().dry_run ? "전송 없이 검증" : "API 전송"} · ${number(runSpec().chunk_days)}일씩 조회`;
  } catch (error) {
    $("range-preview").textContent = error.message;
    $("schedule-form-summary").textContent = error.message;
  }
}
function setPeriod(mode) {
  periodMode = mode;
  document
    .querySelectorAll("[data-period]")
    .forEach((b) => b.classList.toggle("selected", b.dataset.period === mode));
  $("relative-fields").hidden = mode !== "relative";
  $("absolute-fields").hidden = mode !== "absolute";
}
function loadSpec(spec) {
  setPeriod(spec.period_mode);
  if (![...$("months").options].some((o) => Number(o.value) === spec.months))
    $("months").add(new Option(`최근 ${spec.months}개월`, spec.months));
  $("months").value = spec.months;
  $("chunk-days").value = spec.chunk_days;
  $("start-date").value = spec.start_date || "2025-01-01";
  $("end-date").value = spec.end_date || "2025-12-31";
  $("execution-mode").value = spec.dry_run ? "validate" : "send";
}
function showView(view) {
  currentView = view;
  document
    .querySelectorAll(".view")
    .forEach((v) => (v.hidden = v.id !== `view-${view}`));
  document
    .querySelectorAll("[data-view]")
    .forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const info = viewInfo[view];
  $("breadcrumb").textContent = info[0];
  $("page-title").textContent = info[1];
  $("page-subtitle").textContent = info[2];
  if (view === "schedule") preview();
  if (view === "attention")
    loadUnresolved().catch((e) => toast(e.message, true));
}
function renderState() {
  $("env-badge").textContent =
    state.mode === "mock" ? "● MOCK 환경" : "● LIVE 환경";
  $("env-badge").classList.toggle("live", state.mode === "live");
  $("environment-title").textContent =
    state.mode === "mock"
      ? "가상 API 검증 환경에서 실행 중입니다."
      : "사내 API 연결 환경에서 실행 중입니다.";
  $("environment-description").textContent =
    state.mode === "mock"
      ? `모든 요청은 내장 가상 서버에서 처리됩니다. 가상 대상 DB ${number(state.mock_target_count)}건 · 시나리오: ${state.mock_scenario}`
      : `운영 전송 ${state.can_write ? "허용" : "잠금"} · 현재 업무 키: ${state.key_fields.join(" + ")} · API 한도 ${number(state.limit)}건`;
  $("environment-right").textContent =
    `조회 한도 ${number(state.limit)}건 / 요청`;
  $("runner-dot").classList.toggle("online", state.runner_alive);
  $("runner-label").textContent = state.runner_alive
    ? "실행기 정상 동작"
    : "실행기 연결 대기";
  $("execution-mode").querySelector('[value="send"]').disabled =
    !state.can_write;
  $("history-count").textContent = state.jobs.length;
  $("attention-count").textContent = state.unresolved;
  const schedule = state.schedule;
  $("metric-next").textContent = schedule.enabled
    ? formatDate(schedule.next_run_at)
    : "예약 없음";
  $("metric-schedule-note").textContent = schedule.enabled
    ? `${number(schedule.interval_minutes)}분마다 · ${state.timezone}`
    : "스케줄을 설정해 보세요";
  $("schedule-status").textContent = schedule.enabled ? "사용 중" : "비활성";
  $("schedule-status").className =
    `pill ${schedule.enabled ? "status-completed" : "neutral"}`;
  $("saved-schedule").textContent = schedule.enabled
    ? `${schedule.run.period_mode === "relative" ? `최근 ${schedule.run.months}개월` : `${schedule.run.start_date} ~ ${schedule.run.end_date}`} · ${schedule.run.chunk_days}일 단위 · ${schedule.interval_minutes}분마다 · ${schedule.run.dry_run ? "검증" : "전송"}`
    : "자동 갱신 비활성";
  $("saved-next").textContent = schedule.enabled
    ? formatDate(schedule.next_run_at)
    : "—";
  $("schedule-timezone").textContent = state.timezone;
  $("last-updated").textContent =
    `마지막 확인 ${formatDate(new Date().toISOString(), { second: "2-digit" })}`;
  $("history-list").innerHTML = state.jobs.length
    ? state.jobs
        .map(
          (job) =>
            `<div class="history-row"><div><strong>${job.start_date ? `${esc(job.start_date)} → ${esc(job.end_date)}` : job.spec.period_mode === "relative" ? `최근 ${job.spec.months}개월` : `${esc(job.spec.start_date)} → ${esc(job.spec.end_date)}`}</strong><p>${formatDate(job.created_at)} · ${job.spec.dry_run ? "검증" : "API 전송"} · ${esc(job.source)} · 처리 ${number(job.processed)}건 · 오류 ${number(job.failed + job.uncertain)}건</p></div><div class="inline-buttons">${badge(job.status)}<button class="button secondary small" data-job="${job.id}">결과 보기 →</button></div></div>`,
        )
        .join("")
    : '<div class="table-empty">실행 이력이 없습니다. 대시보드에서 첫 동기화를 실행하세요.</div>';
  const endpointLabels = {
    claims: ["Claim 접수 이력", "CLAIMS_BASE_URL", "GET"],
    schema: ["제품 스키마", "PRODUCT_BASE_URL", "GET"],
    product: ["제품 정보", "PRODUCT_BASE_URL", "GET"],
    target: ["FAR 데이터 갱신", "TARGET_BASE_URL", "POST"],
  };
  $("endpoints-list").innerHTML = Object.entries(state.endpoints)
    .map(
      ([key, url]) =>
        `<div class="endpoint"><div><strong>${endpointLabels[key][0]}</strong><small>${endpointLabels[key][1]}</small></div><code>${esc(url)}</code><span class="pill ${key === "target" ? "status-running" : "status-completed"}">${endpointLabels[key][2]}</span></div>`,
    )
    .join("");
}
function renderJob(job) {
  $("job-status").className = `pill status-${job.status}`;
  $("job-status").textContent = labels[job.status] || job.status;
  $("job-title").textContent = job.start_date
    ? `${job.start_date} → ${job.end_date}`
    : "실행 대기 중";
  $("job-meta").textContent =
    `${job.spec.dry_run ? "전송 없이 검증" : "API 전송"} · ${job.spec.chunk_days}일 단위 · ${formatDate(job.started_at || job.created_at)} · #${job.id.slice(0, 8)}`;
  const active = ["queued", "running"].includes(job.status);
  $("cancel-button").hidden = !active;
  $("cancel-button").disabled = !!job.cancel_requested;
  $("cancel-button").textContent = job.cancel_requested
    ? "중지 요청됨"
    : "중지";
  $("retry-button").hidden = active;
  const terminal = !active;
  const chunkFailure =
    job.chunk_summary?.failures ??
    job.chunks.filter((c) => c.status === "failed").length;
  let doneDays = 0,
    totalDays = 0;
  if (job.start_date) {
    totalDays =
      (Date.parse(job.end_date) - Date.parse(job.start_date)) / 86400000 + 1;
    doneDays =
      job.chunk_summary?.finished_days ??
      job.chunks
        .filter((c) => ["completed", "partial", "failed"].includes(c.status))
        .reduce(
          (sum, c) =>
            sum +
            (Date.parse(c.end_date) - Date.parse(c.start_date)) / 86400000 +
            1,
          0,
        );
  }
  const progress =
    job.status === "completed"
      ? 100
      : totalDays
        ? Math.min(99, Math.round((doneDays / totalDays) * 100))
        : 0;
  $("job-progress").value = progress;
  $("progress-label").textContent =
    `처리 ${number(job.processed)} / 조회 ${number(job.fetched)}건`;
  $("progress-value").textContent = terminal
    ? labels[job.status] || job.status
    : `${progress}%`;
  $("metric-fetched").innerHTML = `${number(job.fetched)}<span>건</span>`;
  $("metric-success").innerHTML = `${number(job.succeeded)}<span>건</span>`;
  $("metric-success-note").textContent =
    `${job.spec.dry_run ? "검증 완료" : "전송 완료"} · 변경 없음 ${number(job.skipped)}건`;
  $("metric-failed").innerHTML =
    `${number(job.failed + job.uncertain + chunkFailure)}<span>건</span>`;
  $("metric-failed-note").textContent =
    `조회 구간 오류 ${number(chunkFailure)}개 · 확인 대기 ${number(job.uncertain)}건`;
  $("chunk-note").textContent =
    `총 ${number(job.chunk_count)}개 구간${job.chunk_count > 400 ? " · 최근 400개 표시" : ""} · 자동 분할 포함`;
  $("chunk-list").innerHTML = job.chunks.length
    ? job.chunks
        .map(
          (c) =>
            `<span class="chunk status-${esc(c.status)}" title="${esc(`${c.start_date} ~ ${c.end_date} · ${labels[c.status] || c.status} · ${c.count}건${c.error ? ` · ${c.error}` : ""}`)}">${esc(c.start_date.slice(5))}–${esc(c.end_date.slice(5))} ${c.status === "completed" ? "✓" : c.status === "failed" ? "!" : c.status === "split" ? "⑂" : "·"}</span>`,
        )
        .join("")
    : '<span class="empty-inline">아직 조회한 구간이 없습니다.</span>';
  $("job-error").hidden = !job.error;
  $("job-error").textContent = job.error || "";
  events = job.events;
  renderEvents();
  $("export-button").disabled = false;
}
function renderEvents() {
  const log = $("event-log"),
    nearBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 45;
  const filtered = $("errors-only").checked
    ? events.filter((e) => ["error", "warning"].includes(e.level))
    : events;
  log.innerHTML = filtered.length
    ? filtered
        .map(
          (e) =>
            `<div class="event ${esc(e.level)}"><time>${formatDate(e.time, { hour: "2-digit", minute: "2-digit", second: "2-digit", month: undefined, day: undefined })}</time><span class="event-level">${esc(e.level.toUpperCase())}</span><span class="event-message">${esc(e.message)}</span></div>`,
        )
        .join("")
    : '<div class="log-empty"><strong>표시할 로그가 없습니다.</strong></div>';
  if (nearBottom) log.scrollTop = log.scrollHeight;
}
async function loadRecords(jobId = selectedJob) {
  if (!jobId) return;
  const data = await api(
    `/api/jobs/${jobId}/records?offset=${offset}&limit=25&status=${encodeURIComponent($("record-filter").value)}`,
  );
  if (jobId !== selectedJob) return;
  rows = data.items;
  total = data.total;
  $("record-count").textContent = number(total);
  $("records-body").innerHTML = rows.length
    ? rows
        .map((row, index) => {
          const p = row.payload || {};
          return `<tr class="record-row"><td><strong>${esc(p.far_no || "유효하지 않은 레코드")}</strong><small>${esc(p.sample_no || row.record_key)}</small></td><td>${esc(p.rcv_date || "—")}</td><td>${esc(p.cust_name || "—")}</td><td class="mono">${esc(p.part_id || "—")}</td><td>${esc(p.app || "—")}<small>${esc(p.density || "")}</small></td><td>${badge(row.status)}</td><td><button class="text-button" data-record="${index}" aria-label="${esc(p.far_no || "레코드")} 전송 데이터 보기">보기 →</button></td></tr>`;
        })
        .join("")
    : '<tr><td colspan="7" class="table-empty">표시할 데이터가 없습니다.</td></tr>';
  $("record-page-label").textContent = total
    ? `${number(offset + 1)}–${number(Math.min(offset + 25, total))} / ${number(total)}개 항목`
    : "0개 항목";
  $("prev-page").disabled = offset === 0;
  $("next-page").disabled = offset + 25 >= total;
}
async function selectJob(id) {
  selectedJob = id;
  offset = 0;
  events = [];
  $("record-filter").value = "";
  showView("dashboard");
  const job = await api(`/api/jobs/${id}`);
  if (selectedJob !== id) return;
  renderJob(job);
  await loadRecords(id);
}
async function refresh() {
  if (polling || authRequired) return;
  polling = true;
  try {
    state = await api("/api/state");
    $("connection-error").hidden = true;
    renderState();
    if (!initialized) {
      initialized = true;
      $("schedule-enabled").checked = state.schedule.enabled;
      $("interval").value = state.schedule.interval_minutes;
      if (state.schedule.destination) loadSpec(state.schedule.run);
      await preview();
    }
    if (!selectedJob && state.jobs.length) selectedJob = state.jobs[0].id;
    if (selectedJob) {
      const id = selectedJob;
      const job = await api(`/api/jobs/${id}`);
      if (selectedJob === id) renderJob(job);
      if (currentView === "dashboard") await loadRecords(id);
    }
    if (currentView === "attention") await loadUnresolved();
  } catch (error) {
    if (!authRequired) {
      $("connection-error").hidden = false;
      $("connection-error").textContent =
        `연결 오류: ${error.message} 자동으로 다시 연결합니다.`;
    }
  } finally {
    polling = false;
  }
}
async function action(button, callback) {
  button.disabled = true;
  try {
    await callback();
  } catch (error) {
    toast(error.message, true);
  } finally {
    button.disabled = false;
  }
}
function openPayload(payload, error) {
  $("payload-code").textContent = JSON.stringify(
    payload ? { values: payload } : null,
    null,
    2,
  );
  $("payload-error").hidden = !error;
  $("payload-error").textContent = error || "";
  $("payload-dialog").showModal();
}
async function loadUnresolved() {
  unresolved = await api("/api/deliveries/unresolved");
  $("unresolved-list").innerHTML = unresolved.length
    ? unresolved
        .map(
          (d, index) =>
            `<article class="card attention-card"><div class="card-heading"><h2 class="mono">${esc(d.record_key)}</h2>${badge(d.status)}</div><p>${esc(d.note || "전송 진행 중")}<br>${esc(d.destination)}<br>마지막 변경 ${formatDate(d.updated_at)}</p><div class="inline-buttons"><button class="button secondary small" data-unresolved-payload="${index}">전송 값 확인</button><button class="button primary small" data-resolve="${index}" ${d.status === "sending" ? "disabled" : ""}>반영 여부 기록</button></div></article>`,
        )
        .join("")
    : '<div class="card table-empty">전송 확인을 기다리는 항목이 없습니다.</div>';
}
document
  .querySelectorAll("[data-view]")
  .forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));
document.querySelectorAll("[data-period]").forEach((b) =>
  b.addEventListener("click", () => {
    setPeriod(b.dataset.period);
    preview();
  }),
);
["months", "start-date", "end-date", "chunk-days", "execution-mode"].forEach(
  (id) =>
    $(id).addEventListener("change", () => {
      $("execution-hint").textContent = runSpec().dry_run
        ? "검증 모드는 전송 데이터를 만들고 결과만 저장합니다."
        : state?.mode === "mock"
          ? "내장 가상 서버로 전송하여 데이터 갱신을 검증합니다."
          : "사내 운영 API에 POST 요청을 전송합니다.";
      preview();
    }),
);
$("run-form").addEventListener("submit", (e) => {
  e.preventDefault();
  action($("run-button"), async () => {
    const result = await api("/api/jobs", "POST", runSpec());
    await selectJob(result.id);
    toast("동기화 작업을 등록했습니다.");
    await refresh();
  });
});
$("cancel-button").addEventListener("click", () =>
  action($("cancel-button"), async () => {
    await api(`/api/jobs/${selectedJob}/cancel`, "POST");
    toast("중지를 요청했습니다. 진행 중인 API 호출 후 반영됩니다.");
    await refresh();
  }),
);
$("retry-button").addEventListener("click", () =>
  action($("retry-button"), async () => {
    const result = await api(`/api/jobs/${selectedJob}/retry`, "POST");
    await selectJob(result.id);
    toast("같은 설정으로 다시 실행합니다. 최근 기간은 새 실행일 기준입니다.");
  }),
);
$("errors-only").addEventListener("change", renderEvents);
$("record-filter").addEventListener("change", () => {
  offset = 0;
  loadRecords().catch((e) => toast(e.message, true));
});
$("prev-page").addEventListener("click", () => {
  offset = Math.max(0, offset - 25);
  loadRecords().catch((e) => toast(e.message, true));
});
$("next-page").addEventListener("click", () => {
  offset += 25;
  loadRecords().catch((e) => toast(e.message, true));
});
$("records-body").addEventListener("click", (e) => {
  const b = e.target.closest("[data-record]");
  if (b) {
    const row = rows[Number(b.dataset.record)];
    openPayload(row.payload, row.error);
  }
});
$("history-list").addEventListener("click", (e) => {
  const b = e.target.closest("[data-job]");
  if (b) selectJob(b.dataset.job).catch((error) => toast(error.message, true));
});
$("export-button").addEventListener("click", () =>
  action($("export-button"), async () => {
    const response = await api(
      `/api/jobs/${selectedJob}/export`,
      "GET",
      null,
      true,
    );
    const url = URL.createObjectURL(await response.blob());
    const a = document.createElement("a");
    a.href = url;
    a.download = `claim-sync-${selectedJob}.ndjson`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }),
);
$("copy-payload").addEventListener("click", () =>
  action($("copy-payload"), async () => {
    await navigator.clipboard.writeText($("payload-code").textContent);
    toast("전송 JSON을 복사했습니다.");
  }),
);
document
  .querySelectorAll("[data-close]")
  .forEach((b) =>
    b.addEventListener("click", () => $(b.dataset.close).close()),
  );
$("check-api").addEventListener("click", () =>
  action($("check-api"), async () => {
    showView("connections");
    $("check-results").textContent = "조회 API 연결을 확인하고 있습니다…";
    const result = await api("/api/check", "POST");
    $("check-results").innerHTML = Object.entries(result)
      .map(
        ([key, value]) =>
          `<div class="detail-item"><span>${esc({ product_schema: "제품 schema", claims: "Claim 접수 이력", target: "전송 API" }[key])}</span><strong class="${value.ok === false ? "amber" : value.ok ? "green" : ""}">${esc(value.message)}</strong></div>`,
      )
      .join("");
    toast("API 연결 확인을 마쳤습니다.");
  }),
);
$("schedule-form").addEventListener("submit", (e) => {
  e.preventDefault();
  action(e.submitter, async () => {
    await api("/api/schedule", "PUT", {
      enabled: $("schedule-enabled").checked,
      interval_minutes: Number($("interval").value),
      run: runSpec(),
    });
    toast("스케줄을 저장했습니다.");
    await refresh();
  });
});
$("back-to-config").addEventListener("click", () => {
  showView("dashboard");
  $("months").focus();
});
$("auth-form").addEventListener("submit", (e) => {
  e.preventDefault();
  token = $("access-token").value;
  sessionStorage.setItem("claim-sync-token", token);
  authRequired = false;
  $("auth-dialog").close();
  refresh();
});
$("auth-dialog").addEventListener("cancel", (e) => e.preventDefault());
$("unresolved-list").addEventListener("click", (e) => {
  const p = e.target.closest("[data-unresolved-payload]");
  if (p) {
    const item = unresolved[Number(p.dataset.unresolvedPayload)];
    openPayload(item.payload, item.note);
  }
  const r = e.target.closest("[data-resolve]");
  if (r) {
    resolving = unresolved[Number(r.dataset.resolve)];
    $("resolve-key").textContent = resolving.record_key;
    $("resolve-result").value = "";
    $("resolve-note").value = "";
    $("resolve-dialog").showModal();
  }
});
$("resolve-form").addEventListener("submit", (e) => {
  e.preventDefault();
  if (!$("resolve-result").value) {
    toast("서버 확인 결과를 선택하세요.", true);
    return;
  }
  action(e.submitter, async () => {
    await api("/api/deliveries/resolve", "POST", {
      record_key: resolving.record_key,
      destination: resolving.destination,
      result: $("resolve-result").value,
      note: $("resolve-note").value,
    });
    $("resolve-dialog").close();
    toast("확인 결과를 저장했습니다.");
    await refresh();
  });
});
const mapping = [
  ["far_no", "Claim · farNo", "문자열"],
  ["sample_no", "Claim · sampleNo", "문자열"],
  ["rcv_date", "Claim · rcvDate", "yyyy-mm-dd"],
  ["due_date", "Claim · dueDate", "yyyy-mm-dd"],
  ["cust_name", "Claim · custName", "문자열"],
  ["fail_loc", "Claim · failLoc", "문자열"],
  ["fail_symptom", "Claim · failSymptom", "문자열"],
  ["part_id", "Claim · partId", "원본 전체 문자열 보존"],
  ["failmode1", "Claim · failMode1", "문자열"],
  ["failmode2", "Claim · failMode2", "문자열"],
  ["comp_wc", "Claim · shippingWeekCode", "문자열"],
  ["far_comp_date", "Claim · actualCompDate", "yyyy-mm-dd · 빈 날짜는 null"],
  [
    "ims_created_date",
    "Claim · imsKeyCreatedDate",
    "yyyy-mm-dd · 원본 날짜 기준",
  ],
  ["ims_key", "Claim · imsKey", "문자열"],
  ["lot_id", "Claim · lotId", "문자열"],
  ["app", "제품 · app", "문자열"],
  ["device", "제품 · device", "문자열"],
  ["ctrl", "제품 · ctrl", "문자열"],
  ["nand", "제품 · nand_gen + nand_ver", "공백 하나로 결합 · 빈 값 제외"],
  ["dram", "제품 · dram_gen + dram_ver", "공백 하나로 결합 · 빈 값 제외"],
  ["density", "제품 · density", "문자열"],
];
$("mapping-body").innerHTML = mapping
  .map(
    (r) =>
      `<tr><td class="mono">${esc(r[0])}</td><td>${esc(r[1])}</td><td>${esc(r[2])}</td></tr>`,
  )
  .join("");
refresh();
setInterval(refresh, 2000);
