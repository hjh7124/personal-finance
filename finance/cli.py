"""명령줄 인터페이스.

기록은 짧게, 조회는 한 화면에. 자산관리 도구가 실패하는 가장 흔한 이유는
계산이 틀려서가 아니라 입력이 귀찮아서 안 쓰게 되기 때문이다.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from . import checklist as checklist_mod
from . import report
from .config import EXPENSE_KINDS, Config, ConfigError, load_config
from .ledger import (
    BalanceRow,
    BurnRate,
    ExpenseRow,
    IncomeRow,
    LedgerError,
    Snapshot,
    append_balance,
    append_expense,
    append_income,
    burn_rate,
    latest_snapshot,
    load_balances,
    load_expenses,
    load_income,
    reconcile,
)
from .money import won
from .months import Month
from .runway import simulate, simulate_all


class UsageError(Exception):
    """사용자가 고칠 수 있는 문제."""


# ── 상태 묶음 ───────────────────────────────────────────────

class Workspace:
    """설정 + 원장을 한 번만 읽어 들고 다니기 위한 묶음."""

    def __init__(self, data_dir: Path | None = None) -> None:
        self.cfg: Config = load_config(data_dir)
        self.balances_path = self.cfg.data_dir / "balances.csv"
        self.expenses_path = self.cfg.data_dir / "expenses.csv"
        self.income_path = self.cfg.data_dir / "income.csv"
        self.balance_rows = load_balances(self.balances_path, known_accounts=self.cfg.account_ids)
        self.expense_rows = load_expenses(self.expenses_path)
        self.income_rows = load_income(self.income_path)

    @property
    def as_of(self) -> Month:
        return self.cfg.profile.as_of or Month.today()

    @property
    def snapshot(self) -> Snapshot:
        snap = latest_snapshot(self.balance_rows, on_or_before=self.as_of)
        if snap is None:
            snap = latest_snapshot(self.balance_rows)
        if snap is None:
            raise UsageError(
                "balances.csv 에 잔액 기록이 없습니다. "
                "먼저 `fin snapshot --set 계좌id=금액` 으로 현재 잔액을 넣으세요."
            )
        return snap

    @property
    def burn(self) -> BurnRate:
        return burn_rate(
            self.expense_rows,
            window=self.cfg.profile.burn_window_months,
            as_of=self.as_of,
            adjustment=sum(a.amount for a in self.cfg.profile.burn_adjustments),
        )


# ── 명령 ───────────────────────────────────────────────────

def cmd_status(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    snapshot, burn = ws.snapshot, ws.burn
    results = simulate_all(ws.cfg, snapshot, burn)
    statuses = checklist_mod.evaluate(ws.cfg)

    resigned = ws.cfg.profile.resignation_date
    print(report.header(
        "자산 현황 요약",
        (f"퇴사일 {resigned} · " if resigned else "")
        + f"기준 {snapshot.month} · 작성 {dt.date.today()}",
    ))
    print(report.render_assets(ws.cfg, snapshot))
    print(report.render_burn(burn, ws.cfg))
    print(report.render_runway_table(ws.cfg, results))
    print(report.render_diagnosis(ws.cfg, snapshot, burn, results))
    alerts = report.render_alerts(statuses)
    if alerts:
        print(alerts)
    warnings = report.render_warnings(
        ws.cfg, reconcile(ws.balance_rows, ws.expense_rows, ws.cfg, ws.income_rows)
    )
    if warnings:
        print(warnings)
    print()
    return 0


def cmd_runway(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    snapshot, burn = ws.snapshot, ws.burn

    if args.scenario:
        result = simulate(ws.cfg, snapshot, ws.cfg.scenario(args.scenario), burn)
        print(report.header(f"런웨이 — {result.scenario.name}", f"기준 {snapshot.month}"))
        print(report.render_runway_detail(ws.cfg, result, max_rows=args.months))
        print()
        return 0

    results = simulate_all(ws.cfg, snapshot, burn)
    print(report.header("런웨이", f"기준 {snapshot.month}"))
    print(report.render_runway_table(ws.cfg, results))
    print(report.render_diagnosis(ws.cfg, snapshot, burn, results))
    print()
    print("  개별 시나리오의 월별 흐름: fin runway --scenario <id>")
    print("  가능한 id: " + ", ".join(s.id for s in ws.cfg.scenarios))
    print()
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    month = Month.parse(args.month) if args.month else ws.as_of
    print(report.header(f"{month.label()} 리포트"))
    print(report.render_month_income(ws.income_rows, month))
    print(report.render_month(ws.cfg, ws.expense_rows, month))
    print(report.render_net_cashflow(ws.expense_rows, ws.income_rows, month))
    snapshot = latest_snapshot(ws.balance_rows, on_or_before=month)
    if snapshot:
        print(report.render_assets(ws.cfg, snapshot))
    print()
    return 0


def cmd_checklist(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    statuses = checklist_mod.evaluate(ws.cfg)
    print(report.header("퇴사 후 행정 체크리스트"))
    print(report.render_checklist(ws.cfg, statuses, show_done=args.all, show_notes=not args.brief))
    print()
    print("  처리한 항목은 data/checklist.yaml 에서 done: true 로 바꾸세요.")
    print()
    return 0


def cmd_accounts(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    print(report.header("계좌 목록"))
    print(report.render_assets(ws.cfg, ws.snapshot))
    print()
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    date = dt.date.fromisoformat(args.date) if args.date else _month_end(dt.date.today())
    month = Month(date.year, date.month)

    updates: dict[str, int] = {}
    for pair in args.set:
        if "=" not in pair:
            raise UsageError(f"--set 은 계좌id=금액 형식이어야 합니다: {pair!r}")
        account_id, _, raw = pair.partition("=")
        account_id = account_id.strip()
        if account_id not in ws.cfg.account_ids:
            known = ", ".join(sorted(ws.cfg.account_ids))
            raise UsageError(f"모르는 계좌입니다: {account_id}\n  가능: {known}")
        updates[account_id] = _parse_money(raw)

    if not updates:
        raise UsageError("기록할 잔액이 없습니다. 예: fin snapshot --set main_checking=5200000")

    if args.carry:
        previous = latest_snapshot(ws.balance_rows, on_or_before=month.plus(-1))
        if previous:
            for account_id, amount in previous.balances.items():
                updates.setdefault(account_id, amount)

    for account_id in sorted(updates, key=lambda i: [a.id for a in ws.cfg.accounts].index(i)):
        append_balance(
            ws.balances_path,
            BalanceRow(date=date, account=account_id, amount=updates[account_id], memo=args.memo or ""),
        )
    print(f"{date} 잔액 {len(updates)}건을 {ws.balances_path.name} 에 기록했습니다.")
    for account_id, amount in updates.items():
        print(f"  {ws.cfg.account(account_id).name}: {won(amount)}")
    return 0


def cmd_spend(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    row = ExpenseRow(
        date=date,
        category=args.category,
        amount=_parse_money(args.amount),
        kind=args.kind,
        memo=args.memo or "",
    )
    append_expense(ws.expenses_path, row)
    print(f"{row.date} {row.category} {won(row.amount)} ({row.kind}) 기록했습니다.")
    return 0


def cmd_earn(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    row = IncomeRow(
        date=dt.date.fromisoformat(args.date) if args.date else dt.date.today(),
        source=args.source,
        amount=_parse_money(args.amount),
        memo=args.memo or "",
    )
    append_income(ws.income_path, row)
    print(f"{row.date} {row.source} {won(row.amount)} 기록했습니다.")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    ws = Workspace(args.data_dir)
    print(report.header("설정·기록 점검"))
    print(
        f"  계좌 {len(ws.cfg.accounts)}개 · 잔액 {len(ws.balance_rows)}행 · "
        f"지출 {len(ws.expense_rows)}행 · 수입 {len(ws.income_rows)}행"
    )
    print(f"  시나리오 {len(ws.cfg.scenarios)}개 · 체크리스트 {len(ws.cfg.checklist)}항목")

    issues = reconcile(ws.balance_rows, ws.expense_rows, ws.cfg, ws.income_rows)
    problems = ws.cfg.warnings + issues
    if problems:
        print(report.render_warnings(ws.cfg, issues))
        print()
        return 1

    print("\n  문제 없습니다.\n")
    return 0


# ── 보조 ───────────────────────────────────────────────────

def _parse_money(text: str) -> int:
    cleaned = str(text).strip().replace(",", "").replace("_", "").replace("원", "")
    multiplier = 1
    if cleaned.endswith("만"):
        cleaned, multiplier = cleaned[:-1], 10_000
    elif cleaned.endswith("억"):
        cleaned, multiplier = cleaned[:-1], 100_000_000
    try:
        return int(round(float(cleaned) * multiplier))
    except ValueError as exc:
        raise UsageError(f"금액을 이해할 수 없습니다: {text!r} (예: 320000, 32만, 1.5억)") from exc


def _month_end(date: dt.date) -> dt.date:
    return Month(date.year, date.month).last_date()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fin",
        description="퇴사 후 자산관리 — 런웨이 중심의 현금흐름 관리 도구",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "예시:\n"
            "  fin                              한 화면 요약\n"
            "  fin runway --scenario worst      최악 시나리오의 월별 흐름\n"
            "  fin report --month 2026-08       그 달의 지출 분석\n"
            "  fin spend 식비 32000             지출 기록\n"
            "  fin earn 구직급여 189만          수입 기록\n"
            "  fin snapshot --set cma=18000000  월말 잔액 기록\n"
            "  fin checklist                    남은 행정 절차\n"
        ),
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="데이터 디렉터리 (기본: ./data)")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="자산·런웨이·기한을 한 화면에").set_defaults(func=cmd_status)

    p_runway = sub.add_parser("runway", help="시나리오별 런웨이")
    p_runway.add_argument("--scenario", help="특정 시나리오의 월별 흐름을 본다")
    p_runway.add_argument("--months", type=int, default=30, help="표시할 개월 수 (기본 30)")
    p_runway.set_defaults(func=cmd_runway)

    p_report = sub.add_parser("report", help="월간 지출 리포트")
    p_report.add_argument("--month", help="YYYY-MM (기본: 이번 달)")
    p_report.set_defaults(func=cmd_report)

    p_check = sub.add_parser("checklist", help="퇴사 후 행정 기한")
    p_check.add_argument("--all", action="store_true", help="완료한 항목도 표시")
    p_check.add_argument("--brief", action="store_true", help="설명 없이 목록만")
    p_check.set_defaults(func=cmd_checklist)

    sub.add_parser("accounts", help="계좌별 잔액").set_defaults(func=cmd_accounts)

    p_snap = sub.add_parser("snapshot", help="월말 잔액 기록")
    p_snap.add_argument("--set", action="append", default=[], metavar="계좌id=금액", help="반복 지정 가능")
    p_snap.add_argument("--date", help="YYYY-MM-DD (기본: 이번 달 말일)")
    p_snap.add_argument("--memo", help="메모")
    p_snap.add_argument(
        "--no-carry",
        dest="carry",
        action="store_false",
        help="지정하지 않은 계좌를 직전 스냅샷에서 가져오지 않는다",
    )
    p_snap.set_defaults(func=cmd_snapshot, carry=True)

    p_spend = sub.add_parser("spend", help="지출 기록")
    p_spend.add_argument("category", help="분류 (예: 식비)")
    p_spend.add_argument("amount", help="금액 (예: 32000, 3.2만)")
    p_spend.add_argument("--kind", choices=sorted(EXPENSE_KINDS), default="variable")
    p_spend.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    p_spend.add_argument("--memo", help="메모")
    p_spend.set_defaults(func=cmd_spend)

    p_earn = sub.add_parser("earn", help="수입 기록")
    p_earn.add_argument("source", help="출처 (예: 구직급여, 이자, 프리랜스)")
    p_earn.add_argument("amount", help="금액 (예: 1890000, 189만)")
    p_earn.add_argument("--date", help="YYYY-MM-DD (기본: 오늘)")
    p_earn.add_argument("--memo", help="메모")
    p_earn.set_defaults(func=cmd_earn)

    sub.add_parser("validate", help="설정과 기록의 앞뒤를 점검").set_defaults(func=cmd_validate)

    parser.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, LedgerError, UsageError) as exc:
        print(f"\n오류: {exc}\n", file=sys.stderr)
        return 2
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
