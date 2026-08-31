"""data/ 아래의 YAML 설정을 읽고 검증한다.

설정이 틀린 채로 계산이 돌아가는 것이 가장 나쁘다. 런웨이 숫자는 그럴듯해
보이기 때문에 조용히 틀리면 알아채기 어렵다. 그래서 로딩 단계에서
가능한 한 많이 막고, 못 막는 것은 경고로 남긴다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .months import Month

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

ACCOUNT_TYPES = {"cash", "deposit", "investment", "pension", "real_estate", "debt"}
LIQUIDITY_LEVELS = {"instant", "short", "long", "locked"}
CONFIDENCE_LEVELS = {"confirmed", "expected", "optimistic"}
EXPENSE_KINDS = {"fixed", "variable", "irregular"}

#: 런웨이 재원으로 인정할 유동성 등급
TIERS: dict[str, set[str]] = {
    "primary": {"instant", "short"},
    "secondary": {"instant", "short", "long"},
}

LIQUIDITY_LABELS = {
    "instant": "즉시",
    "short": "1개월 내",
    "long": "손실 감수",
    "locked": "인출 불가",
}


class ConfigError(Exception):
    """설정 파일이 이 체계의 가정을 깨뜨렸을 때."""


@dataclass(frozen=True)
class Account:
    id: str
    name: str
    type: str
    liquidity: str
    note: str = ""

    @property
    def is_debt(self) -> bool:
        return self.type == "debt"

    def in_tier(self, tier: str) -> bool:
        return not self.is_debt and self.liquidity in TIERS[tier]


@dataclass(frozen=True)
class Profile:
    #: 아직 적지 않았으면 None. 행정 기한 계산만 비활성화되고 나머지는 정상 동작한다.
    resignation_date: dt.date | None
    as_of: Month | None
    currency: str
    household_size: int
    target_runway_months: int
    emergency_reserve: int
    burn_window_months: int


@dataclass(frozen=True)
class Income:
    id: str
    name: str
    amount: int
    start: Month
    end: Month
    confidence: str
    note: str = ""

    def active_in(self, month: Month) -> bool:
        return self.start <= month <= self.end


@dataclass(frozen=True)
class ScheduledExpense:
    id: str
    name: str
    amount: int
    months: tuple[Month, ...]
    note: str = ""

    def amount_in(self, month: Month) -> int:
        return self.amount if month in self.months else 0


@dataclass(frozen=True)
class Scenario:
    id: str
    name: str
    monthly_spend: int | None
    spend_multiplier: float
    income_confidence: frozenset[str]
    use_tier: str


@dataclass(frozen=True)
class ChecklistItem:
    id: str
    name: str
    due_offset_days: int
    critical: bool
    done: bool
    where: str = ""
    note: str = ""

    def due_date(self, resignation_date: dt.date) -> dt.date:
        return resignation_date + dt.timedelta(days=self.due_offset_days)



@dataclass
class Config:
    profile: Profile
    accounts: list[Account]
    incomes: list[Income]
    scheduled_expenses: list[ScheduledExpense]
    scenarios: list[Scenario]
    checklist: list[ChecklistItem]
    data_dir: Path
    warnings: list[str] = field(default_factory=list)

    def account(self, account_id: str) -> Account:
        for acc in self.accounts:
            if acc.id == account_id:
                return acc
        raise ConfigError(f"accounts.yaml에 없는 계좌입니다: {account_id}")

    @property
    def account_ids(self) -> set[str]:
        return {a.id for a in self.accounts}

    def scenario(self, scenario_id: str) -> Scenario:
        for sc in self.scenarios:
            if sc.id == scenario_id:
                return sc
        known = ", ".join(s.id for s in self.scenarios)
        raise ConfigError(f"없는 시나리오입니다: {scenario_id} (가능: {known})")


# ── 로딩 헬퍼 ────────────────────────────────────────────────

def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"설정 파일이 없습니다: {path}")
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} 를 읽을 수 없습니다: {exc}") from exc
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ConfigError(f"{path.name} 의 최상위는 매핑이어야 합니다.")
    return loaded


def _require(mapping: dict[str, Any], key: str, where: str) -> Any:
    if key not in mapping or mapping[key] is None:
        raise ConfigError(f"{where}: '{key}' 항목이 필요합니다.")
    return mapping[key]


def _as_int(value: Any, where: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{where}: 숫자여야 합니다 (받은 값: {value!r})")
    return int(round(value))


def _as_date(value: Any, where: str) -> dt.date:
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value))
    except ValueError as exc:
        raise ConfigError(f"{where}: YYYY-MM-DD 형식이어야 합니다 ({value!r})") from exc


def _one_of(value: Any, allowed: set[str], where: str) -> str:
    text = str(value)
    if text not in allowed:
        raise ConfigError(f"{where}: '{text}' 는 허용되지 않습니다 (가능: {', '.join(sorted(allowed))})")
    return text


# ── 각 파일 로더 ─────────────────────────────────────────────

def load_profile(data_dir: Path) -> Profile:
    raw = _read_yaml(data_dir / "profile.yaml")
    where = "profile.yaml"
    as_of_raw = raw.get("as_of")
    resignation_raw = raw.get("resignation_date")
    return Profile(
        resignation_date=(
            _as_date(resignation_raw, f"{where}.resignation_date") if resignation_raw else None
        ),
        as_of=Month.parse(as_of_raw) if as_of_raw else None,
        currency=str(raw.get("currency", "KRW")),
        household_size=_as_int(raw.get("household_size", 1), f"{where}.household_size"),
        target_runway_months=_as_int(raw.get("target_runway_months", 12), f"{where}.target_runway_months"),
        emergency_reserve=_as_int(raw.get("emergency_reserve", 0), f"{where}.emergency_reserve"),
        burn_window_months=max(1, _as_int(raw.get("burn_window_months", 3), f"{where}.burn_window_months")),
    )


def load_accounts(data_dir: Path) -> list[Account]:
    raw = _read_yaml(data_dir / "accounts.yaml")
    entries = raw.get("accounts") or []
    if not entries:
        raise ConfigError("accounts.yaml 에 계좌가 하나도 없습니다.")
    accounts: list[Account] = []
    seen: set[str] = set()
    for entry in entries:
        where = f"accounts.yaml[{entry.get('id', '?')}]"
        account_id = str(_require(entry, "id", where))
        if account_id in seen:
            raise ConfigError(f"계좌 id가 중복됩니다: {account_id}")
        seen.add(account_id)
        accounts.append(
            Account(
                id=account_id,
                name=str(_require(entry, "name", where)),
                type=_one_of(_require(entry, "type", where), ACCOUNT_TYPES, f"{where}.type"),
                liquidity=_one_of(_require(entry, "liquidity", where), LIQUIDITY_LEVELS, f"{where}.liquidity"),
                note=str(entry.get("note", "") or ""),
            )
        )
    return accounts


def load_cashflow_plan(data_dir: Path) -> tuple[list[Income], list[ScheduledExpense]]:
    raw = _read_yaml(data_dir / "cashflow_plan.yaml")

    incomes: list[Income] = []
    for entry in raw.get("incomes") or []:
        where = f"cashflow_plan.yaml/incomes[{entry.get('id', '?')}]"
        start = Month.parse(_require(entry, "start", where))
        end = Month.parse(_require(entry, "end", where))
        if end < start:
            raise ConfigError(f"{where}: end({end})가 start({start})보다 빠릅니다.")
        incomes.append(
            Income(
                id=str(_require(entry, "id", where)),
                name=str(_require(entry, "name", where)),
                amount=_as_int(_require(entry, "amount", where), f"{where}.amount"),
                start=start,
                end=end,
                confidence=_one_of(entry.get("confidence", "expected"), CONFIDENCE_LEVELS, f"{where}.confidence"),
                note=str(entry.get("note", "") or ""),
            )
        )

    scheduled: list[ScheduledExpense] = []
    for entry in raw.get("scheduled_expenses") or []:
        where = f"cashflow_plan.yaml/scheduled_expenses[{entry.get('id', '?')}]"
        months = tuple(Month.parse(m) for m in (entry.get("months") or []))
        scheduled.append(
            ScheduledExpense(
                id=str(_require(entry, "id", where)),
                name=str(_require(entry, "name", where)),
                amount=_as_int(entry.get("amount", 0), f"{where}.amount"),
                months=months,
                note=str(entry.get("note", "") or ""),
            )
        )
    return incomes, scheduled


def load_scenarios(data_dir: Path) -> list[Scenario]:
    raw = _read_yaml(data_dir / "scenarios.yaml")
    entries = raw.get("scenarios") or []
    if not entries:
        raise ConfigError("scenarios.yaml 에 시나리오가 없습니다.")
    scenarios: list[Scenario] = []
    for entry in entries:
        where = f"scenarios.yaml[{entry.get('id', '?')}]"
        spend = entry.get("monthly_spend")
        confidences = entry.get("income_confidence") or ["confirmed"]
        scenarios.append(
            Scenario(
                id=str(_require(entry, "id", where)),
                name=str(entry.get("name", entry.get("id"))),
                monthly_spend=None if spend is None else _as_int(spend, f"{where}.monthly_spend"),
                spend_multiplier=float(entry.get("spend_multiplier", 1.0)),
                income_confidence=frozenset(
                    _one_of(c, CONFIDENCE_LEVELS, f"{where}.income_confidence") for c in confidences
                ),
                use_tier=_one_of(entry.get("use_tier", "primary"), set(TIERS), f"{where}.use_tier"),
            )
        )
    return scenarios


def load_checklist(data_dir: Path) -> list[ChecklistItem]:
    path = data_dir / "checklist.yaml"
    if not path.exists():
        return []
    raw = _read_yaml(path)
    items: list[ChecklistItem] = []
    for entry in raw.get("items") or []:
        where = f"checklist.yaml[{entry.get('id', '?')}]"
        items.append(
            ChecklistItem(
                id=str(_require(entry, "id", where)),
                name=str(_require(entry, "name", where)),
                due_offset_days=_as_int(entry.get("due_offset_days", 0), f"{where}.due_offset_days"),
                critical=bool(entry.get("critical", False)),
                done=bool(entry.get("done", False)),
                where=str(entry.get("where", "") or ""),
                note=" ".join(str(entry.get("note", "") or "").split()),
            )
        )
    return items


def load_config(data_dir: Path | str | None = None) -> Config:
    directory = Path(data_dir) if data_dir else DATA_DIR
    profile = load_profile(directory)
    accounts = load_accounts(directory)
    incomes, scheduled = load_cashflow_plan(directory)
    cfg = Config(
        profile=profile,
        accounts=accounts,
        incomes=incomes,
        scheduled_expenses=scheduled,
        scenarios=load_scenarios(directory),
        checklist=load_checklist(directory),
        data_dir=directory,
    )
    cfg.warnings.extend(_soft_checks(cfg))
    return cfg


def _soft_checks(cfg: Config) -> list[str]:
    """막을 수는 없지만 알고는 있어야 하는 것들."""
    notes: list[str] = []
    if not any(a.in_tier("primary") for a in cfg.accounts):
        notes.append("유동성 instant/short 계좌가 하나도 없습니다. 런웨이가 0으로 계산됩니다.")
    if cfg.profile.resignation_date is None:
        notes.append("profile.yaml 에 퇴사일(resignation_date)이 없습니다. 행정 기한 알림이 꺼집니다.")
    if cfg.profile.emergency_reserve <= 0:
        notes.append("비상금(emergency_reserve)이 0입니다. 잔고 바닥까지 쓰는 전제로 계산됩니다.")
    for income in cfg.incomes:
        if income.confidence == "optimistic":
            continue
        if income.end - income.start > 24:
            notes.append(f"수입 '{income.name}' 이 24개월 넘게 이어집니다. 기간이 맞는지 확인하세요.")
    return notes
