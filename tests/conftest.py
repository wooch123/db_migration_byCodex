from datetime import date

import pytest

from claim_sync.config import Settings
from claim_sync.models import RunSpec
from claim_sync.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None, app_mode="mock", data_dir=tmp_path, get_retries=0, retry_backoff_seconds=0
    )


@pytest.fixture
def store(settings):
    return Store(settings.data_dir)


@pytest.fixture
def spec():
    return RunSpec(
        period_mode="absolute",
        start_date=date(2025, 1, 1),
        end_date=date(2025, 1, 3),
        chunk_days=2,
        dry_run=False,
    )


@pytest.fixture
def claim():
    return {
        "farNo": "FAR-001",
        "sampleNo": "S001",
        "rcvDate": "2025-01-01T23:15:00-08:00",
        "dueDate": "2025-01-15",
        "actualCompDate": "",
        "imsKeyCreatedDate": None,
        "custName": "Example",
        "failLoc": "Korea",
        "failSymptom": "Read failure",
        "partId": "ABCDEFGHIJKLMNO-EXT",
        "failMode1": "Read",
        "failMode2": "Intermittent",
        "shippingWeekCode": "0025",
        "imsKey": "IMS001",
        "lotId": "LOT001",
    }


@pytest.fixture
def product():
    return {
        "app": "SSD",
        "device": "NVMe",
        "ctrl": "CTRL",
        "density": "1 TB",
        "nand_gen": "V8",
        "nand_ver": "1.0",
        "dram_gen": "LPDDR4",
        "dram_ver": "2.0",
    }
