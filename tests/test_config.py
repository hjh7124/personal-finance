import datetime as dt
import tempfile
import unittest
from pathlib import Path

from finance.config import ConfigError, load_config
from finance.months import Month

EXAMPLE_DIR = Path(__file__).resolve().parent.parent / "data" / "example"
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

MINIMAL = {
    "profile.yaml": "resignation_date: 2026-06-30\ntarget_runway_months: 12\nemergency_reserve: 1000000\n",
    "accounts.yaml": "accounts:\n  - id: cash\n    name: 현금\n    type: cash\n    liquidity: instant\n",
    "cashflow_plan.yaml": "incomes: []\nscheduled_expenses: []\n",
    "scenarios.yaml": "scenarios:\n  - id: base\n    name: 기본\n    monthly_spend: 1000000\n",
}


class ShippedDataTest(unittest.TestCase):
    def test_working_data_dir_loads(self):
        cfg = load_config(DATA_DIR)
        self.assertTrue(cfg.accounts)
        self.assertTrue(cfg.checklist)

    def test_example_data_dir_loads(self):
        cfg = load_config(EXAMPLE_DIR)
        self.assertEqual(cfg.profile.resignation_date, dt.date(2026, 6, 30))
        self.assertTrue(any(s.id == "worst" for s in cfg.scenarios))
        self.assertEqual(cfg.warnings, [])


class ValidationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        for name, text in MINIMAL.items():
            (self.dir / name).write_text(text, encoding="utf-8")

    def write(self, name, text):
        (self.dir / name).write_text(text, encoding="utf-8")

    def test_minimal_config_loads(self):
        cfg = load_config(self.dir)
        self.assertEqual(cfg.profile.emergency_reserve, 1_000_000)
        self.assertEqual(cfg.scenarios[0].use_tier, "primary")

    def test_resignation_date_is_optional(self):
        self.write("profile.yaml", "resignation_date: null\n")
        cfg = load_config(self.dir)
        self.assertIsNone(cfg.profile.resignation_date)
        self.assertTrue(any("퇴사일" in w for w in cfg.warnings))

    def test_unknown_liquidity_is_rejected(self):
        self.write("accounts.yaml", "accounts:\n  - id: cash\n    name: 현금\n    type: cash\n    liquidity: 아무거나\n")
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertIn("liquidity", str(ctx.exception))

    def test_duplicate_account_id_is_rejected(self):
        self.write(
            "accounts.yaml",
            "accounts:\n"
            "  - id: cash\n    name: 현금\n    type: cash\n    liquidity: instant\n"
            "  - id: cash\n    name: 현금2\n    type: cash\n    liquidity: instant\n",
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertIn("중복", str(ctx.exception))

    def test_income_ending_before_it_starts_is_rejected(self):
        self.write(
            "cashflow_plan.yaml",
            "incomes:\n  - id: x\n    name: 수입\n    amount: 100\n    start: 2027-01\n    end: 2026-01\n",
        )
        with self.assertRaises(ConfigError):
            load_config(self.dir)

    def test_underscored_numbers_parse(self):
        self.write(
            "cashflow_plan.yaml",
            "incomes:\n  - id: x\n    name: 수입\n    amount: 1_890_000\n    start: 2026-09\n    end: 2027-02\n",
        )
        cfg = load_config(self.dir)
        self.assertEqual(cfg.incomes[0].amount, 1_890_000)
        self.assertEqual(cfg.incomes[0].start, Month(2026, 9))

    def test_missing_file_is_reported_by_name(self):
        (self.dir / "scenarios.yaml").unlink()
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.dir)
        self.assertIn("scenarios.yaml", str(ctx.exception))

    def test_unknown_scenario_lookup_lists_options(self):
        cfg = load_config(self.dir)
        with self.assertRaises(ConfigError) as ctx:
            cfg.scenario("없는거")
        self.assertIn("base", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
