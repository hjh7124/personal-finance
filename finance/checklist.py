"""퇴사 후 행정 기한 추적.

돈을 버는 일이 아니라 '놓치면 사라지는 돈'을 지키는 일이다. 실업급여
수급기간 12개월, 건강보험 임의계속가입 신청기한처럼 한 번 지나가면
되돌릴 수 없는 것들이 있어서, 날짜를 사람 기억에 맡기지 않는다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from .config import ChecklistItem, Config

#: 이 일수 안으로 들어오면 '임박'으로 본다.
SOON_DAYS = 30

STATUS_ORDER = {"overdue": 0, "soon": 1, "upcoming": 2, "done": 3}
STATUS_LABELS = {
    "overdue": "기한 지남",
    "soon": "임박",
    "upcoming": "예정",
    "done": "완료",
}
STATUS_MARKS = {"overdue": "!", "soon": "▲", "upcoming": "·", "done": "✓"}


@dataclass(frozen=True)
class ChecklistStatus:
    item: ChecklistItem
    due: dt.date
    days_left: int
    status: str

    @property
    def is_open(self) -> bool:
        return self.status != "done"

    @property
    def needs_attention(self) -> bool:
        return self.status in ("overdue", "soon")

    def when(self) -> str:
        if self.status == "done":
            return "완료"
        if self.days_left < 0:
            return f"{-self.days_left}일 지남"
        if self.days_left == 0:
            return "오늘"
        return f"{self.days_left}일 남음"


def evaluate(cfg: Config, *, today: dt.date | None = None) -> list[ChecklistStatus]:
    if cfg.profile.resignation_date is None:
        # 기준일이 없으면 기한도 없다. 없는 날짜를 지어내는 것보다 비워두는 편이 낫다.
        return []
    now = today or dt.date.today()
    results: list[ChecklistStatus] = []
    for item in cfg.checklist:
        due = item.due_date(cfg.profile.resignation_date)
        days_left = (due - now).days
        if item.done:
            status = "done"
        elif days_left < 0:
            status = "overdue"
        elif days_left <= SOON_DAYS:
            status = "soon"
        else:
            status = "upcoming"
        results.append(ChecklistStatus(item=item, due=due, days_left=days_left, status=status))

    results.sort(key=lambda s: (STATUS_ORDER[s.status], s.due))
    return results


def open_alerts(cfg: Config, *, today: dt.date | None = None) -> list[ChecklistStatus]:
    """지금 당장 눈에 띄어야 하는 항목만."""
    return [s for s in evaluate(cfg, today=today) if s.needs_attention]
