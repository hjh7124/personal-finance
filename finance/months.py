"""월(月) 단위 시간 계산.

퇴사 후 현금흐름은 전부 월 단위로 움직인다. 날짜까지 따지면 코드만
복잡해지고 판단은 나아지지 않으므로, 이 체계의 시간 단위는 '월'이다.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass

_MONTH_RE = re.compile(r"^(\d{4})-(\d{1,2})$")


@dataclass(frozen=True, order=True)
class Month:
    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            raise ValueError(f"월은 1~12 사이여야 합니다: {self.month}")

    # ── 생성 ────────────────────────────────────────────────
    @classmethod
    def parse(cls, value: "Month | str | dt.date") -> "Month":
        if isinstance(value, Month):
            return value
        if isinstance(value, dt.datetime):
            value = value.date()
        if isinstance(value, dt.date):
            return cls(value.year, value.month)
        text = str(value).strip()
        m = _MONTH_RE.match(text)
        if m:
            return cls(int(m.group(1)), int(m.group(2)))
        try:
            d = dt.date.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"YYYY-MM 형식이 아닙니다: {value!r}") from exc
        return cls(d.year, d.month)

    @classmethod
    def today(cls) -> "Month":
        t = dt.date.today()
        return cls(t.year, t.month)

    # ── 연산 ────────────────────────────────────────────────
    def plus(self, n: int) -> "Month":
        total = (self.year * 12 + self.month - 1) + n
        return Month(total // 12, total % 12 + 1)

    def __sub__(self, other: "Month") -> int:
        """두 월 사이의 개월 수 차이."""
        return (self.year * 12 + self.month) - (other.year * 12 + other.month)

    def range_to(self, end: "Month") -> list["Month"]:
        """self부터 end까지(양끝 포함)."""
        if end < self:
            return []
        return [self.plus(i) for i in range(end - self + 1)]

    # ── 표현 ────────────────────────────────────────────────
    def __str__(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    def label(self) -> str:
        return f"{self.year}년 {self.month}월"

    def last_date(self) -> dt.date:
        nxt = self.plus(1)
        return dt.date(nxt.year, nxt.month, 1) - dt.timedelta(days=1)
