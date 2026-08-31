"""테스트용 설정 조립기.

파일을 거치지 않고 Config를 직접 만들어, 계산 로직만 따로 검증한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from finance.config import Account, Config, Income, Profile, Scenario, ScheduledExpense
from finance.months import Month


def make_profile(**overrides) -> Profile:
    defaults = dict(
        resignation_date=dt.date(2026, 6, 30),
        as_of=Month(2026, 8),
        currency="KRW",
        household_size=1,
        target_runway_months=12,
        emergency_reserve=0,
        burn_window_months=3,
    )
    defaults.update(overrides)
    return Profile(**defaults)


def make_scenario(**overrides) -> Scenario:
    defaults = dict(
        id="base",
        name="기본",
        monthly_spend=1_000_000,
        spend_multiplier=1.0,
        income_confidence=frozenset({"confirmed", "expected"}),
        use_tier="primary",
    )
    defaults.update(overrides)
    return Scenario(**defaults)


def make_config(
    *,
    accounts: list[Account] | None = None,
    incomes: list[Income] | None = None,
    scheduled: list[ScheduledExpense] | None = None,
    scenarios: list[Scenario] | None = None,
    profile: Profile | None = None,
) -> Config:
    return Config(
        profile=profile or make_profile(),
        accounts=accounts
        if accounts is not None
        else [
            Account(id="cash", name="현금", type="cash", liquidity="instant"),
            Account(id="stock", name="주식", type="investment", liquidity="long"),
            Account(id="irp", name="IRP", type="pension", liquidity="locked"),
            Account(id="loan", name="대출", type="debt", liquidity="locked"),
        ],
        incomes=incomes or [],
        scheduled_expenses=scheduled or [],
        scenarios=scenarios or [make_scenario()],
        checklist=[],
        data_dir=Path("."),
    )


def income(**overrides) -> Income:
    defaults = dict(
        id="unemployment",
        name="구직급여",
        amount=1_000_000,
        start=Month(2026, 9),
        end=Month(2027, 2),
        confidence="expected",
        note="",
    )
    defaults.update(overrides)
    return Income(**defaults)


def scheduled(**overrides) -> ScheduledExpense:
    defaults = dict(id="tax", name="세금", amount=1_000_000, months=(Month(2027, 5),), note="")
    defaults.update(overrides)
    return ScheduledExpense(**defaults)
