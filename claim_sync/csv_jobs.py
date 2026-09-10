"""Validated, immutable CSV jobs shared by the web console and headless CLI."""

from .csv_import import CsvImportError, load_csv
from .models import RunSpec


def validate_snapshot(settings, snapshot):
    if set(settings.target_key_fields) != {"far_no", "sample_no"} or len(settings.target_key_fields) != 2:
        raise CsvImportError('CSV 가져오기에는 TARGET_KEY_FIELDS=["far_no","sample_no"] 설정이 필요합니다.')
    if snapshot["error_count"]:
        raise CsvImportError(
            f"CSV 오류 {snapshot['error_count']}개를 수정한 뒤 다시 확인하세요. 전송하지 않았습니다."
        )
    if not snapshot["rows"]:
        raise CsvImportError("전송할 CSV 데이터 행이 없습니다.")


def enqueue_csv(settings, store, filename, *, dry_run=True, blank_mode="omit", sha256=None, source="csv"):
    snapshot = load_csv(settings, filename, blank_mode)
    if sha256 is not None and snapshot["sha256"] != sha256:
        raise CsvImportError("미리보기 이후 CSV 파일이 변경되었습니다. 다시 미리보기를 확인하세요.")
    validate_snapshot(settings, snapshot)
    if not dry_run and not settings.can_write:
        raise CsvImportError("실제 전송이 잠겨 있습니다. .env의 운영 전송 조건을 확인하세요.")
    spec = RunSpec(source_type="csv", csv_filename=snapshot["filename"], dry_run=dry_run)
    return store.enqueue_csv(spec, settings.destination, snapshot, source=source)
