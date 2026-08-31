"""업무 경비 원장.

일하면서 쓴 돈은 생활비가 아니다. 현장을 오가며 낸 통행료와 주유비를
개인 지출에 섞으면 소진 속도가 부풀고, 그 숫자를 보고 엉뚱한 긴축을 하게
된다. 그래서 원장을 아예 분리한다.

다만 정산받기 전까지는 실제로 내 통장에서 나간 돈이다. 그래서 이 원장은
'얼마 썼나'가 아니라 **'얼마를 아직 못 받았나'**를 추적한다.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .ledger import LedgerError, append_row, parse_amount, parse_date
from .months import Month

COST_FIELDS = ["date", "site", "category", "amount", "settled", "memo"]

#: 정산 상태. 빈 값은 미정산으로 본다 — 받았다고 표시하기 전까지는 못 받은 돈이다.
SETTLED_VALUES = {"", "미정산", "정산완료", "자부담"}

import csv


@dataclass(frozen=True)
class BusinessCost:
    date: dt.date
    site: str
    category: str
    amount: int
    settled: str = "미정산"
    memo: str = ""

    @property
    def month(self) -> Month:
        return Month(self.date.year, self.date.month)

    @property
    def is_outstanding(self) -> bool:
        """아직 돌려받지 못한 돈. 자부담으로 표시한 것은 애초에 받을 게 아니다."""
        return self.settled in ("", "미정산")


def load_costs(path: Path) -> list[BusinessCost]:
    if not path.exists():
        return []
    rows: list[BusinessCost] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line, raw in enumerate(csv.DictReader(fh), start=2):
            if not (raw.get("date") or "").strip():
                continue
            settled = (raw.get("settled") or "").strip()
            if settled not in SETTLED_VALUES:
                raise LedgerError(
                    f"{path.name}:{line} settled 는 {'/'.join(sorted(SETTLED_VALUES - {''}))} "
                    f"중 하나이거나 비어 있어야 합니다 (받은 값: {settled!r})."
                )
            rows.append(
                BusinessCost(
                    date=parse_date(raw["date"], path, line),
                    site=(raw.get("site") or "미지정").strip(),
                    category=(raw.get("category") or "미분류").strip(),
                    amount=parse_amount(raw.get("amount", "0"), path, line),
                    settled=settled or "미정산",
                    memo=(raw.get("memo") or "").strip(),
                )
            )
    return rows


def append_cost(path: Path, row: BusinessCost) -> None:
    append_row(
        path,
        COST_FIELDS,
        {
            "date": row.date.isoformat(),
            "site": row.site,
            "category": row.category,
            "amount": str(row.amount),
            "settled": row.settled,
            "memo": row.memo,
        },
    )


# ── 집계 ────────────────────────────────────────────────────

def in_month(rows: list[BusinessCost], month: Month) -> list[BusinessCost]:
    return [r for r in rows if r.month == month]


def total(rows: list[BusinessCost]) -> int:
    return sum(r.amount for r in rows)


def outstanding(rows: list[BusinessCost]) -> int:
    """아직 정산받지 못한 금액. 사실상 나에게 갚아야 할 돈이다."""
    return sum(r.amount for r in rows if r.is_outstanding)


def by_category(rows: list[BusinessCost]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row.category] += row.amount
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def by_site(rows: list[BusinessCost]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row.site] += row.amount
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def by_month(rows: list[BusinessCost]) -> dict[Month, int]:
    totals: dict[Month, int] = defaultdict(int)
    for row in rows:
        totals[row.month] += row.amount
    return dict(sorted(totals.items()))


def workdays(rows: list[BusinessCost]) -> int:
    """경비가 발생한 날의 수. 현장에 나간 날로 본다."""
    return len({r.date for r in rows})
