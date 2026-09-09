import pytest
from pydantic import ValidationError

from claim_sync.config import Settings


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_base_url": "https://username:secret@example.invalid"},
        {"target_path": "//other.example/path"},
        {"product_record_path": "/without-placeholder"},
        {"target_success_path": "success", "target_success_value": "invalid-json"},
    ],
)
def test_invalid_endpoint_and_acknowledgement_settings(kwargs):
    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None, **kwargs)


def test_dotenv_changes_all_bases_without_code_change(tmp_path):
    env = tmp_path / "example.env"
    env.write_text(
        "CLAIMS_BASE_URL=http://source.example\nPRODUCT_BASE_URL=http://product.example\n"
        "TARGET_BASE_URL=https://target.example\nTARGET_PATH=/new/target\n",
        encoding="utf-8",
    )
    settings = Settings(_env_file=env)
    assert settings.claims_base_url == "http://source.example"
    assert settings.product_base_url == "http://product.example"
    assert settings.target_url == "https://target.example/new/target"
