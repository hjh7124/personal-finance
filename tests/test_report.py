import unittest

from finance.money import bar, man, signed_man, won
from finance.report import display_width, pad, truncate, wrap


class WidthTest(unittest.TestCase):
    def test_hangul_counts_as_two_columns(self):
        self.assertEqual(display_width("현금"), 4)
        self.assertEqual(display_width("cash"), 4)
        self.assertEqual(display_width("현금 cash"), 9)

    def test_pad_aligns_mixed_scripts_to_the_same_width(self):
        self.assertEqual(display_width(pad("현금", 10)), 10)
        self.assertEqual(display_width(pad("cash", 10)), 10)
        self.assertEqual(display_width(pad("현금", 10, "right")), 10)

    def test_pad_never_truncates(self):
        self.assertEqual(pad("아주아주긴이름", 4), "아주아주긴이름")

    def test_truncate_marks_elision_and_fits(self):
        out = truncate("주식·ETF 계좌 장기보유분", 12)
        self.assertTrue(out.endswith("…"))
        self.assertLessEqual(display_width(out), 12)

    def test_truncate_leaves_short_text_alone(self):
        self.assertEqual(truncate("현금", 12), "현금")


class WrapTest(unittest.TestCase):
    def test_every_line_fits(self):
        text = "기본 시나리오 26개월로 목표 12개월을 넘깁니다. 지금 구조를 유지해도 됩니다."
        for line in wrap(text, 30):
            self.assertLessEqual(display_width(line), 30)

    def test_indent_is_applied_and_counted(self):
        lines = wrap("가나다라마바사아자차카타파하 " * 4, 40, indent="    ")
        for line in lines:
            self.assertTrue(line.startswith("    "))
            self.assertLessEqual(display_width(line), 40)

    def test_long_unbroken_token_is_split(self):
        lines = wrap("가" * 60, 20)
        self.assertGreater(len(lines), 1)
        for line in lines:
            self.assertLessEqual(display_width(line), 20)

    def test_empty_text_yields_one_line(self):
        self.assertEqual(wrap("", 20), [""])


class MoneyTest(unittest.TestCase):
    def test_won_uses_thousand_separators(self):
        self.assertEqual(won(1234567), "1,234,567원")
        self.assertEqual(won(-5000), "-5,000원")

    def test_man_switches_unit_by_magnitude(self):
        self.assertEqual(man(60_000), "6.0만")
        self.assertEqual(man(5_200_000), "520만")
        self.assertEqual(man(150_000_000), "1.50억")

    def test_signed_man_marks_gains(self):
        self.assertTrue(signed_man(120_000).startswith("+"))
        self.assertTrue(signed_man(-120_000).startswith("-"))

    def test_bar_length_is_stable(self):
        self.assertEqual(len(bar(5, 10, width=10)), 10)
        self.assertEqual(bar(0, 10, width=4), "····")
        self.assertEqual(bar(20, 10, width=4), "████")
        self.assertEqual(bar(1, 0), "")


if __name__ == "__main__":
    unittest.main()
