import datetime as dt
import unittest

from finance.ledger import BurnRate, Snapshot
from finance.months import Month
from finance.runway import liquid_balance, required_spend_for, resolve_monthly_spend, simulate
from tests.helpers import income, make_config, make_profile, make_scenario, scheduled

AUG = Month(2026, 8)


def snapshot(**balances):
    return Snapshot(month=AUG, balances=balances)


def burn(monthly=1_000_000):
    return BurnRate(monthly=monthly, fixed=monthly // 2, variable=monthly - monthly // 2, months_used=(AUG,))


class LiquidBalanceTest(unittest.TestCase):
    def test_emergency_reserve_is_held_back(self):
        cfg = make_config(profile=make_profile(emergency_reserve=5_000_000))
        self.assertEqual(liquid_balance(cfg, snapshot(cash=12_000_000), "primary"), 7_000_000)

    def test_locked_and_debt_never_count(self):
        cfg = make_config()
        state = snapshot(cash=10_000_000, stock=20_000_000, irp=99_000_000, loan=8_000_000)
        self.assertEqual(liquid_balance(cfg, state, "primary"), 10_000_000)
        self.assertEqual(liquid_balance(cfg, state, "secondary"), 30_000_000)


class SimulateTest(unittest.TestCase):
    def test_simple_burn_down(self):
        cfg = make_config()
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(), burn())
        self.assertEqual(result.start_month, Month(2026, 9))
        self.assertEqual(result.months_survived, 10)
        self.assertEqual(result.depletion_month, Month(2027, 7))
        self.assertFalse(result.is_sustainable)

    def test_income_extends_the_runway(self):
        cfg = make_config(incomes=[income(amount=600_000, start=Month(2026, 9), end=Month(2027, 8))])
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(), burn())
        self.assertEqual(result.rows[0].income, 600_000)
        self.assertEqual(result.rows[0].closing, 9_600_000)
        self.assertGreater(result.months_survived, 10)

    def test_income_confidence_is_filtered_per_scenario(self):
        cfg = make_config(incomes=[income(confidence="optimistic", amount=900_000)])
        cautious = simulate(
            cfg,
            snapshot(cash=10_000_000),
            make_scenario(income_confidence=frozenset({"confirmed"})),
            burn(),
        )
        hopeful = simulate(
            cfg,
            snapshot(cash=10_000_000),
            make_scenario(income_confidence=frozenset({"confirmed", "optimistic"})),
            burn(),
        )
        self.assertEqual(cautious.rows[0].income, 0)
        self.assertEqual(hopeful.rows[0].income, 900_000)

    def test_income_stops_after_its_end_month(self):
        cfg = make_config(incomes=[income(amount=500_000, start=Month(2026, 9), end=Month(2026, 10))])
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(), burn())
        self.assertEqual([r.income for r in result.rows[:4]], [500_000, 500_000, 0, 0])

    def test_scheduled_expense_lands_in_its_month(self):
        cfg = make_config(scheduled=[scheduled(amount=3_000_000, months=(Month(2026, 10),))])
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(), burn())
        self.assertEqual(result.rows[0].scheduled, 0)
        self.assertEqual(result.rows[1].scheduled, 3_000_000)
        self.assertEqual(result.rows[1].closing, 5_000_000)

    def test_spend_multiplier_applies_to_the_measured_burn(self):
        cfg = make_config()
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(monthly_spend=None, spend_multiplier=1.2), burn())
        self.assertEqual(result.monthly_spend, 1_200_000)

    def test_measured_burn_is_used_when_scenario_leaves_it_blank(self):
        self.assertEqual(resolve_monthly_spend(make_scenario(monthly_spend=None), burn(1_700_000)), 1_700_000)

    def test_surplus_never_depletes(self):
        cfg = make_config(incomes=[income(amount=2_000_000, start=Month(2026, 9), end=Month(2099, 12))])
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(), burn(), horizon=24)
        self.assertTrue(result.is_sustainable)
        self.assertIsNone(result.depletion_month)
        self.assertEqual(result.months_survived, 24)

    def test_already_below_the_reserve_is_depleted_now(self):
        cfg = make_config(profile=make_profile(emergency_reserve=5_000_000))
        result = simulate(cfg, snapshot(cash=1_000_000), make_scenario(), burn())
        self.assertEqual(result.months_survived, 0)
        self.assertEqual(result.depletion_month, Month(2026, 9))

    def test_secondary_tier_reaches_further(self):
        cfg = make_config()
        state = snapshot(cash=10_000_000, stock=10_000_000)
        primary = simulate(cfg, state, make_scenario(use_tier="primary"), burn())
        secondary = simulate(cfg, state, make_scenario(use_tier="secondary"), burn())
        self.assertEqual(secondary.months_survived, primary.months_survived * 2)

    def test_rows_chain_opening_to_closing(self):
        cfg = make_config(incomes=[income(amount=300_000)], scheduled=[scheduled(amount=500_000, months=(Month(2026, 10),))])
        result = simulate(cfg, snapshot(cash=10_000_000), make_scenario(), burn())
        for previous, current in zip(result.rows, result.rows[1:]):
            self.assertEqual(current.opening, previous.closing)


class RequiredSpendTest(unittest.TestCase):
    def test_returns_none_when_target_is_already_met(self):
        cfg = make_config(profile=make_profile(target_runway_months=6))
        self.assertIsNone(required_spend_for(cfg, snapshot(cash=10_000_000), make_scenario(), burn(), 6))

    def test_finds_a_spend_level_that_reaches_the_target(self):
        cfg = make_config(profile=make_profile(target_runway_months=20))
        state = snapshot(cash=10_000_000)
        needed = required_spend_for(cfg, state, make_scenario(), burn(), 20)
        self.assertIsNotNone(needed)
        self.assertLess(needed, 1_000_000)

        reached = simulate(cfg, state, make_scenario(monthly_spend=needed), burn(), horizon=21)
        self.assertGreaterEqual(reached.months_survived, 20)

    def test_returns_none_when_even_zero_spending_falls_short(self):
        cfg = make_config(scheduled=[scheduled(amount=20_000_000, months=(Month(2026, 9),))])
        needed = required_spend_for(cfg, snapshot(cash=10_000_000), make_scenario(), burn(), 24)
        self.assertIsNone(needed)


if __name__ == "__main__":
    unittest.main()
