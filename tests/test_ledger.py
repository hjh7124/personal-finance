import datetime as dt
import tempfile
import unittest
from pathlib import Path

from finance import ledger
from finance.ledger import BalanceRow, ExpenseRow, LedgerError
from finance.months import Month
from tests.helpers import make_config


def expense(month, kind, amount, category="식비"):
    return ExpenseRow(date=Month.parse(month).last_date(), category=category, amount=amount, kind=kind)


class BurnRateTest(unittest.TestCase):
    def test_excludes_irregular_spending(self):
        rows = [
            expense("2026-06", "fixed", 1_000_000),
            expense("2026-06", "variable", 1_000_000),
            expense("2026-06", "irregular", 5_000_000, "경조사"),
        ]
        burn = ledger.burn_rate(rows, window=3, as_of=Month(2026, 6))
        self.assertEqual(burn.monthly, 2_000_000)

    def test_averages_only_the_window(self):
        rows = [
            expense("2026-04", "variable", 9_000_000),
            expense("2026-05", "variable", 1_000_000),
            expense("2026-06", "variable", 2_000_000),
            expense("2026-07", "variable", 3_000_000),
        ]
        burn = ledger.burn_rate(rows, window=3, as_of=Month(2026, 7))
        self.assertEqual(burn.monthly, 2_000_000)
        self.assertEqual(burn.months_used, (Month(2026, 5), Month(2026, 6), Month(2026, 7)))

    def test_ignores_months_after_as_of(self):
        rows = [expense("2026-06", "variable", 1_000_000), expense("2026-09", "variable", 9_000_000)]
        burn = ledger.burn_rate(rows, window=3, as_of=Month(2026, 6))
        self.assertEqual(burn.monthly, 1_000_000)

    def test_empty_ledger_is_marked_estimated(self):
        burn = ledger.burn_rate([], window=3, as_of=Month(2026, 6))
        self.assertTrue(burn.is_estimated)
        self.assertEqual(burn.monthly, 0)


class SnapshotTest(unittest.TestCase):
    def test_latest_entry_in_a_month_wins(self):
        rows = [
            BalanceRow(dt.date(2026, 8, 1), "cash", 1_000_000),
            BalanceRow(dt.date(2026, 8, 31), "cash", 2_000_000),
        ]
        self.assertEqual(ledger.latest_snapshot(rows).balances["cash"], 2_000_000)

    def test_on_or_before_ignores_future_snapshots(self):
        rows = [
            BalanceRow(dt.date(2026, 8, 31), "cash", 1_000_000),
            BalanceRow(dt.date(2026, 12, 31), "cash", 5_000_000),
        ]
        snapshot = ledger.latest_snapshot(rows, on_or_before=Month(2026, 9))
        self.assertEqual(snapshot.month, Month(2026, 8))

    def test_totals_respect_tier_and_debt(self):
        cfg = make_config()
        rows = [
            BalanceRow(dt.date(2026, 8, 31), "cash", 10_000_000),
            BalanceRow(dt.date(2026, 8, 31), "stock", 20_000_000),
            BalanceRow(dt.date(2026, 8, 31), "irp", 40_000_000),
            BalanceRow(dt.date(2026, 8, 31), "loan", 5_000_000),
        ]
        snapshot = ledger.latest_snapshot(rows)
        self.assertEqual(snapshot.total(cfg.accounts, tier="primary"), 10_000_000)
        self.assertEqual(snapshot.total(cfg.accounts, tier="secondary"), 30_000_000)
        self.assertEqual(snapshot.total(cfg.accounts), 70_000_000)
        self.assertEqual(snapshot.debt_total(cfg.accounts), 5_000_000)
        self.assertEqual(snapshot.net_worth(cfg.accounts), 65_000_000)


class CsvIoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def write(self, name, text):
        path = self.dir / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_unknown_account_is_rejected(self):
        path = self.write("balances.csv", "date,account,amount,memo\n2026-08-31,typo_account,100,\n")
        with self.assertRaises(LedgerError) as ctx:
            ledger.load_balances(path, known_accounts={"cash"})
        self.assertIn("typo_account", str(ctx.exception))

    def test_bad_kind_is_rejected(self):
        path = self.write("expenses.csv", "date,category,amount,kind,memo\n2026-08-31,식비,100,점심,\n")
        with self.assertRaises(LedgerError):
            ledger.load_expenses(path)

    def test_amounts_with_commas_are_accepted(self):
        path = self.write("expenses.csv", 'date,category,amount,kind,memo\n2026-08-31,식비,"1,200",variable,\n')
        self.assertEqual(ledger.load_expenses(path)[0].amount, 1200)

    def test_bad_date_reports_the_line(self):
        path = self.write("expenses.csv", "date,category,amount,kind,memo\n2026/08/31,식비,100,variable,\n")
        with self.assertRaises(LedgerError) as ctx:
            ledger.load_expenses(path)
        self.assertIn(":2", str(ctx.exception))

    def test_append_then_read_round_trips(self):
        path = self.dir / "expenses.csv"
        ledger.append_expense(path, ExpenseRow(dt.date(2026, 9, 1), "식비", 3200, "variable", "점심"))
        ledger.append_expense(path, ExpenseRow(dt.date(2026, 9, 2), "교통", 1500, "variable", ""))
        rows = ledger.load_expenses(path)
        self.assertEqual([r.amount for r in rows], [3200, 1500])
        self.assertEqual(rows[0].memo, "점심")

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(ledger.load_expenses(self.dir / "nope.csv"), [])


class ReconcileTest(unittest.TestCase):
    def test_flags_unexplained_drop(self):
        cfg = make_config()
        balances = [
            BalanceRow(dt.date(2026, 7, 31), "cash", 10_000_000),
            BalanceRow(dt.date(2026, 8, 31), "cash", 6_000_000),
        ]
        expenses = [expense("2026-08", "variable", 1_000_000)]
        issues = ledger.reconcile(balances, expenses, cfg)
        self.assertEqual(len(issues), 1)
        self.assertIn("설명되지 않습니다", issues[0])

    def test_silent_when_numbers_line_up(self):
        cfg = make_config()
        balances = [
            BalanceRow(dt.date(2026, 7, 31), "cash", 10_000_000),
            BalanceRow(dt.date(2026, 8, 31), "cash", 8_000_000),
        ]
        expenses = [expense("2026-08", "variable", 2_000_000)]
        self.assertEqual(ledger.reconcile(balances, expenses, cfg), [])


if __name__ == "__main__":
    unittest.main()
