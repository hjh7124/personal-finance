import datetime as dt
import unittest

from finance import checklist
from finance.config import ChecklistItem
from tests.helpers import make_config, make_profile

RESIGNED = dt.date(2026, 6, 30)


def item(offset, done=False, critical=False, item_id="x"):
    return ChecklistItem(id=item_id, name="항목", due_offset_days=offset, critical=critical, done=done)


def config_with(items, resignation_date=RESIGNED):
    cfg = make_config(profile=make_profile(resignation_date=resignation_date))
    cfg.checklist = items
    return cfg


class ChecklistTest(unittest.TestCase):
    def test_status_buckets(self):
        cfg = config_with([item(0, item_id="a"), item(20, item_id="b"), item(200, item_id="c"), item(5, done=True, item_id="d")])
        by_id = {s.item.id: s.status for s in checklist.evaluate(cfg, today=dt.date(2026, 7, 10))}
        self.assertEqual(by_id["a"], "overdue")
        self.assertEqual(by_id["b"], "soon")
        self.assertEqual(by_id["c"], "upcoming")
        self.assertEqual(by_id["d"], "done")

    def test_due_date_is_offset_from_resignation(self):
        cfg = config_with([item(14)])
        self.assertEqual(checklist.evaluate(cfg, today=RESIGNED)[0].due, dt.date(2026, 7, 14))

    def test_sorted_urgent_first(self):
        cfg = config_with([item(200, item_id="later"), item(-5, item_id="past")])
        order = [s.item.id for s in checklist.evaluate(cfg, today=RESIGNED)]
        self.assertEqual(order, ["past", "later"])

    def test_open_alerts_skips_done_and_distant(self):
        cfg = config_with([item(0, item_id="a"), item(300, item_id="b"), item(1, done=True, item_id="c")])
        alerts = checklist.open_alerts(cfg, today=dt.date(2026, 7, 10))
        self.assertEqual([a.item.id for a in alerts], ["a"])

    def test_no_resignation_date_means_no_deadlines(self):
        cfg = config_with([item(14)], resignation_date=None)
        self.assertEqual(checklist.evaluate(cfg), [])

    def test_when_text(self):
        cfg = config_with([item(10)])
        status = checklist.evaluate(cfg, today=dt.date(2026, 7, 10))[0]
        self.assertEqual(status.when(), "오늘")


if __name__ == "__main__":
    unittest.main()
