import calendar
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_type: Literal["claims", "csv"] = "claims"
    csv_filename: str | None = None
    period_mode: Literal["relative", "absolute"] = "relative"
    months: int = Field(default=1, ge=1, le=120)
    start_date: date | None = None
    end_date: date | None = None
    chunk_days: int = Field(default=7, ge=1, le=366)
    dry_run: bool = True

    @model_validator(mode="after")
    def valid_dates(self):
        if self.source_type == "csv":
            if not self.csv_filename or not self.csv_filename.strip():
                raise ValueError("CSV 파일 이름이 필요합니다.")
            return self
        if self.csv_filename is not None:
            raise ValueError("Claim 조회에는 CSV 파일을 지정할 수 없습니다.")
        if self.period_mode == "absolute":
            if not self.start_date or not self.end_date:
                raise ValueError("시작일과 종료일을 모두 입력하세요.")
            if self.start_date > self.end_date:
                raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
            if (self.end_date - self.start_date).days > 3660:
                raise ValueError("한 작업의 최대 기간은 10년입니다.")
        return self

    def resolve(self, timezone: str, now: datetime | None = None) -> tuple[date, date]:
        if self.source_type == "csv":
            raise ValueError("CSV 작업은 날짜 기간 대신 저장된 파일 데이터를 사용합니다.")
        if self.period_mode == "absolute":
            return self.start_date, self.end_date
        today = (now or datetime.now(ZoneInfo(timezone))).astimezone(ZoneInfo(timezone)).date()
        month_index = today.year * 12 + today.month - 1 - self.months
        year, month = divmod(month_index, 12)
        month += 1
        start = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
        return start, today


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    interval_minutes: int = Field(default=60, ge=1, le=525600)
    run: RunSpec = Field(default_factory=RunSpec)

    @model_validator(mode="after")
    def claims_schedule(self):
        if self.run.source_type != "claims":
            raise ValueError("이 스케줄은 Claim 조회용입니다. CSV는 가져오기 화면이나 CLI에서 실행하세요.")
        return self


def chunks(start: date, end: date, days: int):
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        yield cursor, stop
        if stop == end:
            break
        cursor = stop + timedelta(days=1)
