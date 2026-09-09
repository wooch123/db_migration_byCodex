import json
from pathlib import Path
from typing import Final, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Supplied intranet endpoints. Settings still allows .env/environment overrides.
DEFAULT_CLAIMS_BASE_URL: Final = "http://12.81.220.37:8080"
DEFAULT_PRODUCT_BASE_URL: Final = "http://12.81.221.145:5273"
DEFAULT_TARGET_BASE_URL: Final = "https://estgtask.samsungds.net"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")
    app_mode: Literal["mock", "live"] = "mock"
    data_dir: Path = Path("data")
    timezone: str = "Asia/Seoul"
    enable_runner: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    app_access_token: str = ""
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"])
    claims_base_url: str = DEFAULT_CLAIMS_BASE_URL
    claims_path: str = "/api/searchFlashClaims"
    claims_from_param: str = "rcvDataFrom"
    claims_to_param: str = "rcvDateTo"
    claims_limit: int = Field(default=1000, ge=1, le=100000)
    claims_records_path: str = "auto"
    claims_headers: dict[str, str] = Field(default_factory=dict)
    product_base_url: str = DEFAULT_PRODUCT_BASE_URL
    product_schema_path: str = "/dbms/api/product-info/schema"
    product_record_path: str = "/dbms/api/product-info/record/{part_id}"
    product_records_path: str = "auto"
    product_headers: dict[str, str] = Field(default_factory=dict)
    target_base_url: str = DEFAULT_TARGET_BASE_URL
    target_path: str = "/api/external/far_table"
    target_headers: dict[str, str] = Field(default_factory=dict)
    allow_live_writes: bool = False
    target_upsert_confirmed: bool = False
    target_key_fields: list[str] = Field(default_factory=lambda: ["far_no", "sample_no"])
    target_dataset_id: str = "default"
    target_idempotency_header: str = "Idempotency-Key"
    target_success_path: str = ""
    target_success_value: str = "true"
    http_timeout_seconds: float = Field(default=30, gt=0, le=300)
    get_retries: int = Field(default=3, ge=0, le=8)
    retry_backoff_seconds: float = Field(default=1, ge=0, le=60)
    tls_verify: bool = True
    ca_bundle: str = ""
    trust_env_proxy: bool = False
    max_response_bytes: int = Field(default=10485760, ge=1024)
    mock_claims_per_day: int = Field(default=6, ge=0, le=10000)
    mock_scenario: Literal["none", "product_missing", "target_error", "target_timeout", "malformed_claim"] = (
        "none"
    )

    @model_validator(mode="after")
    def validate_config(self):
        ZoneInfo(self.timezone)
        if self.target_success_path:
            json.loads(self.target_success_value)
        for base in (self.claims_base_url, self.product_base_url, self.target_base_url):
            url = urlparse(base)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.query
                or url.fragment
            ):
                raise ValueError("API base URL must be an HTTP(S) URL without credentials, query or fragment")
        for path in (self.claims_path, self.product_schema_path, self.product_record_path, self.target_path):
            if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
                raise ValueError("API paths must start with a single / and contain no query or fragment")
        if "{part_id}" not in self.product_record_path:
            raise ValueError("PRODUCT_RECORD_PATH must include {part_id}")
        if not self.target_key_fields or any(
            k not in {"far_no", "sample_no", "ims_key"} for k in self.target_key_fields
        ):
            raise ValueError("TARGET_KEY_FIELDS must contain far_no, sample_no and/or ims_key")
        return self

    @property
    def target_url(self) -> str:
        return self.target_base_url.rstrip("/") + self.target_path

    @property
    def destination(self) -> str:
        return (
            f"{self.app_mode}|{self.target_url}|{self.target_dataset_id}|{','.join(self.target_key_fields)}"
        )

    @property
    def can_write(self) -> bool:
        return self.app_mode == "mock" or (self.allow_live_writes and self.target_upsert_confirmed)
