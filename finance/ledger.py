"""CSV 원장 읽기·쓰기.

잔액(balances.csv)은 '월말에 얼마 남아 있었나'의 스냅샷이고,
지출(expenses.csv)은 '그 달에 얼마 썼나'의 기록이다. 둘은 서로를
검증한다. 잔액이 줄어든 폭과 기록한 지출이 크게 다르면 어딘가 빠진 것이다.
"""

from __future__ import annotations

import csv
import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .config import EXPENSE_KINDS, Account, Config
from .months import Month

BALANCE_FIELDS = ["date", "account", "amount", "memo"]
EXPENSE_FIELDS = ["date", "category", "amount", "kind", "memo"]

#: 최근 실지출 평균에 넣지 않는 종류. 비정기 지출은 평균을 왜곡하므로
#: 미래 계획(cashflow_plan.yaml)에서 시점을 지정해 따로 다룬다.
RECURRING_KINDS = ("fixed", "variable")


class LedgerError(Exception):
    pass


@dataclass(frozen=True)
class BalanceRow:
    date: dt.date
    account: str
    amount: int
    memo: str = ""

    @property
    def month(self) -> Month:
        return Month(self.date.year, self.date.month)


@dataclass(frozen=True)
class ExpenseRow:
    date: dt.date
    category: str
    amount: int
    kind: str
    memo: str = ""

    @property
    def month(self) -> Month:
        return Month(self.date.year, self.date.month)


@dataclass(frozen=True)
class Snapshot:
    """특정 월말의 계좌별 잔액."""

    month: Month
    balances: dict[str, int]

    def total(self, accounts: list[Account], *, tier: str | None = None) -> int:
        total = 0
        for acc in accounts:
            if acc.is_debt:
                continue
            if tier is not None and not acc.in_tier(tier):
                continue
            total += self.balances.get(acc.id, 0)
        return total

    def debt_total(self, accounts: list[Account]) -> int:
        return sum(self.balances.get(a.id, 0) for a in accounts if a.is_debt)

    def net_worth(self, accounts: list[Account]) -> int:
        return self.total(accounts) - self.debt_total(accounts)


# ── 읽기 ────────────────────────────────────────────────────

def _parse_date(value: str, path: Path, line: int) -> dt.date:
    try:
        return dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise LedgerError(f"{path.name}:{line} 날짜 형식 오류 ({value!r}) — YYYY-MM-DD 여야 합니다.") from exc


def _parse_amount(value: str, path: Path, line: int) -> int:
    text = value.strip().replace(",", "").replace("_", "")
    if not text:
        return 0
    try:
        return int(round(float(text)))
    except ValueError as exc:
        raise LedgerError(f"{path.name}:{line} 금액이 숫자가 아닙니다 ({value!r}).") from exc


def load_balances(path: Path, *, known_accounts: set[str] | None = None) -> list[BalanceRow]:
    if not path.exists():
        return []
    rows: list[BalanceRow] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line, raw in enumerate(csv.DictReader(fh), start=2):
            if not (raw.get("date") or "").strip():
                continue
            account = (raw.get("account") or "").strip()
            if known_accounts is not None and account not in known_accounts:
                raise LedgerError(
                    f"{path.name}:{line} accounts.yaml에 없는 계좌입니다: {account!r}"
                )
            rows.append(
                BalanceRow(
                    date=_parse_date(raw["date"], path, line),
                    account=account,
                    amount=_parse_amount(raw.get("amount", "0"), path, line),
                    memo=(raw.get("memo") or "").strip(),
                )
            )
    return rows


def load_expenses(path: Path) -> list[ExpenseRow]:
    if not path.exists():
        return []
    rows: list[ExpenseRow] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for line, raw in enumerate(csv.DictReader(fh), start=2):
            if not (raw.get("date") or "").strip():
                continue
            kind = (raw.get("kind") or "variable").strip() or "variable"
            if kind not in EXPENSE_KINDS:
                raise LedgerError(
                    f"{path.name}:{line} kind는 {'/'.join(sorted(EXPENSE_KINDS))} 중 하나여야 합니다 (받은 값: {kind!r})."
                )
            rows.append(
                ExpenseRow(
                    date=_parse_date(raw["date"], path, line),
                    category=(raw.get("category") or "미분류").strip(),
                    amount=_parse_amount(raw.get("amount", "0"), path, line),
                    kind=kind,
                    memo=(raw.get("memo") or "").strip(),
                )
            )
    return rows


# ── 집계 ────────────────────────────────────────────────────

def snapshots(rows: list[BalanceRow]) -> list[Snapshot]:
    """월별 스냅샷. 같은 달에 여러 번 기록했으면 가장 마지막 것을 쓴다."""
    by_month: dict[Month, dict[str, tuple[dt.date, int]]] = defaultdict(dict)
    for row in rows:
        month = Month(row.date.year, row.date.month)
        previous = by_month[month].get(row.account)
        if previous is None or row.date >= previous[0]:
            by_month[month][row.account] = (row.date, row.amount)
    return [
        Snapshot(month=month, balances={acc: amt for acc, (_, amt) in entries.items()})
        for month, entries in sorted(by_month.items())
    ]


def latest_snapshot(rows: list[BalanceRow], *, on_or_before: Month | None = None) -> Snapshot | None:
    available = snapshots(rows)
    if on_or_before is not None:
        available = [s for s in available if s.month <= on_or_before]
    return available[-1] if available else None


def monthly_expense_totals(rows: list[ExpenseRow]) -> dict[Month, dict[str, int]]:
    """{월: {kind: 합계}}. 'total' 키에 전체 합계도 넣는다."""
    totals: dict[Month, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        bucket = totals[row.month]
        bucket[row.kind] += row.amount
        bucket["total"] += row.amount
    return {month: dict(kinds) for month, kinds in sorted(totals.items())}


def category_totals(rows: list[ExpenseRow], month: Month) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int)
    for row in rows:
        if row.month == month:
            totals[row.category] += row.amount
    return dict(sorted(totals.items(), key=lambda kv: -kv[1]))


@dataclass(frozen=True)
class BurnRate:
    """최근 실지출로 계산한 월 소진 속도."""

    monthly: int
    fixed: int
    variable: int
    months_used: tuple[Month, ...]

    @property
    def is_estimated(self) -> bool:
        return not self.months_used


def burn_rate(rows: list[ExpenseRow], *, window: int, as_of: Month) -> BurnRate:
    """as_of 월까지(포함) 최근 window개월의 정기 지출 평균.

    비정기(irregular)는 제외한다. 결혼식 두 건이 겹친 달을 평균에 넣으면
    앞으로 매달 축의금을 낸다는 계산이 되어 런웨이가 근거 없이 짧아진다.
    """
    per_month: dict[Month, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in rows:
        if row.kind in RECURRING_KINDS and row.month <= as_of:
            per_month[row.month][row.kind] += row.amount

    months = sorted(per_month)[-window:]
    if not months:
        return BurnRate(monthly=0, fixed=0, variable=0, months_used=())

    fixed = sum(per_month[m]["fixed"] for m in months) // len(months)
    variable = sum(per_month[m]["variable"] for m in months) // len(months)
    return BurnRate(
        monthly=fixed + variable,
        fixed=fixed,
        variable=variable,
        months_used=tuple(months),
    )


def reconcile(
    balance_rows: list[BalanceRow],
    expense_rows: list[ExpenseRow],
    cfg: Config,
    *,
    tolerance_ratio: float = 0.25,
) -> list[str]:
    """잔액 변화와 기록한 지출이 앞뒤가 맞는지 본다.

    완벽히 맞을 수는 없다(수입, 투자 평가손익, 계좌 간 이체). 다만 유동자산이
    줄어든 폭보다 기록한 지출이 한참 적다면 빠뜨린 지출이 있다는 뜻이다.
    """
    issues: list[str] = []
    snaps = snapshots(balance_rows)
    totals = monthly_expense_totals(expense_rows)

    for previous, current in zip(snaps, snaps[1:]):
        if current.month - previous.month != 1:
            continue
        drop = previous.total(cfg.accounts, tier="primary") - current.total(cfg.accounts, tier="primary")
        recorded = totals.get(current.month, {}).get("total", 0)
        if drop <= 0 or recorded == 0:
            continue
        gap = drop - recorded
        if gap > max(drop * tolerance_ratio, 200_000):
            issues.append(
                f"{current.month} 유동자산은 {drop:,}원 줄었는데 기록된 지출은 "
                f"{recorded:,}원입니다. 약 {gap:,}원이 설명되지 않습니다."
            )
    return issues


# ── 쓰기 ────────────────────────────────────────────────────

def _append_row(path: Path, fields: list[str], row: dict[str, str]) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        if not exists:
            writer.writeheader()
        writer.writerow(row)


def append_balance(path: Path, row: BalanceRow) -> None:
    _append_row(
        path,
        BALANCE_FIELDS,
        {"date": row.date.isoformat(), "account": row.account, "amount": str(row.amount), "memo": row.memo},
    )


def append_expense(path: Path, row: ExpenseRow) -> None:
    _append_row(
        path,
        EXPENSE_FIELDS,
        {
            "date": row.date.isoformat(),
            "category": row.category,
            "amount": str(row.amount),
            "kind": row.kind,
            "memo": row.memo,
        },
    )
