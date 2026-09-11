"use strict";

let csvFiles = [], csvMapping = [], csvSnapshot = null;
let csvRevision = 0, csvLoading = false, csvReading = false, csvSubmitting = false;

function csvSelection() {
  return { filename: $("csv-file").value, blank_mode: $("csv-blank-mode").value };
}
function csvSelectionKey() {
  return JSON.stringify(csvSelection());
}
function csvSize(bytes) {
  return bytes < 1024 * 1024
    ? `${number(Math.ceil(bytes / 1024))} KB`
    : `${(bytes / (1024 * 1024)).toLocaleString("ko-KR", { maximumFractionDigits: 1 })} MB`;
}
function csvError(message = "") {
  $("csv-error").hidden = !message;
  $("csv-error").textContent = message;
}
function invalidateCSVPreview() {
  ++csvRevision;
  csvSnapshot = null;
  csvReading = false;
  $("csv-preview-panel").hidden = true;
  $("csv-read-status").textContent = "파일을 다시 읽어 현재 전송할 값을 확인하세요.";
  csvError();
  updateCSVControls();
}
function updateCSVControls() {
  const busy = csvLoading || csvReading || csvSubmitting;
  const current = csvSnapshot && csvSnapshot.selection === csvSelectionKey();
  const valid = current && csvSnapshot.data.sha256 && !csvSnapshot.data.error_count && (csvSnapshot.data.valid_rows > 0 || csvSnapshot.data.skipped_count > 0);
  $("csv-file").disabled = csvLoading || csvSubmitting || !csvFiles.length;
  $("csv-blank-mode").disabled = csvLoading || csvSubmitting;
  $("csv-refresh").disabled = busy;
  $("csv-preview").disabled = busy || !$("csv-file").value;
  $("csv-preview").textContent = csvReading ? "파일 읽는 중…" : "파일 읽기 · 미리보기";
  $("csv-validate").disabled = busy || !valid;
  $("csv-send").disabled = busy || !valid || !state?.can_write;
  $("csv-send").textContent = state?.mode === "mock" ? "가상 API에 전송" : "운영 API에 전송";
  $("csv-blank-hint").textContent = $("csv-blank-mode").value === "null"
    ? "CSV에 있는 컬럼의 빈 셀을 null로 보내 값 비우기를 요청합니다. CSV에 없는 컬럼은 전송하지 않습니다."
    : "빈 셀과 CSV에 없는 컬럼은 전송하지 않습니다. 수정 시 해당 기존 값은 유지됩니다.";
  $("csv-send-hint").textContent = csvSubmitting
    ? "실행을 등록하고 있습니다…"
    : !valid
      ? current && !csvSnapshot.data.error_count && !csvSnapshot.data.total_rows
        ? "데이터 행이 없는 파일입니다. 값을 입력한 뒤 다시 읽어주세요."
        : "미리보기를 확인한 뒤 실행하세요. 파일 형식 오류가 있거나 파일을 고쳤다면 수정 후 다시 읽어주세요."
      : !csvSnapshot.data.valid_rows
        ? "모든 행에 전송할 값 또는 업무 키가 없어 API 호출 없이 전송 제외로 기록합니다."
      : !state?.can_write
        ? "운영 전송이 잠겨 있습니다. .env의 ALLOW_LIVE_WRITES와 TARGET_UPSERT_CONFIRMED를 true로 설정하고 앱을 재시작하세요. 검증 실행은 가능합니다."
        : "전송 대상 행을 파일 순서대로 보냅니다. 중복 경고가 있어도 전송하며, POST가 HTTP 400이면 같은 FAR / SAMPLE에 PATCH로 한 번 전송합니다.";
  const file = csvFiles.find((item) => item.name === $("csv-file").value);
  $("csv-file-meta").textContent = file
    ? `${csvSize(file.size)} · 마지막 수정 ${formatDate(file.modified_at)}`
    : csvFiles.length ? "가져올 CSV 파일을 선택하세요." : "csv/ 폴더에 CSV 파일을 넣은 뒤 목록을 새로고침하세요.";
}
function renderCSVMapping() {
  const selectedHeaders = csvSnapshot?.data.headers || [];
  $("csv-mapping-count").textContent = `${number(csvMapping.length)}개 헤더 → 운영 서버 필드`;
  $("csv-mapping-body").innerHTML = csvMapping.map(({ source, target }) => {
    const included = selectedHeaders.some((header) => header.target === target);
    return `<tr><td>${esc(source)}${["far_no", "sample_no"].includes(target) ? ' <span class="label-tag">업무 키</span>' : ""}${included ? ' <span class="csv-mapped">파일에 포함</span>' : ""}</td><td class="mono">${esc(target)}</td></tr>`;
  }).join("");
}
async function loadCSVFiles() {
  if (csvSubmitting) return;
  const previous = $("csv-file").value;
  invalidateCSVPreview();
  const revision = csvRevision;
  csvLoading = true;
  $("csv-read-status").textContent = "CSV 폴더를 확인하고 있습니다…";
  updateCSVControls();
  try {
    const result = await api("/api/csv/files");
    if (revision !== csvRevision) return;
    csvFiles = result.files;
    csvMapping = result.mapping;
    $("csv-directory").textContent = result.directory;
    $("csv-limits").textContent = `far 또는 sample이 비었거나 전송할 값이 없는 행은 제외합니다. 중복 키는 경고만 표시하고 전송합니다. 파일당 최대 ${csvSize(result.limits.max_bytes)} · ${number(result.limits.max_rows)}행`;
    $("csv-file").innerHTML = '<option value="">파일 선택</option>' + csvFiles.map((file) => `<option value="${esc(file.name)}">${esc(file.name)}</option>`).join("");
    if (csvFiles.some((file) => file.name === previous)) $("csv-file").value = previous;
    else if (csvFiles.length === 1) $("csv-file").value = csvFiles[0].name;
    $("csv-read-status").textContent = csvFiles.length
      ? `${number(csvFiles.length)}개 파일을 찾았습니다. 파일을 읽어 전송할 값을 확인하세요.`
      : "아직 CSV 파일이 없습니다. 위 폴더에 파일을 저장하세요.";
    renderCSVMapping();
  } catch (error) {
    if (revision !== csvRevision) return;
    csvFiles = [];
    $("csv-file").innerHTML = '<option value="">목록을 불러올 수 없습니다</option>';
    $("csv-read-status").textContent = "파일 목록을 다시 불러오세요.";
    csvError(error.message);
  } finally {
    if (revision === csvRevision) {
      csvLoading = false;
      updateCSVControls();
    }
  }
}
function renderCSVPreview() {
  const data = csvSnapshot.data;
  $("csv-preview-panel").hidden = false;
  $("csv-preview-summary").textContent = `${data.filename} · ${data.encoding} · 전체 ${number(data.total_rows)}행 / 전송 대상 ${number(data.valid_rows)}행 · 전송 제외 ${number(data.skipped_count)}행 · 경고 ${number(data.warning_count)}건 · 오류 ${number(data.error_count)}건`;
  $("csv-preview-status").className = `pill ${data.error_count ? "status-failed" : data.warning_count ? "status-needs_attention" : data.valid_rows ? "status-validated" : "neutral"}`;
  $("csv-preview-status").textContent = data.error_count ? "오류 수정 필요" : data.warning_count ? "경고 있음 · 전송 가능" : data.valid_rows ? "전송 가능" : data.skipped_count ? "모든 행 전송 제외" : "데이터 행 없음";
  $("csv-ignored").hidden = !data.ignored_headers.length;
  $("csv-ignored").textContent = `전송에서 제외한 헤더: ${data.ignored_headers.join(", ")}`;
  const errors = data.errors || [];
  $("csv-validation-errors").hidden = !data.error_count;
  $("csv-validation-errors").innerHTML = `<div><strong>CSV 파일을 수정한 뒤 다시 읽어주세요.</strong><ul>${errors.map((error) => `<li>${error.line == null ? "파일" : `${number(error.line)}행`}: ${esc(error.message)}</li>`).join("")}</ul>${data.error_count > errors.length ? `<p>총 ${number(data.error_count)}건 중 ${number(errors.length)}건의 오류를 표시합니다.</p>` : ""}</div>`;
  const warnings = data.warnings || [];
  $("csv-validation-warnings").hidden = !data.warning_count;
  $("csv-validation-warnings").innerHTML = `<div><strong>중복 키가 있어도 파일 순서대로 전송합니다.</strong><p>같은 필드는 뒤 행에 입력한 값으로 갱신하며, 전송에서 제외한 빈 필드는 기존 값을 유지합니다.</p><ul>${warnings.map((warning) => `<li>${warning.line == null ? "파일" : `${number(warning.line)}행`}: ${esc(warning.message)}</li>`).join("")}</ul>${data.warning_count > warnings.length ? `<p>총 ${number(data.warning_count)}건 중 ${number(warnings.length)}건의 경고를 표시합니다.</p>` : ""}</div>`;
  const skipped = data.skipped_rows || [];
  $("csv-skipped-rows").hidden = !data.skipped_count;
  $("csv-skipped-rows").innerHTML = `<div><strong>전송할 값 또는 업무 키가 없는 행은 오류 없이 제외합니다.</strong><ul>${skipped.map((row) => `<li>${number(row.line)}행: ${esc(row.message)}</li>`).join("")}</ul>${data.skipped_count > skipped.length ? `<p>총 ${number(data.skipped_count)}행 중 ${number(skipped.length)}행의 제외 사유를 표시합니다.</p>` : ""}</div>`;
  $("csv-preview-body").innerHTML = data.rows.length ? data.rows.map((row, index) => {
    const values = row.values;
    const fields = Object.keys(values).filter((key) => !["far_no", "sample_no"].includes(key));
    return `<tr><td>${number(row.line)}행</td><td><strong>${esc(values.far_no)}</strong><small>${esc(values.sample_no)}</small></td><td>${esc(values.name ?? "—")}</td><td class="mono">${esc(values.firmware ?? "—")}</td><td title="${esc(fields.join(", "))}">${number(fields.length)}개</td><td><button class="text-button" data-csv-row="${index}" aria-label="CSV ${number(row.line)}행 전송 데이터 보기">전체 보기 →</button></td></tr>`;
  }).join("") : '<tr><td colspan="6" class="table-empty">전송할 데이터가 없습니다.</td></tr>';
  $("csv-preview-note").textContent = `전송 대상 중 최대 ${number(data.preview_limit)}행을 표시합니다. 실행 시 대상 ${number(data.valid_rows)}행과 제외 ${number(data.skipped_count)}행의 결과를 기록합니다. 파일 내용이 변경되면 다시 읽어야 합니다.`;
  $("csv-read-status").textContent = "파일을 읽었습니다. 미리보기와 빈 셀 처리 방식을 확인하세요.";
  renderCSVMapping();
  updateCSVControls();
}
async function readCSVPreview() {
  if (csvLoading || csvReading || csvSubmitting || !$("csv-file").value) return;
  invalidateCSVPreview();
  const revision = csvRevision;
  const selection = csvSelectionKey();
  csvReading = true;
  $("csv-read-status").textContent = "헤더와 모든 행을 확인하고 있습니다…";
  updateCSVControls();
  try {
    const data = await api("/api/csv/preview", "POST", csvSelection());
    if (revision !== csvRevision || selection !== csvSelectionKey()) return;
    csvSnapshot = { selection, data };
    renderCSVPreview();
  } catch (error) {
    if (revision !== csvRevision) return;
    csvError(error.message);
    $("csv-read-status").textContent = "파일을 읽지 못했습니다. 오류 내용을 확인하세요.";
  } finally {
    if (revision === csvRevision) {
      csvReading = false;
      updateCSVControls();
    }
  }
}
async function queueCSV(dryRun) {
  if (csvSubmitting || csvLoading || csvReading || !csvSnapshot || csvSnapshot.selection !== csvSelectionKey()) return;
  const data = csvSnapshot.data;
  if (data.error_count || !(data.valid_rows > 0 || data.skipped_count > 0) || (!dryRun && !state?.can_write)) return;
  csvSubmitting = true;
  csvError();
  updateCSVControls();
  try {
    const result = await api("/api/csv/jobs", "POST", {
      filename: data.filename,
      sha256: data.sha256,
      blank_mode: data.blank_mode,
      dry_run: dryRun,
    });
    invalidateCSVPreview();
    toast(dryRun ? "CSV 검증 작업을 등록했습니다." : "CSV API 전송 작업을 등록했습니다.");
    await selectJob(result.id);
    await refresh();
  } catch (error) {
    invalidateCSVPreview();
    csvError(error.message);
    toast(error.message, true);
  } finally {
    csvSubmitting = false;
    updateCSVControls();
  }
}
$("csv-refresh").addEventListener("click", loadCSVFiles);
$("csv-file").addEventListener("change", () => { invalidateCSVPreview(); renderCSVMapping(); });
$("csv-blank-mode").addEventListener("change", () => { invalidateCSVPreview(); renderCSVMapping(); });
$("csv-preview").addEventListener("click", readCSVPreview);
$("csv-validate").addEventListener("click", () => queueCSV(true));
$("csv-send").addEventListener("click", () => queueCSV(false));
$("csv-preview-body").addEventListener("click", (event) => {
  const button = event.target.closest("[data-csv-row]");
  if (!button || !csvSnapshot) return;
  const row = csvSnapshot.data.rows[Number(button.dataset.csvRow)];
  if (row) openPayload(row.values, null, null, `CSV ${row.line}행`);
});
