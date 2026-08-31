import datetime as dt
import unittest

from finance.months import Month


class MonthTest(unittest.TestCase):
    def test_parse_accepts_month_date_and_month(self):
        self.assertEqual(Month.parse("2026-09"), Month(2026, 9))
        self.assertEqual(Month.parse("2026-9"), Month(2026, 9))
        self.assertEqual(Month.parse(dt.date(2026, 9, 30)), Month(2026, 9))
        self.assertEqual(Month.parse(Month(2026, 9)), Month(2026, 9))

    def test_parse_rejects_nonsense(self):
        with self.assertRaises(ValueError):
            Month.parse("2026년 9월")
        with self.assertRaises(ValueError):
            Month(2026, 13)

    def test_plus_crosses_year_boundary(self):
        self.assertEqual(Month(2026, 11).plus(3), Month(2027, 2))
        self.assertEqual(Month(2026, 1).plus(-1), Month(2025, 12))

    def test_difference_in_months(self):
        self.assertEqual(Month(2027, 2) - Month(2026, 11), 3)
        self.assertEqual(Month(2026, 11) - Month(2027, 2), -3)

    def test_range_is_inclusive_and_empty_when_reversed(self):
        self.assertEqual(
            Month(2026, 11).range_to(Month(2027, 1)),
            [Month(2026, 11), Month(2026, 12), Month(2027, 1)],
        )
        self.assertEqual(Month(2027, 1).range_to(Month(2026, 11)), [])

    def test_last_date_handles_february(self):
        self.assertEqual(Month(2027, 2).last_date(), dt.date(2027, 2, 28))
        self.assertEqual(Month(2028, 2).last_date(), dt.date(2028, 2, 29))

    def test_sorting_is_chronological(self):
        months = [Month(2027, 1), Month(2026, 12), Month(2026, 2)]
        self.assertEqual(sorted(months), [Month(2026, 2), Month(2026, 12), Month(2027, 1)])


if __name__ == "__main__":
    unittest.main()
