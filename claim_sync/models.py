import calendar
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RunSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period_mode: Literal["relative", "absolute"] = "relative"
    months: int = Field(default=1, ge=1, le=120)
    start_date: date | None = None
    end_date: date | None = None
    chunk_days: int = Field(default=7, ge=1, le=366)
    dry_run: bool = True

    @model_validator(mode="after")
    def valid_dates(self):
        if self.period_mode == "absolute":
            if not self.start_date or not self.end_date:
                raise ValueError("시작일과 종료일을 모두 입력하세요.")
            if self.start_date > self.end_date:
                raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
            if (self.end_date - self.start_date).days > 3660:
                raise ValueError("한 작업의 최대 기간은 10년입니다.")
        return self

    def resolve(self, timezone: str, now: datetime | None = None) -> tuple[date, date]:
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


def chunks(start: date, end: date, days: int):
    cursor = start
    while cursor <= end:
        stop = min(cursor + timedelta(days=days - 1), end)
        yield cursor, stop
        if stop == end:
            break
        cursor = stop + timedelta(days=1)
