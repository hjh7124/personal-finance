"""런웨이 시뮬레이션.

이 체계의 심장이다. "유동자산에서 비상금을 뺀 돈으로, 예정 수입을 받고
예상 지출을 내면서, 몇 번의 월말을 넘길 수 있는가"를 한 달씩 굴려서 센다.

일부러 단순하게 만들었다. 수익률도 물가상승률도 넣지 않는다. 소득 공백기의
자산관리에서 틀리면 치명적인 것은 수익률 가정이 아니라 '언제 바닥나는가'이고,
그 답은 복리 가정 없이도 충분히 정확하게 나온다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .config import Config, Scenario
from .ledger import BurnRate, Snapshot
from .months import Month

#: 시뮬레이션 최대 기간. 이보다 길게 버티면 사실상 '소진되지 않음'이다.
DEFAULT_HORIZON_MONTHS = 120


@dataclass(frozen=True)
class MonthRow:
    month: Month
    opening: int
    income: int
    spend: int
    scheduled: int

    @property
    def net(self) -> int:
        return self.income - self.spend - self.scheduled

    @property
    def closing(self) -> int:
        return self.opening + self.net


@dataclass
class RunwayResult:
    scenario: Scenario
    start_month: Month
    starting_balance: int
    monthly_spend: int
    rows: list[MonthRow] = field(default_factory=list)
    depletion_month: Month | None = None
    horizon_months: int = DEFAULT_HORIZON_MONTHS

    @property
    def months_survived(self) -> int:
        """잔고가 0 이상으로 끝난 월의 수."""
        return sum(1 for row in self.rows if row.closing >= 0)

    @property
    def is_sustainable(self) -> bool:
        """시뮬레이션 기간 안에 바닥나지 않음."""
        return self.depletion_month is None

    @property
    def steady_state_net(self) -> int:
        """예정 수입이 모두 끝난 뒤의 월 순현금흐름. 보통 음수다."""
        return -self.monthly_spend

    def summary(self) -> str:
        if self.is_sustainable:
            return f"{self.horizon_months}개월 내 소진되지 않음"
        return f"{self.months_survived}개월 (소진 예상: {self.depletion_month.label()})"


def liquid_balance(cfg: Config, snapshot: Snapshot, tier: str) -> int:
    """해당 등급에서 실제로 쓸 수 있는 돈 = 유동자산 − 비상금."""
    return snapshot.total(cfg.accounts, tier=tier) - cfg.profile.emergency_reserve


def resolve_monthly_spend(scenario: Scenario, burn: BurnRate) -> int:
    """시나리오의 월 지출. 지정값이 없으면 최근 실지출 평균을 쓴다."""
    base = scenario.monthly_spend if scenario.monthly_spend is not None else burn.monthly
    return int(round(base * scenario.spend_multiplier))


def income_in(cfg: Config, scenario: Scenario, month: Month) -> int:
    return sum(
        income.amount
        for income in cfg.incomes
        if income.confidence in scenario.income_confidence and income.active_in(month)
    )


def scheduled_in(cfg: Config, month: Month) -> int:
    return sum(item.amount_in(month) for item in cfg.scheduled_expenses)


def simulate(
    cfg: Config,
    snapshot: Snapshot,
    scenario: Scenario,
    burn: BurnRate,
    *,
    start_month: Month | None = None,
    horizon: int = DEFAULT_HORIZON_MONTHS,
) -> RunwayResult:
    """스냅샷 다음 달부터 한 달씩 굴린다."""
    start = start_month or snapshot.month.plus(1)
    balance = liquid_balance(cfg, snapshot, scenario.use_tier)
    monthly_spend = resolve_monthly_spend(scenario, burn)

    result = RunwayResult(
        scenario=scenario,
        start_month=start,
        starting_balance=balance,
        monthly_spend=monthly_spend,
        horizon_months=horizon,
    )

    if balance < 0:
        # 비상금 기준선 아래로 이미 내려와 있는 상태.
        result.depletion_month = start
        return result

    for offset in range(horizon):
        month = start.plus(offset)
        row = MonthRow(
            month=month,
            opening=balance,
            income=income_in(cfg, scenario, month),
            spend=monthly_spend,
            scheduled=scheduled_in(cfg, month),
        )
        result.rows.append(row)
        balance = row.closing
        if balance < 0:
            result.depletion_month = month
            break

    return result


def simulate_all(
    cfg: Config,
    snapshot: Snapshot,
    burn: BurnRate,
    *,
    horizon: int = DEFAULT_HORIZON_MONTHS,
) -> list[RunwayResult]:
    return [simulate(cfg, snapshot, sc, burn, horizon=horizon) for sc in cfg.scenarios]


def required_spend_for(
    cfg: Config,
    snapshot: Snapshot,
    scenario: Scenario,
    burn: BurnRate,
    target_months: int,
) -> int | None:
    """목표 런웨이를 채우려면 월 지출을 얼마까지 줄여야 하는지.

    이분 탐색. 이미 목표를 넘겼으면 None을 돌려준다 — 줄일 필요가 없다.
    """
    current = simulate(cfg, snapshot, scenario, burn, horizon=target_months + 1)
    if current.is_sustainable or current.months_survived >= target_months:
        return None

    def survives(spend: int) -> bool:
        probe = Scenario(
            id=scenario.id,
            name=scenario.name,
            monthly_spend=spend,
            spend_multiplier=1.0,
            income_confidence=scenario.income_confidence,
            use_tier=scenario.use_tier,
        )
        run = simulate(cfg, snapshot, probe, burn, horizon=target_months + 1)
        return run.is_sustainable or run.months_survived >= target_months

    low, high = 0, resolve_monthly_spend(scenario, burn)
    if not survives(low):
        return None  # 지출을 0으로 줄여도 목표에 못 미친다.
    while high - low > 10_000:
        mid = (low + high) // 2
        if survives(mid):
            low = mid
        else:
            high = mid
    return low
