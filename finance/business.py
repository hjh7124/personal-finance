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

#: 정산 상태.
#:   미정산   — 개인 돈으로 냈고 아직 못 받음 (빈 값도 이걸로 본다)
#:   정산완료 — 개인 돈으로 냈고 받았음
#:   법인카드 — 회사가 직접 결제. 애초에 내 통장에서 나가지 않았다
#:   선지급   — 미리 받은 경비에서 나감. 받은 시점과 쓴 시점이 상쇄되므로
#:              개인 자산은 줄지 않는다
#:   자부담   — 내가 냈고 받을 생각이 없음
#: 받았다고 표시하기 전까지는 못 받은 돈으로 본다.
SETTLED_VALUES = {"", "미정산", "정산완료", "법인카드", "선지급", "자부담"}

#: 개인 자산을 실제로 줄이지 않는 결제 방식
NO_DRAIN = {"법인카드", "선지급"}

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
        """아직 돌려받지 못한 돈."""
        return self.settled in ("", "미정산")

    @property
    def hit_my_account(self) -> bool:
        """개인 자산을 줄였는가.

        법인카드는 애초에 내 돈이 아니고, 선지급은 받은 돈에서 나가므로
        받은 시점과 쓴 시점이 상쇄된다. 둘 다 개인 자산을 줄이지 않는다.
        """
        return self.settled not in NO_DRAIN


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


def by_settled(rows: list[BusinessCost]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        totals[row.settled] += row.amount
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


def paid_from_my_account(rows: list[BusinessCost]) -> int:
    """내 통장에서 실제로 나간 업무 경비. 법인카드 결제는 빠진다."""
    return sum(r.amount for r in rows if r.hit_my_account)


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


# ── 예산 ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Budget:
    """경비 예산.

    period 가 monthly 면 매달 새로 채워지는 한도, total 이면 프로젝트 전체에
    한 번 주어진 금액이다. 같은 100만원이라도 읽는 법이 완전히 다르다.
    """

    id: str
    name: str
    amount: int
    period: str
    site: str | None = None
    start: Month | None = None
    end_date: dt.date | None = None
    note: str = ""

    def covers(self, cost: BusinessCost) -> bool:
        if self.site and cost.site != self.site:
            return False
        if self.start and cost.month < self.start:
            return False
        return True


@dataclass(frozen=True)
class BudgetStatus:
    budget: Budget
    spent: int
    months_counted: int

    @property
    def limit(self) -> int:
        return self.budget.amount

    @property
    def remaining(self) -> int:
        return self.limit - self.spent

    @property
    def ratio(self) -> float:
        return self.spent / self.limit if self.limit else 0.0

    @property
    def is_over(self) -> bool:
        return self.spent > self.limit

    def months_left(self, monthly_rate: int) -> float | None:
        """지금 속도로 남은 예산이 몇 달치인지. monthly 예산에는 의미가 없다."""
        if self.budget.period != "total" or monthly_rate <= 0:
            return None
        return max(0.0, self.remaining / monthly_rate)


@dataclass(frozen=True)
class BudgetPace:
    """한도 안에서 움직이고 있는가.

    소진률만으로는 늦다. 64%를 쓴 것이 문제인지 아닌지는 며칠이 지났고
    앞으로 며칠 더 나가야 하는지에 달려 있다. 그래서 이 계산의 중심은
    비율이 아니라 **하루 단가**다 — 한도를 예상 현장 일수로 나눈 값이
    넘지 말아야 할 선이고, 실제 하루 단가가 그 아래면 한도 안에 들어온다.
    """

    status: BudgetStatus
    elapsed_days: int
    total_days: int
    workdays: int
    per_workday: int
    per_workday_source: str
    expected_workdays: int

    #: 이 일수보다 적게 지났으면 월말 추정을 하지 않는다. 표본이 너무 적다.
    MIN_DAYS_TO_PROJECT = 5

    @property
    def remaining_days(self) -> int:
        return max(0, self.total_days - self.elapsed_days)

    @property
    def affordable_rate(self) -> int:
        """현장 하루당 넘지 말아야 할 금액. 한도 ÷ 예상 현장 일수."""
        return self.status.limit // max(1, self.expected_workdays)

    @property
    def rate_headroom(self) -> int:
        """허용 단가에서 실제 단가를 뺀 값. 음수면 이 페이스로는 한도를 넘긴다."""
        return self.affordable_rate - self.per_workday

    @property
    def affordable_workdays(self) -> int:
        """남은 예산으로 앞으로 몇 번 더 나갈 수 있나."""
        if self.per_workday <= 0:
            return 0
        return max(0, self.status.remaining // self.per_workday)

    @property
    def projected(self) -> int | None:
        """지금 페이스로 갔을 때의 월말 금액. 초반에는 표본이 적어 내지 않는다."""
        if self.elapsed_days < self.MIN_DAYS_TO_PROJECT or not self.elapsed_days:
            return None
        return round(self.status.spent / self.elapsed_days * self.total_days)

    @property
    def on_track(self) -> bool | None:
        projected = self.projected
        return None if projected is None else projected <= self.status.limit


def pace(
    status: BudgetStatus,
    costs: list[BusinessCost],
    *,
    as_of: Month,
    today: dt.date,
    fallback_rate: int = 0,
    fallback_workdays: int = 0,
) -> BudgetPace:
    """예산 상태에 시간축을 붙인다.

    이번 달 현장 일수가 아직 적으면 하루 단가와 예상 현장 일수를 지난달
    실적에서 빌려온다. 한 번 나간 날을 그 달 전체로 늘려 잡으면 예측이 요동친다.
    """
    scoped = [c for c in costs if c.month == as_of and status.budget.covers(c)]
    total_days = as_of.last_date().day
    elapsed = min(total_days, today.day) if Month(today.year, today.month) == as_of else total_days
    days = workdays(scoped)

    if days >= 3:
        rate, source = total(scoped) // days, "이번 달 실적"
    elif fallback_rate:
        rate, source = fallback_rate, "지난달 실적"
    elif days:
        rate, source = total(scoped) // days, "이번 달 실적 (표본 적음)"
    else:
        rate, source = 0, "기록 없음"

    if days >= 3 and elapsed:
        expected = round(days / elapsed * total_days)
    else:
        expected = fallback_workdays or days or 1

    return BudgetPace(
        status=status,
        elapsed_days=elapsed,
        total_days=total_days,
        workdays=days,
        per_workday=rate,
        per_workday_source=source,
        expected_workdays=max(1, expected),
    )


def month_reference(
    costs: list[BusinessCost], month: Month, budget: Budget | None = None
) -> tuple[int, int]:
    """그 달의 (현장 하루당 평균, 현장 일수). 다음 달 예측의 기준선으로 쓴다."""
    scoped = [c for c in costs if c.month == month and (budget is None or budget.covers(c))]
    days = workdays(scoped)
    return (total(scoped) // days if days else 0), days


def _as_date(value: object) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    return dt.date.fromisoformat(str(value))


def load_budgets(data_dir: Path) -> list[Budget]:
    from .config import ConfigError, read_yaml

    path = data_dir / "business_budget.yaml"
    if not path.exists():
        return []
    raw = read_yaml(path)
    budgets: list[Budget] = []
    for entry in raw.get("budgets") or []:
        where = f"business_budget.yaml[{entry.get('id', '?')}]"
        period = str(entry.get("period", "monthly"))
        if period not in ("monthly", "total"):
            raise ConfigError(f"{where}: period 는 monthly 또는 total 이어야 합니다 ({period!r})")
        if "amount" not in entry:
            raise ConfigError(f"{where}: 'amount' 항목이 필요합니다.")
        budgets.append(
            Budget(
                id=str(entry.get("id") or entry.get("name")),
                name=str(entry.get("name", entry.get("id"))),
                amount=int(round(entry["amount"])),
                period=period,
                site=str(entry["site"]) if entry.get("site") else None,
                start=Month.parse(entry["start"]) if entry.get("start") else None,
                end_date=_as_date(entry["end_date"]) if entry.get("end_date") else None,
                note=" ".join(str(entry.get("note", "") or "").split()),
            )
        )
    return budgets


def evaluate_budget(budget: Budget, costs: list[BusinessCost], *, as_of: Month) -> BudgetStatus:
    """as_of 시점의 예산 소진 상태.

    monthly 예산은 그 달만 본다 — 매달 새로 채워지는 한도이므로 지난달 지출을
    끌고 오면 한도를 넘긴 것처럼 보인다. total 예산은 start 이후 전부를 누적한다.
    """
    if budget.period == "monthly":
        scoped = [c for c in costs if budget.covers(c) and c.month == as_of]
    else:
        scoped = [c for c in costs if budget.covers(c) and c.month <= as_of]
    months = len({c.month for c in scoped})
    return BudgetStatus(budget=budget, spent=total(scoped), months_counted=months)


@dataclass(frozen=True)
class ProjectPace:
    """기한이 있는 총액 예산이 끝까지 버티는가.

    월 한도와는 질문이 다르다. 매달 채워지는 돈이 아니라 한 번 받은 돈이므로,
    남은 기간에 나갈 횟수를 곱해 끝까지 계산해봐야 한다. 여유가 있으면 남고,
    없으면 종료 전에 바닥난다.
    """

    status: BudgetStatus
    today: dt.date
    end_date: dt.date
    per_workday: int
    workday_ratio: float

    @property
    def days_left(self) -> int:
        return max(0, (self.end_date - self.today).days + 1)

    @property
    def expected_workdays_left(self) -> int:
        return round(self.days_left * self.workday_ratio)

    @property
    def projected_spend_left(self) -> int:
        return self.expected_workdays_left * self.per_workday

    @property
    def projected_final(self) -> int:
        return self.status.spent + self.projected_spend_left

    @property
    def headroom(self) -> int:
        """끝까지 쓰고 남는 돈. 음수면 종료 전에 바닥난다."""
        return self.status.remaining - self.projected_spend_left

    @property
    def allowance_per_workday(self) -> int:
        """남은 예산을 남은 출근 횟수로 나눈 값. 이 선을 넘으면 모자란다."""
        days = self.expected_workdays_left
        return self.status.remaining // days if days else self.status.remaining

    @property
    def runs_out_on(self) -> dt.date | None:
        """지금 단가로 예산이 바닥나는 날. 끝까지 버티면 None."""
        if self.headroom >= 0 or self.per_workday <= 0 or self.workday_ratio <= 0:
            return None
        affordable = self.status.remaining // self.per_workday
        return self.today + dt.timedelta(days=round(affordable / self.workday_ratio))


def project_pace(
    status: BudgetStatus,
    costs: list[BusinessCost],
    *,
    today: dt.date,
    reference_month: Month,
) -> ProjectPace | None:
    """총액 예산 + 종료일이 있을 때의 소진 전망.

    현장에 나가는 빈도(workday_ratio)는 기준월 실적에서 가져온다.
    경비는 매일 나가지 않으므로 달력 일수로 나누면 과대 추정된다.
    """
    budget = status.budget
    if budget.period != "total" or budget.end_date is None:
        return None

    scoped = [c for c in costs if c.month == reference_month and budget.covers(c)]
    days = workdays(scoped)
    rate = total(scoped) // days if days else 0
    ratio = days / reference_month.last_date().day if days else 0.0
    return ProjectPace(
        status=status, today=today, end_date=budget.end_date,
        per_workday=rate, workday_ratio=ratio,
    )
