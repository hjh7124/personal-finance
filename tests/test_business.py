import datetime as dt
import tempfile
import unittest
from pathlib import Path

from finance import business
from finance.business import BusinessCost
from finance.ledger import LedgerError
from finance.months import Month


def cost(day, category, amount, site="고양", settled="미정산"):
    return BusinessCost(
        date=dt.date(2026, 8, day), site=site, category=category, amount=amount, settled=settled
    )


class AggregationTest(unittest.TestCase):
    def setUp(self):
        self.rows = [
            cost(3, "주유", 50_000),
            cost(3, "톨비", 4_000),
            cost(3, "톨비", 2_100),
            cost(10, "톨비", 3_200, site="정릉", settled="정산완료"),
            cost(14, "식사", 22_000, site="정릉"),
        ]

    def test_totals_by_category_are_sorted(self):
        self.assertEqual(
            list(business.by_category(self.rows)), ["주유", "식사", "톨비", ]
        )
        self.assertEqual(business.by_category(self.rows)["톨비"], 9_300)

    def test_totals_by_site(self):
        self.assertEqual(business.by_site(self.rows), {"고양": 56_100, "정릉": 25_200})

    def test_total_counts_everything(self):
        self.assertEqual(business.total(self.rows), 81_300)

    def test_outstanding_excludes_settled(self):
        self.assertEqual(business.outstanding(self.rows), 81_300 - 3_200)

    def test_self_funded_is_not_outstanding(self):
        rows = [cost(3, "주유", 50_000, settled="자부담")]
        self.assertEqual(business.total(rows), 50_000)
        self.assertEqual(business.outstanding(rows), 0)

    def test_workdays_counts_distinct_dates(self):
        self.assertEqual(business.workdays(self.rows), 3)

    def test_in_month_filters(self):
        self.assertEqual(len(business.in_month(self.rows, Month(2026, 8))), 5)
        self.assertEqual(business.in_month(self.rows, Month(2026, 9)), [])

    def test_by_month_groups(self):
        self.assertEqual(business.by_month(self.rows), {Month(2026, 8): 81_300})


class CsvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_round_trip(self):
        path = self.dir / "business_costs.csv"
        business.append_cost(path, cost(3, "톨비", 3_400))
        business.append_cost(path, cost(4, "주유", 50_000, settled="정산완료"))
        rows = business.load_costs(path)
        self.assertEqual([r.amount for r in rows], [3_400, 50_000])
        self.assertTrue(rows[0].is_outstanding)
        self.assertFalse(rows[1].is_outstanding)

    def test_blank_settled_reads_as_outstanding(self):
        path = self.dir / "c.csv"
        path.write_text("date,site,category,amount,settled,memo\n2026-08-03,고양,톨비,3400,,\n", encoding="utf-8")
        self.assertTrue(business.load_costs(path)[0].is_outstanding)

    def test_unknown_settled_value_is_rejected(self):
        path = self.dir / "c.csv"
        path.write_text("date,site,category,amount,settled,memo\n2026-08-03,고양,톨비,3400,나중에,\n", encoding="utf-8")
        with self.assertRaises(LedgerError):
            business.load_costs(path)

    def test_missing_file_reads_as_empty(self):
        self.assertEqual(business.load_costs(self.dir / "none.csv"), [])


if __name__ == "__main__":
    unittest.main()


class BudgetTest(unittest.TestCase):
    def budget(self, **overrides):
        defaults = dict(
            id="site", name="현장 경비", amount=1_000_000, period="monthly",
            site="고양", start=Month(2026, 8),
        )
        defaults.update(overrides)
        return business.Budget(**defaults)

    def test_monthly_budget_looks_at_that_month_only(self):
        """매달 새로 채워지는 한도이므로 지난달 지출을 끌고 오지 않는다."""
        rows = [cost(3, "주유", 400_000), cost(3, "주유", 400_000, site="정릉")]
        status = business.evaluate_budget(self.budget(), rows, as_of=Month(2026, 8))
        self.assertEqual(status.spent, 400_000)      # 다른 현장은 빠진다
        self.assertEqual(status.limit, 1_000_000)
        self.assertFalse(status.is_over)

    def test_monthly_budget_ignores_other_months(self):
        july = BusinessCost(date=dt.date(2026, 9, 2), site="고양", category="주유", amount=900_000)
        rows = [cost(3, "주유", 400_000), july]
        aug = business.evaluate_budget(self.budget(), rows, as_of=Month(2026, 8))
        sep = business.evaluate_budget(self.budget(), rows, as_of=Month(2026, 9))
        self.assertEqual(aug.spent, 400_000)
        self.assertEqual(sep.spent, 900_000)

    def test_total_budget_accumulates_across_months(self):
        rows = [
            cost(3, "주유", 700_000),
            BusinessCost(date=dt.date(2026, 9, 2), site="고양", category="주유", amount=500_000),
        ]
        status = business.evaluate_budget(self.budget(period="total"), rows, as_of=Month(2026, 9))
        self.assertEqual(status.spent, 1_200_000)
        self.assertTrue(status.is_over)

    def test_total_budget_does_not_refill(self):
        rows = [cost(3, "주유", 700_000), cost(4, "주유", 500_000)]
        status = business.evaluate_budget(self.budget(period="total"), rows, as_of=Month(2026, 8))
        self.assertEqual(status.limit, 1_000_000)
        self.assertTrue(status.is_over)
        self.assertEqual(status.remaining, -200_000)

    def test_costs_before_the_start_month_are_excluded(self):
        early = BusinessCost(date=dt.date(2026, 7, 1), site="고양", category="주유", amount=900_000)
        status = business.evaluate_budget(self.budget(), [early], as_of=Month(2026, 8))
        self.assertEqual(status.spent, 0)

    def test_budget_without_a_site_covers_everything(self):
        rows = [cost(3, "주유", 100_000), cost(3, "주유", 200_000, site="정릉")]
        status = business.evaluate_budget(self.budget(site=None), rows, as_of=Month(2026, 8))
        self.assertEqual(status.spent, 300_000)

    def test_months_left_only_applies_to_total_budgets(self):
        rows = [cost(3, "주유", 500_000)]
        monthly = business.evaluate_budget(self.budget(), rows, as_of=Month(2026, 8))
        total_budget = business.evaluate_budget(self.budget(period="total"), rows, as_of=Month(2026, 8))
        self.assertIsNone(monthly.months_left(500_000))
        self.assertEqual(total_budget.months_left(500_000), 1.0)

    def test_months_left_is_zero_when_over_budget(self):
        rows = [cost(3, "주유", 1_500_000)]
        status = business.evaluate_budget(self.budget(period="total"), rows, as_of=Month(2026, 8))
        self.assertEqual(status.months_left(500_000), 0.0)


class BudgetLoadingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_missing_file_yields_no_budgets(self):
        self.assertEqual(business.load_budgets(self.dir), [])

    def test_loads_fields(self):
        (self.dir / "business_budget.yaml").write_text(
            "budgets:\n  - id: site\n    name: 현장 경비\n    amount: 1_000_000\n"
            "    period: total\n    site: 고양\n    start: 2026-08\n",
            encoding="utf-8",
        )
        budget = business.load_budgets(self.dir)[0]
        self.assertEqual((budget.amount, budget.period, budget.site), (1_000_000, "total", "고양"))
        self.assertEqual(budget.start, Month(2026, 8))

    def test_bad_period_is_rejected(self):
        from finance.config import ConfigError

        (self.dir / "business_budget.yaml").write_text(
            "budgets:\n  - id: x\n    name: x\n    amount: 100\n    period: 분기\n", encoding="utf-8"
        )
        with self.assertRaises(ConfigError):
            business.load_budgets(self.dir)


class PaymentSourceTest(unittest.TestCase):
    def test_corporate_card_never_hits_my_account(self):
        rows = [cost(3, "주유", 50_000, settled="법인카드")]
        self.assertEqual(business.total(rows), 50_000)
        self.assertEqual(business.paid_from_my_account(rows), 0)
        self.assertEqual(business.outstanding(rows), 0)

    def test_personal_payment_counts_both_ways(self):
        rows = [cost(3, "주유", 50_000)]
        self.assertEqual(business.paid_from_my_account(rows), 50_000)
        self.assertEqual(business.outstanding(rows), 50_000)

    def test_settled_payment_left_my_account_but_came_back(self):
        rows = [cost(3, "주유", 50_000, settled="정산완료")]
        self.assertEqual(business.paid_from_my_account(rows), 50_000)
        self.assertEqual(business.outstanding(rows), 0)

    def test_self_funded_left_my_account_and_stays_gone(self):
        rows = [cost(3, "주유", 50_000, settled="자부담")]
        self.assertEqual(business.paid_from_my_account(rows), 50_000)
        self.assertEqual(business.outstanding(rows), 0)

    def test_corporate_card_still_counts_against_the_budget(self):
        budget = business.Budget(
            id="b", name="현장", amount=1_000_000, period="monthly", site="고양", start=Month(2026, 8)
        )
        rows = [cost(3, "주유", 400_000, settled="법인카드")]
        self.assertEqual(business.evaluate_budget(budget, rows, as_of=Month(2026, 8)).spent, 400_000)


class PaceTest(unittest.TestCase):
    """한도 안에서 움직이는지는 비율이 아니라 하루 단가가 말해준다."""

    def budget(self, amount=1_000_000, period="monthly"):
        return business.Budget(id="b", name="경비", amount=amount, period=period, start=Month(2026, 8))

    def paced(self, rows, *, today, fallback_rate=0, fallback_workdays=0, month=Month(2026, 8)):
        status = business.evaluate_budget(self.budget(), rows, as_of=month)
        return business.pace(
            status, rows, as_of=month, today=today,
            fallback_rate=fallback_rate, fallback_workdays=fallback_workdays,
        )

    def test_allowed_rate_is_limit_over_expected_workdays(self):
        rows = [cost(d, "주유", 30_000) for d in (1, 5, 9, 13, 17, 21, 25, 29)]
        p = self.paced(rows, today=dt.date(2026, 8, 31))
        self.assertEqual(p.workdays, 8)
        self.assertEqual(p.expected_workdays, 8)
        self.assertEqual(p.affordable_rate, 125_000)
        self.assertEqual(p.per_workday, 30_000)
        self.assertGreater(p.rate_headroom, 0)

    def test_headroom_goes_negative_when_the_rate_is_too_high(self):
        rows = [cost(d, "주유", 200_000) for d in (1, 5, 9, 13, 17, 21, 25, 29)]
        p = self.paced(rows, today=dt.date(2026, 8, 31))
        self.assertEqual(p.per_workday, 200_000)
        self.assertLess(p.rate_headroom, 0)

    def test_thin_sample_borrows_last_month(self):
        rows = [cost(1, "톨비", 5_400)]
        p = self.paced(rows, today=dt.date(2026, 8, 2), fallback_rate=29_145, fallback_workdays=22)
        self.assertEqual(p.per_workday, 29_145)
        self.assertEqual(p.per_workday_source, "지난달 실적")
        self.assertEqual(p.expected_workdays, 22)

    def test_own_sample_wins_once_there_are_three_workdays(self):
        rows = [cost(d, "주유", 40_000) for d in (1, 2, 3)]
        p = self.paced(rows, today=dt.date(2026, 8, 31), fallback_rate=29_145, fallback_workdays=22)
        self.assertEqual(p.per_workday, 40_000)
        self.assertEqual(p.per_workday_source, "이번 달 실적")

    def test_projection_waits_for_enough_days(self):
        rows = [cost(1, "주유", 50_000)]
        early = self.paced(rows, today=dt.date(2026, 8, 2), fallback_rate=30_000, fallback_workdays=20)
        self.assertIsNone(early.projected)
        self.assertIsNone(early.on_track)

        later = self.paced(rows, today=dt.date(2026, 8, 31))
        self.assertEqual(later.projected, 50_000)
        self.assertTrue(later.on_track)

    def test_projection_flags_an_overrun(self):
        rows = [cost(d, "주유", 100_000) for d in range(1, 11)]
        p = self.paced(rows, today=dt.date(2026, 8, 10))
        self.assertGreater(p.projected, 1_000_000)
        self.assertFalse(p.on_track)

    def test_affordable_workdays_uses_the_remaining_budget(self):
        rows = [cost(1, "주유", 100_000)]
        p = self.paced(rows, today=dt.date(2026, 8, 2), fallback_rate=50_000, fallback_workdays=20)
        self.assertEqual(p.affordable_workdays, 900_000 // 50_000)

    def test_no_records_yields_no_rate(self):
        p = self.paced([], today=dt.date(2026, 8, 10))
        self.assertEqual(p.per_workday, 0)
        self.assertEqual(p.affordable_workdays, 0)
        self.assertEqual(p.per_workday_source, "기록 없음")

    def test_elapsed_days_cap_at_month_end_for_past_months(self):
        rows = [cost(1, "주유", 10_000)]
        p = self.paced(rows, today=dt.date(2026, 12, 25))
        self.assertEqual(p.elapsed_days, 31)
        self.assertEqual(p.remaining_days, 0)


class MonthReferenceTest(unittest.TestCase):
    def test_returns_rate_and_workdays(self):
        rows = [cost(1, "주유", 30_000), cost(1, "톨비", 10_000), cost(5, "주유", 20_000)]
        rate, days = business.month_reference(rows, Month(2026, 8))
        self.assertEqual(days, 2)
        self.assertEqual(rate, 30_000)

    def test_empty_month_is_zero(self):
        self.assertEqual(business.month_reference([], Month(2026, 8)), (0, 0))


class ProjectPaceTest(unittest.TestCase):
    """기한이 있는 총액 예산 — 끝까지 버티는가."""

    def budget(self, amount=1_000_000, end=dt.date(2026, 9, 18)):
        return business.Budget(
            id="b", name="프로젝트 경비", amount=amount, period="total",
            start=Month(2026, 8), end_date=end,
        )

    def august(self, per_day=30_000, days=20):
        return [cost(d, "주유", per_day) for d in range(1, days + 1)]

    def paced(self, rows, *, today, budget=None):
        budget = budget or self.budget()
        status = business.evaluate_budget(budget, rows, as_of=Month(2026, 9))
        return business.project_pace(status, rows, today=today, reference_month=Month(2026, 8))

    def test_projects_to_the_end_date_using_the_workday_ratio(self):
        p = self.paced(self.august(), today=dt.date(2026, 9, 1))
        self.assertEqual(p.days_left, 18)
        self.assertAlmostEqual(p.workday_ratio, 20 / 31, places=4)
        self.assertEqual(p.expected_workdays_left, round(18 * 20 / 31))
        self.assertEqual(p.per_workday, 30_000)

    def test_headroom_positive_when_the_budget_holds(self):
        p = self.paced(self.august(per_day=10_000), today=dt.date(2026, 9, 1))
        self.assertGreater(p.headroom, 0)
        self.assertIsNone(p.runs_out_on)

    def test_headroom_negative_and_a_runout_date_when_it_does_not(self):
        p = self.paced(self.august(per_day=45_000), today=dt.date(2026, 9, 1))
        self.assertLess(p.headroom, 0)
        self.assertIsNotNone(p.runs_out_on)
        self.assertGreater(p.runs_out_on, dt.date(2026, 9, 1))

    def test_allowance_is_remaining_over_remaining_workdays(self):
        rows = self.august()
        p = self.paced(rows, today=dt.date(2026, 9, 1))
        self.assertEqual(
            p.allowance_per_workday, p.status.remaining // p.expected_workdays_left
        )

    def test_past_the_end_date_nothing_is_left(self):
        p = self.paced(self.august(), today=dt.date(2026, 10, 1))
        self.assertEqual(p.days_left, 0)
        self.assertEqual(p.expected_workdays_left, 0)
        self.assertEqual(p.projected_spend_left, 0)
        self.assertEqual(p.projected_final, p.status.spent)

    def test_monthly_budget_gets_no_project_pace(self):
        budget = business.Budget(
            id="m", name="월 한도", amount=1_000_000, period="monthly", start=Month(2026, 8)
        )
        status = business.evaluate_budget(budget, self.august(), as_of=Month(2026, 9))
        self.assertIsNone(
            business.project_pace(status, self.august(), today=dt.date(2026, 9, 1),
                                  reference_month=Month(2026, 8))
        )

    def test_total_budget_without_an_end_date_gets_no_project_pace(self):
        budget = self.budget(end=None)
        status = business.evaluate_budget(budget, self.august(), as_of=Month(2026, 9))
        self.assertIsNone(
            business.project_pace(status, self.august(), today=dt.date(2026, 9, 1),
                                  reference_month=Month(2026, 8))
        )


class PrepaidTest(unittest.TestCase):
    def test_prepaid_does_not_drain_my_account(self):
        rows = [cost(3, "주유", 50_000, settled="선지급")]
        self.assertEqual(business.total(rows), 50_000)
        self.assertEqual(business.paid_from_my_account(rows), 0)
        self.assertEqual(business.outstanding(rows), 0)

    def test_prepaid_still_counts_against_the_budget(self):
        budget = business.Budget(
            id="b", name="경비", amount=1_000_000, period="total", start=Month(2026, 8)
        )
        rows = [cost(3, "주유", 400_000, settled="선지급")]
        self.assertEqual(business.evaluate_budget(budget, rows, as_of=Month(2026, 8)).spent, 400_000)
