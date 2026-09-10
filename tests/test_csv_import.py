import csv
import hashlib
import io
import os
import subprocess
from pathlib import Path

import pytest

from claim_sync.csv_import import CSV_MAPPING, CsvImportError, list_csv_files, load_csv


@pytest.fixture
def csv_settings(settings, tmp_path):
    settings.csv_dir = tmp_path / "CSV 자료"
    settings.csv_dir.mkdir()
    return settings


def write_csv(settings, text, *, name="입력 데이터.csv", encoding="utf-8-sig"):
    raw = text.encode(encoding)
    (settings.csv_dir / name).write_bytes(raw)
    return name, raw


def test_exact_field_mapping_and_leading_zero_text(csv_settings):
    expected = [
        ("far", "far_no"),
        ("sample", "sample_no"),
        ("담당자", "name"),
        ("F/W", "firmware"),
        ("Release Date", "release_date"),
        ("Init.", "init"),
        ("SLC Max EC", "slc_max_ec"),
        ("SLC Min. EC", "slc_min_ec"),
        ("SLC Avg. EC", "slc_avg_ec"),
        ("M/TLC Max EC", "mlc_max_ec"),
        ("M/TLC Min. EC", "mlc_min_ec"),
        ("M/TLC Avg. EC", "mlc_avg_ec"),
        ("FTL Open", "open_count"),
        ("RTBB", "rtbb_count"),
        ("Reclaim", "reclaim_count"),
        ("Written Size(GB)", "write_size"),
        ("Read Data (GB)", "read_size"),
        ("LVD count", "lvd_count"),
        ("NPO Count", "npor_count"),
        ("SPO Count", "spor_count"),
        ("ErrorLogCnt(eMMC)", "error_log_count"),
        ("ECID(UFS) or CID(eMMC)", "ecid"),
        ("EXT_CSD(eMMC Only)", "ext_csd"),
    ]
    assert list(CSV_MAPPING.items()) == expected
    source = io.StringIO(newline="")
    writer = csv.writer(source)
    writer.writerow([item[0] for item in expected])
    data = ["000123", "001", "홍길동", "=1+1", "2025/01/31"] + ["0007"] * 18
    writer.writerow(data)
    name, raw = write_csv(csv_settings, source.getvalue())
    result = load_csv(csv_settings, name)
    assert result["sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["error_count"] == 0
    assert result["encoding"] == "utf-8-sig"
    assert result["valid_rows"] == result["total_rows"] == 1
    values = result["rows"][0]["values"]
    assert values == {
        target: "2025-01-31" if target == "release_date" else data[index]
        for index, (_, target) in enumerate(expected)
    }


@pytest.mark.parametrize("encoding", ["utf-8", "utf-8-sig", "cp949"])
def test_encodings_quotes_and_physical_line_numbers(csv_settings, encoding):
    name, _ = write_csv(
        csv_settings,
        'far,sample,담당자,F/W\r\n001,0002,"김, 대리","첫줄\r\n둘째줄"\r\n\r\n002,0003,이대리,V2\r\n',
        encoding=encoding,
    )
    result = load_csv(csv_settings, name)
    assert result["encoding"] == encoding
    assert result["error_count"] == 0
    assert [row["line"] for row in result["rows"]] == [2, 5]
    assert result["rows"][0]["values"]["name"] == "김, 대리"
    assert result["rows"][0]["values"]["firmware"] == "첫줄\r\n둘째줄"


def test_normalized_headers_and_unknown_fields(csv_settings):
    name, _ = write_csv(csv_settings, " FAR , SAMPLE ,slc   max ec,알 수 없는 칸\n001,002, 003 ,비고\n")
    result = load_csv(csv_settings, name)
    assert result["rows"][0]["values"] == {"far_no": "001", "sample_no": "002", "slc_max_ec": "003"}
    assert result["ignored_headers"] == ["알 수 없는 칸"]
    assert result["headers"][-1] == {"source": "알 수 없는 칸", "target": None}


def test_blank_omit_only_sends_populated_columns(csv_settings):
    name, _ = write_csv(csv_settings, "far,sample,F/W,Release Date,담당자\n001,002,V1,,\n")
    result = load_csv(csv_settings, name)
    assert result["blank_mode"] == "omit"
    assert result["rows"][0]["values"] == {"far_no": "001", "sample_no": "002", "firmware": "V1"}


def test_explicit_null_only_clears_headers_present_in_file(csv_settings):
    name, _ = write_csv(csv_settings, "far,sample,F/W,Release Date\n001,002,,\n")
    result = load_csv(csv_settings, name, "null")
    assert result["error_count"] == 0
    assert result["rows"][0]["values"] == {
        "far_no": "001",
        "sample_no": "002",
        "firmware": None,
        "release_date": None,
    }
    assert load_csv(csv_settings, name)["error_count"] == 1


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2024-02-29", "2024-02-29"),
        ("2025/01/31", "2025-01-31"),
        ("2025.01.31", "2025-01-31"),
        ("2025-1-2", "2025-01-02"),
        ("2025/1/2", "2025-01-02"),
        ("2025.1.2", "2025-01-02"),
        ("2025-01-31T23:45:12Z", "2025-01-31"),
        ("2025-01-31T23:45:12.345-08:00", "2025-01-31"),
        ("2025-01-31 23:45:12", "2025-01-31"),
    ],
)
def test_release_dates(csv_settings, value, expected):
    name, _ = write_csv(csv_settings, f"far,sample,Release Date\n001,002,{value}\n")
    result = load_csv(csv_settings, name)
    assert result["error_count"] == 0
    assert result["rows"][0]["values"]["release_date"] == expected


@pytest.mark.parametrize("value", ["2025-02-29", "01/02/2025", "46000", "2025/01.31", "2025-01-31T25:00"])
def test_invalid_or_ambiguous_release_dates(csv_settings, value):
    name, _ = write_csv(csv_settings, f"far,sample,Release Date\n001,002,{value}\n")
    result = load_csv(csv_settings, name)
    assert result["error_count"] == 1
    assert result["valid_rows"] == 0
    assert result["errors"][0]["line"] == 2
    assert "Release Date" in result["errors"][0]["message"]


@pytest.mark.parametrize("blank_mode", ["omit", "null"])
def test_required_identifiers_cannot_be_null(csv_settings, blank_mode):
    name, _ = write_csv(csv_settings, "far,sample,F/W\n,002,V1\n001, ,V2\n")
    result = load_csv(csv_settings, name, blank_mode)
    assert result["error_count"] == 2
    assert result["rows"] == []
    assert "far_no" in result["errors"][0]["message"]
    assert "sample_no" in result["errors"][1]["message"]


@pytest.mark.parametrize("second_value", ["V1", "V2"])
def test_duplicate_business_keys_are_explicit_errors(csv_settings, second_value):
    name, _ = write_csv(csv_settings, f"far,sample,F/W\n001,002,V1\n001,002,{second_value}\n")
    result = load_csv(csv_settings, name)
    assert result["error_count"] == 1
    assert result["valid_rows"] == 1
    assert result["errors"] == [{"line": 3, "message": "같은 far/sample 조합이 2행에 이미 있습니다."}]


@pytest.mark.parametrize(
    "content",
    [
        "",
        "\n",
        "far,sample,F/W,\n",
        "far,sample,F/W, f/w \n",
        "far,sample,F/W,unknown, UNKNOWN \n",
        "far,F/W\n001,V1\n",
        "far,sample,unknown\n001,002,V1\n",
        "far,sample,F/W\n001,002,V1,extra\n",
        "far,sample,F/W\n001,002\n",
        'far,sample,F/W\n001,002,"unterminated\n',
    ],
)
def test_structural_failures_reject_file(csv_settings, content):
    name, _ = write_csv(csv_settings, content)
    with pytest.raises(CsvImportError):
        load_csv(csv_settings, name)


def test_empty_data_rows_and_error_detail_limit(csv_settings):
    name, _ = write_csv(csv_settings, "far,sample,F/W\n\n,,\n")
    result = load_csv(csv_settings, name)
    assert result["total_rows"] == 0
    assert result["error_count"] == 1
    assert result["errors"][0]["line"] is None
    name, _ = write_csv(csv_settings, "far,sample,F/W\n" + ",002,V1\n" * 125)
    result = load_csv(csv_settings, name)
    assert result["total_rows"] == result["error_count"] == 125
    assert len(result["errors"]) == 100
    assert result["valid_rows"] == 0


def test_file_size_and_row_count_limits(csv_settings):
    csv_settings.csv_max_bytes = 1024
    name, _ = write_csv(csv_settings, "far,sample,F/W\n001,002," + "x" * 1024)
    with pytest.raises(CsvImportError, match="크기"):
        load_csv(csv_settings, name)
    csv_settings.csv_max_rows = 1
    name, _ = write_csv(csv_settings, "far,sample,F/W\n001,002,V1\n\n002,003,V2\n")
    with pytest.raises(CsvImportError, match="데이터 행"):
        load_csv(csv_settings, name)


def test_long_ext_csd_field_within_file_limit_and_csv_global_limit_restored(csv_settings):
    original = csv.field_size_limit()
    value = "00ff" * 40000
    name, _ = write_csv(csv_settings, f"far,sample,EXT_CSD(eMMC Only)\n001,002,{value}\n")
    result = load_csv(csv_settings, name)
    assert result["rows"][0]["values"]["ext_csd"] == value
    assert csv.field_size_limit() == original


@pytest.mark.parametrize("raw", [b"\x80", b"far,sample,F/W\n001,002,\x00", "한글".encode("utf-16")])
def test_invalid_encoding_and_nul_rejected(csv_settings, raw):
    (csv_settings.csv_dir / "bad.csv").write_bytes(raw)
    with pytest.raises(CsvImportError):
        load_csv(csv_settings, "bad.csv")


@pytest.mark.parametrize(
    "filename",
    [
        "../secret.csv",
        "..\\secret.csv",
        "C:\\secret.csv",
        "/secret.csv",
        "a.csv:secret",
        "bad.csv\x00",
        "file.txt",
        "",
        "file.csv ",
    ],
)
def test_path_traversal_and_nonliteral_files_rejected(csv_settings, filename):
    with pytest.raises(CsvImportError):
        load_csv(csv_settings, filename)


def test_listing_creates_directory_and_ignores_non_csv_and_subdirectories(settings, tmp_path):
    settings.csv_dir = tmp_path / "new csv"
    listing = list_csv_files(settings)
    assert settings.csv_dir.is_dir()
    assert Path(listing["directory"]) == settings.csv_dir.resolve()
    assert listing["files"] == []
    assert listing["mapping"][0] == {"source": "far", "target": "far_no"}
    assert listing["limits"]["max_bytes"] == settings.csv_max_bytes
    (settings.csv_dir / "sub.csv").mkdir()
    (settings.csv_dir / "ignore.txt").write_text("ignore")
    name, raw = write_csv(settings, "far,sample,F/W\n001,002,V1\n", name="UPPER.CSV")
    listed = list_csv_files(settings)["files"]
    assert len(listed) == 1
    assert listed[0]["name"] == name
    assert listed[0]["size"] == len(raw)
    assert listed[0]["modified_at"].endswith("+00:00")
    with pytest.raises(CsvImportError):
        load_csv(settings, "sub.csv")


def test_missing_files_and_unknown_blank_mode(csv_settings):
    with pytest.raises(CsvImportError, match="읽을 수"):
        load_csv(csv_settings, "missing.csv")
    with pytest.raises(CsvImportError, match="빈칸"):
        load_csv(csv_settings, "missing.csv", "erase")


def test_link_files_and_link_directory_rejected(csv_settings, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    source = outside / "outside.csv"
    source.write_text("far,sample,F/W\n001,002,V1\n")
    linked = csv_settings.csv_dir / "linked.csv"
    try:
        linked.symlink_to(source)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this Windows host")
    assert list_csv_files(csv_settings)["files"] == []
    with pytest.raises(CsvImportError, match="연결 경로"):
        load_csv(csv_settings, linked.name)
    directory_link = tmp_path / "linked-directory"
    directory_link.symlink_to(outside, target_is_directory=True)
    csv_settings.csv_dir = directory_link
    with pytest.raises(CsvImportError, match="연결 경로"):
        list_csv_files(csv_settings)


def test_file_replacement_before_open_cannot_read_another_file(csv_settings, tmp_path, monkeypatch):
    name, _ = write_csv(csv_settings, "far,sample,F/W\n001,002,V1\n")
    alternate = tmp_path / "unrelated.csv"
    alternate.write_text("private unrelated content")
    original_open = os.open

    def open_alternate(path, flags):
        return original_open(alternate, flags)

    monkeypatch.setattr(os, "open", open_alternate)
    with pytest.raises(CsvImportError, match="변경"):
        load_csv(csv_settings, name)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction handling")
def test_windows_junction_directory_rejected(csv_settings, tmp_path):
    outside = tmp_path / "outside-junction-target"
    outside.mkdir()
    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd.exe", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=10,
    )
    assert created.returncode == 0, created.stderr
    csv_settings.csv_dir = junction
    with pytest.raises(CsvImportError, match="연결 경로"):
        list_csv_files(csv_settings)
