import contextlib
import io
import shutil
import tempfile
import unittest
from pathlib import Path

from finance.cli import main

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = ROOT / "data" / "example"


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue() + err.getvalue()


class ReadOnlyCommandTest(unittest.TestCase):
    """예시 데이터로 모든 조회 명령이 실제로 끝까지 돈다."""

    def test_status(self):
        code, text = run("--data-dir", str(EXAMPLE_DIR), "status")
        self.assertEqual(code, 0)
        self.assertIn("런웨이", text)
        self.assertIn("판단", text)

    def test_default_command_is_status(self):
        code, text = run("--data-dir", str(EXAMPLE_DIR))
        self.assertEqual(code, 0)
        self.assertIn("자산 현황 요약", text)

    def test_runway_overview_and_detail(self):
        code, text = run("--data-dir", str(EXAMPLE_DIR), "runway")
        self.assertEqual(code, 0)
        self.assertIn("기본 (지금처럼 살면)", text)

        code, text = run("--data-dir", str(EXAMPLE_DIR), "runway", "--scenario", "worst", "--months", "5")
        self.assertEqual(code, 0)
        self.assertIn("월별 흐름", text)
        self.assertIn("생략", text)

    def test_unknown_scenario_exits_with_guidance(self):
        code, text = run("--data-dir", str(EXAMPLE_DIR), "runway", "--scenario", "없는거")
        self.assertEqual(code, 2)
        self.assertIn("base", text)

    def test_report_shows_income_and_net_cashflow(self):
        code, text = run("--data-dir", str(EXAMPLE_DIR), "report", "--month", "2026-08")
        self.assertEqual(code, 0)
        self.assertIn("수입", text)
        self.assertIn("순현금흐름", text)
        self.assertIn("CMA 월 이자".split()[0], text)

    def test_report_and_checklist_and_accounts(self):
        for argv in (["report", "--month", "2026-08"], ["checklist"], ["accounts"]):
            code, text = run("--data-dir", str(EXAMPLE_DIR), *argv)
            self.assertEqual(code, 0, msg=f"{argv} 실패:\n{text}")
            self.assertTrue(text.strip())

    def test_validate_is_clean_on_example_data(self):
        code, _ = run("--data-dir", str(EXAMPLE_DIR), "validate")
        self.assertEqual(code, 0)


class WriteCommandTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name) / "data"
        shutil.copytree(EXAMPLE_DIR, self.dir)
        self.addCleanup(self._tmp.cleanup)

    def test_spend_appends_a_row(self):
        code, text = run("--data-dir", str(self.dir), "spend", "식비", "3.2만", "--date", "2026-09-02")
        self.assertEqual(code, 0)
        self.assertIn("32,000원", text)
        self.assertIn("2026-09-02,식비,32000,variable,", (self.dir / "expenses.csv").read_text(encoding="utf-8"))

    def test_earn_appends_a_row(self):
        code, text = run("--data-dir", str(self.dir), "earn", "구직급여", "189만", "--date", "2026-09-15")
        self.assertEqual(code, 0)
        self.assertIn("1,890,000원", text)
        self.assertIn("2026-09-15,구직급여,1890000,", (self.dir / "income.csv").read_text(encoding="utf-8"))

    def test_earn_creates_the_file_when_missing(self):
        (self.dir / "income.csv").unlink()
        code, _ = run("--data-dir", str(self.dir), "earn", "이자", "5000")
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / "income.csv").read_text(encoding="utf-8").startswith("date,source,amount,memo"))

    def test_spend_rejects_unparseable_amount(self):
        code, text = run("--data-dir", str(self.dir), "spend", "식비", "삼만원")
        self.assertEqual(code, 2)
        self.assertIn("금액", text)

    def test_snapshot_carries_forward_untouched_accounts(self):
        code, _ = run(
            "--data-dir", str(self.dir), "snapshot", "--date", "2026-09-30", "--set", "main_checking=4000000"
        )
        self.assertEqual(code, 0)
        written = (self.dir / "balances.csv").read_text(encoding="utf-8")
        self.assertIn("2026-09-30,main_checking,4000000", written)
        # 손대지 않은 계좌는 직전 스냅샷 값이 따라온다.
        self.assertIn("2026-09-30,jeonse_deposit,150000000", written)

    def test_snapshot_without_carry_records_only_what_was_given(self):
        code, _ = run(
            "--data-dir", str(self.dir),
            "snapshot", "--date", "2026-09-30", "--set", "main_checking=4000000", "--no-carry",
        )
        self.assertEqual(code, 0)
        lines = [l for l in (self.dir / "balances.csv").read_text(encoding="utf-8").splitlines() if "2026-09-30" in l]
        self.assertEqual(len(lines), 1)

    def test_snapshot_rejects_unknown_account(self):
        code, text = run("--data-dir", str(self.dir), "snapshot", "--set", "없는계좌=100")
        self.assertEqual(code, 2)
        self.assertIn("모르는 계좌", text)

    def test_snapshot_requires_something_to_record(self):
        code, text = run("--data-dir", str(self.dir), "snapshot")
        self.assertEqual(code, 2)
        self.assertIn("기록할 잔액이 없습니다", text)

    def test_status_without_balances_explains_the_next_step(self):
        (self.dir / "balances.csv").write_text("date,account,amount,memo\n", encoding="utf-8")
        code, text = run("--data-dir", str(self.dir), "status")
        self.assertEqual(code, 2)
        self.assertIn("fin snapshot", text)


if __name__ == "__main__":
    unittest.main()
