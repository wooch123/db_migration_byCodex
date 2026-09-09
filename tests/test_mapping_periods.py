from datetime import UTC, date, datetime

import pytest
from pydantic import ValidationError

from claim_sync.clients import UpstreamError, claim_rows, product_record, schema_fields
from claim_sync.mapping import PRODUCT_FIELDS, map_record, normalized_date, part_prefix
from claim_sync.models import RunSpec, chunks


def test_exact_requested_mapping(claim, product):
    result = map_record(claim, product)
    assert result == {
        "far_no": "FAR-001",
        "sample_no": "S001",
        "rcv_date": "2025-01-01",
        "due_date": "2025-01-15",
        "cust_name": "Example",
        "fail_loc": "Korea",
        "fail_symptom": "Read failure",
        "part_id": "ABCDEFGHIJKLMNO-EXT",
        "failmode1": "Read",
        "failmode2": "Intermittent",
        "comp_wc": "0025",
        "far_comp_date": None,
        "ims_created_date": None,
        "ims_key": "IMS001",
        "lot_id": "LOT001",
        "app": "SSD",
        "device": "NVMe",
        "ctrl": "CTRL",
        "nand": "V8 1.0",
        "dram": "LPDDR4 2.0",
        "density": "1 TB",
    }
    assert part_prefix(claim) == "ABCDEFGHIJKLMNO"


def test_null_product_components(claim, product):
    product.update(nand_gen=None, nand_ver=" 2.0 ", dram_gen=None, dram_ver="")
    values = map_record(claim, product)
    assert values["nand"] == "2.0"
    assert values["dram"] is None


@pytest.mark.parametrize("value", ["2025-02-30", "not-a-date", 123, {}, "2025-01-01garbage"])
def test_invalid_dates_fail(value):
    with pytest.raises(ValueError):
        normalized_date(value, "rcvDate")


def test_required_identity_and_product_fields(claim, product):
    claim["sampleNo"] = " "
    with pytest.raises(ValueError):
        map_record(claim, product)
    with pytest.raises(ValueError):
        part_prefix({"partId": "SHORT"})
    with pytest.raises(ValueError):
        map_record(claim, {})


@pytest.mark.parametrize(
    ("now", "months", "expected"),
    [
        ("2025-03-31T00:00:00+00:00", 1, (date(2025, 2, 28), date(2025, 3, 31))),
        ("2024-03-31T00:00:00+00:00", 1, (date(2024, 2, 29), date(2024, 3, 31))),
        ("2025-01-15T00:00:00+00:00", 2, (date(2024, 11, 15), date(2025, 1, 15))),
        ("2025-02-28T16:00:00+00:00", 1, (date(2025, 2, 1), date(2025, 3, 1))),
    ],
)
def test_calendar_months_and_seoul_timezone(now, months, expected):
    assert RunSpec(months=months).resolve("Asia/Seoul", datetime.fromisoformat(now)) == expected


def test_inclusive_chunks_no_overlap():
    periods = list(chunks(date(2025, 1, 1), date(2025, 1, 10), 3))
    assert periods == [
        (date(2025, 1, 1), date(2025, 1, 3)),
        (date(2025, 1, 4), date(2025, 1, 6)),
        (date(2025, 1, 7), date(2025, 1, 9)),
        (date(2025, 1, 10), date(2025, 1, 10)),
    ]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"chunk_days": 0},
        {"months": 0},
        {"period_mode": "absolute"},
        {"period_mode": "absolute", "start_date": "2025-02-01", "end_date": "2025-01-01"},
    ],
)
def test_bad_period_configuration(kwargs):
    with pytest.raises(ValidationError):
        RunSpec(**kwargs)


def test_response_envelopes(product):
    rows, truncated = claim_rows({"data": {"items": [{"farNo": "A"}], "total": 5}})
    assert rows == [{"farNo": "A"}] and truncated
    assert claim_rows({"custom": {"rows": []}}, "custom.rows") == ([], False)
    assert claim_rows({"custom": {"rows": [], "total": 1}}, "custom.rows") == ([], True)
    assert product_record({"data": [product]}) == product
    assert schema_fields({"columns": [{"name": field} for field in PRODUCT_FIELDS]}) == set(PRODUCT_FIELDS)


@pytest.mark.parametrize("body", [{"error": "failure"}, None, {"data": {}}, [None]])
def test_unknown_claim_shape_never_becomes_empty_success(body):
    with pytest.raises(UpstreamError):
        claim_rows(body)


def test_absolute_dates_are_not_shifted_by_execution_date():
    spec = RunSpec(period_mode="absolute", start_date="2025-01-01", end_date="2025-12-31")
    assert spec.resolve("Asia/Seoul", datetime(2026, 9, 9, tzinfo=UTC)) == (
        date(2025, 1, 1),
        date(2025, 12, 31),
    )
