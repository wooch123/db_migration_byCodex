"""Read locally supplied FAR CSV data without contacting an API."""

import csv
import hashlib
import io
import os
import re
import stat
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Lock

from claim_sync.config import Settings

CSV_MAPPING = {
    "far": "far_no",
    "sample": "sample_no",
    "담당자": "name",
    "F/W": "firmware",
    "Release Date": "release_date",
    "Init.": "init",
    "SLC Max EC": "slc_max_ec",
    "SLC Min. EC": "slc_min_ec",
    "SLC Avg. EC": "slc_avg_ec",
    "M/TLC Max EC": "mlc_max_ec",
    "M/TLC Min. EC": "mlc_min_ec",
    "M/TLC Avg. EC": "mlc_avg_ec",
    "FTL Open": "open_count",
    "RTBB": "rtbb_count",
    "Reclaim": "reclaim_count",
    "Written Size(GB)": "write_size",
    "Read Data (GB)": "read_size",
    "LVD count": "lvd_count",
    "NPO Count": "npor_count",
    "SPO Count": "spor_count",
    "ErrorLogCnt(eMMC)": "error_log_count",
    "ECID(UFS) or CID(eMMC)": "ecid",
    "EXT_CSD(eMMC Only)": "ext_csd",
}
_KEYS = {"far_no", "sample_no"}
_MAX_ERROR_DETAILS = 100
_CSV_LOCK = Lock()


class CsvImportError(ValueError):
    """A file cannot be safely loaded or is not a supported CSV file."""


def _normalized_header(value: str) -> str:
    return " ".join(value.split()).casefold()


_NORMALIZED_MAPPING = {_normalized_header(source): target for source, target in CSV_MAPPING.items()}


def _is_link(info: os.stat_result) -> bool:
    # Junctions and other Windows reparse points are not all reported as symlinks.
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _directory(settings: Settings) -> Path:
    try:
        configured = Path(settings.csv_dir).absolute()
        if configured.exists() or configured.is_symlink():
            info = configured.lstat()
            if _is_link(info) or not stat.S_ISDIR(info.st_mode):
                raise CsvImportError("CSV 폴더는 연결 경로나 파일이 아닌 실제 폴더여야 합니다.")
        else:
            configured.mkdir(parents=True, exist_ok=True)
        return configured.resolve(strict=True)
    except OSError as exc:
        raise CsvImportError(f"CSV 폴더를 사용할 수 없습니다: {exc}") from exc


def _filename(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character in value for character in "/\\:\x00<>|?*")
        or value.endswith(".")
        or Path(value).suffix.casefold() != ".csv"
    ):
        raise CsvImportError("CSV 폴더 바로 아래의 .csv 파일명만 지정할 수 있습니다.")
    return value


def _file_info(root: Path, filename: str) -> tuple[Path, os.stat_result]:
    path = root / _filename(filename)
    try:
        info = path.lstat()
        if _is_link(info) or not stat.S_ISREG(info.st_mode) or path.resolve(strict=True).parent != root:
            raise CsvImportError(
                "CSV 파일은 지정 폴더의 실제 파일이어야 합니다. 연결 경로는 허용하지 않습니다."
            )
        return path, info
    except OSError as exc:
        raise CsvImportError(f"CSV 파일을 읽을 수 없습니다: {filename}: {exc}") from exc


def list_csv_files(settings: Settings) -> dict:
    root = _directory(settings)
    files = []
    try:
        for path in root.iterdir():
            if path.suffix.casefold() != ".csv":
                continue
            try:
                _, info = _file_info(root, path.name)
            except CsvImportError:
                continue
            files.append(
                {
                    "name": path.name,
                    "size": info.st_size,
                    "modified_at": datetime.fromtimestamp(info.st_mtime, UTC).isoformat(),
                }
            )
    except OSError as exc:
        raise CsvImportError(f"CSV 파일 목록을 읽을 수 없습니다: {exc}") from exc
    return {
        "directory": str(root),
        "files": sorted(files, key=lambda item: (item["name"].casefold(), item["name"])),
        "mapping": [{"source": source, "target": target} for source, target in CSV_MAPPING.items()],
        "limits": {"max_bytes": settings.csv_max_bytes, "max_rows": settings.csv_max_rows},
    }


def _read_bytes(root: Path, filename: str, max_bytes: int) -> bytes:
    path, info = _file_info(root, filename)
    if info.st_size > max_bytes:
        raise CsvImportError(f"CSV 파일 크기가 제한({max_bytes:,} 바이트)을 초과합니다.")
    descriptor = None
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise CsvImportError(
                "읽는 중 CSV 파일이 변경되었습니다. 파일 목록을 새로 고친 후 다시 시도하세요."
            )
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise CsvImportError(f"CSV 파일 크기가 제한({max_bytes:,} 바이트)을 초과합니다.")
        return raw
    except OSError as exc:
        raise CsvImportError(f"CSV 파일을 읽을 수 없습니다: {filename}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _decode(raw: bytes) -> tuple[str, str]:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise CsvImportError("UTF-16 CSV는 지원하지 않습니다. UTF-8 또는 CP949 CSV로 저장하세요.")
    for encoding in ("utf-8-sig", "cp949"):
        try:
            content = raw.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" in content:
            raise CsvImportError("CSV에 NUL 문자가 있습니다. UTF-8 또는 CP949 텍스트 파일인지 확인하세요.")
        label = "utf-8-sig" if raw.startswith(b"\xef\xbb\xbf") else "utf-8"
        return content, encoding if encoding == "cp949" else label
    raise CsvImportError("CSV를 UTF-8 또는 CP949로 읽을 수 없습니다. 저장 인코딩을 확인하세요.")


def _release_date(value: str) -> str:
    parts = re.fullmatch(r"(\d{4})([-/.])(\d{1,2})\2(\d{1,2})", value)
    try:
        if parts:
            return date(int(parts[1]), int(parts[3]), int(parts[4])).isoformat()
        if re.match(r"^\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}", value):
            return datetime.fromisoformat(value).date().isoformat()
    except ValueError:
        pass
    raise ValueError(
        "Release Date는 유효한 YYYY-MM-DD, YYYY/MM/DD, YYYY.MM.DD 또는 ISO 날짜·시간이어야 합니다."
    )


def _parse(content: str, settings: Settings, blank_mode: str) -> dict:
    # csv's field limit is process-wide. Serialize our readers while temporarily
    # increasing it so a long EXT_CSD value can use the configured file allowance.
    with _CSV_LOCK:
        old_limit = csv.field_size_limit()
        csv.field_size_limit(max(old_limit, min(settings.csv_max_bytes, 2**31 - 1)))
        try:
            return _parse_rows(content, settings.csv_max_rows, blank_mode)
        finally:
            csv.field_size_limit(old_limit)


def _parse_rows(content: str, max_rows: int, blank_mode: str) -> dict:
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    try:
        source_headers = next(reader)
    except StopIteration as exc:
        raise CsvImportError("CSV 파일이 비어 있습니다.") from exc
    except csv.Error as exc:
        raise CsvImportError(f"CSV 헤더 형식이 올바르지 않습니다: {exc}") from exc
    normalized = [_normalized_header(header) for header in source_headers]
    if not normalized or any(not header for header in normalized):
        raise CsvImportError("CSV 컬럼 헤더에 빈 이름이 있습니다.")
    if len(normalized) != len(set(normalized)):
        raise CsvImportError("CSV 컬럼 헤더가 중복되었습니다. 공백과 영문 대소문자도 확인하세요.")
    targets = [_NORMALIZED_MAPPING.get(header) for header in normalized]
    if not _KEYS.issubset(targets):
        raise CsvImportError("CSV에는 far와 sample 컬럼 헤더가 모두 있어야 합니다.")
    if not any(target and target not in _KEYS for target in targets):
        raise CsvImportError("CSV에는 far와 sample 이외에 전송할 수 있는 컬럼이 하나 이상 있어야 합니다.")
    result = {
        "headers": [
            {"source": source.strip(), "target": target}
            for source, target in zip(source_headers, targets, strict=True)
        ],
        "ignored_headers": [
            source.strip() for source, target in zip(source_headers, targets, strict=True) if not target
        ],
        "rows": [],
        "errors": [],
        "error_count": 0,
        "total_rows": 0,
        "valid_rows": 0,
    }
    seen = {}
    try:
        while True:
            line = reader.line_num + 1
            try:
                row = next(reader)
            except StopIteration:
                break
            if not any(value.strip() for value in row):
                continue
            result["total_rows"] += 1
            if result["total_rows"] > max_rows:
                raise CsvImportError(f"CSV 데이터 행이 제한({max_rows:,}행)을 초과합니다.")
            if len(row) != len(targets):
                raise CsvImportError(
                    f"CSV {line}행의 컬럼 수({len(row)})가 헤더({len(targets)})와 다릅니다. 쉼표와 따옴표를 확인하세요."
                )
            values = {}
            row_errors = []
            for target, raw_value in zip(targets, row, strict=True):
                if target is None:
                    continue
                value = raw_value.strip()
                if target in _KEYS:
                    if not value:
                        row_errors.append(f"필수 값 {target}가 비어 있습니다.")
                    values[target] = value
                elif value:
                    if target == "release_date":
                        try:
                            value = _release_date(value)
                        except ValueError as exc:
                            row_errors.append(str(exc))
                    values[target] = value
                elif blank_mode == "null":
                    values[target] = None
            if not any(target not in _KEYS for target in values):
                row_errors.append("far와 sample 이외에 전송할 값이 없습니다.")
            key = (values["far_no"], values["sample_no"])
            if all(key):
                if key in seen:
                    row_errors.append(f"같은 far/sample 조합이 {seen[key]}행에 이미 있습니다.")
                else:
                    seen[key] = line
            if row_errors:
                result["error_count"] += 1
                if len(result["errors"]) < _MAX_ERROR_DETAILS:
                    result["errors"].append({"line": line, "message": " ".join(row_errors)})
            else:
                result["rows"].append({"line": line, "values": values})
    except csv.Error as exc:
        raise CsvImportError(f"CSV {reader.line_num}행 부근의 형식이 올바르지 않습니다: {exc}") from exc
    if not result["total_rows"]:
        result["error_count"] = 1
        result["errors"].append({"line": None, "message": "CSV에 데이터 행이 없습니다."})
    result["valid_rows"] = len(result["rows"])
    return result


def load_csv(settings: Settings, filename: str, blank_mode: str = "omit") -> dict:
    """Read a bounded, immediate local file; values remain text except explicit nulls."""
    if blank_mode not in {"omit", "null"}:
        raise CsvImportError("빈칸 처리 방식은 omit 또는 null이어야 합니다.")
    _filename(filename)
    raw = _read_bytes(_directory(settings), filename, settings.csv_max_bytes)
    content, encoding = _decode(raw)
    return {
        "filename": filename,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "encoding": encoding,
        "blank_mode": blank_mode,
        **_parse(content, settings, blank_mode),
    }
