"""터미널 리포트 렌더링.

한글은 한 글자가 두 칸을 차지한다. len()으로 자리를 맞추면 표가 어긋나므로
동아시아 문자폭을 계산해서 채운다.
"""

from __future__ import annotations

import datetime as dt
import unicodedata

from . import checklist as checklist_mod
from .config import LIQUIDITY_LABELS, Config
from .ledger import (
    BurnRate,
    ExpenseRow,
    IncomeRow,
    Snapshot,
    category_totals,
    monthly_expense_totals,
    monthly_income_totals,
    source_totals,
)
from .money import bar, man, signed_man, won
from .months import Month
from .runway import RunwayResult, liquid_balance, required_spend_for

WIDTH = 76


# ── 폭 계산 ─────────────────────────────────────────────────

def display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def pad(text: str, width: int, align: str = "left") -> str:
    gap = max(0, width - display_width(text))
    if align == "right":
        return " " * gap + text
    if align == "center":
        left = gap // 2
        return " " * left + text + " " * (gap - left)
    return text + " " * gap


def wrap(text: str, width: int, indent: str = "") -> list[str]:
    """문자폭 기준 줄바꿈. 터미널 너비를 넘기면 표 전체가 무너진다."""
    limit = max(10, width - display_width(indent))
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if display_width(candidate) <= limit:
            current = candidate
            continue
        if current:
            lines.append(current)
        while display_width(word) > limit:
            head = ""
            for ch in word:
                if display_width(head + ch) > limit:
                    break
                head += ch
            lines.append(head)
            word = word[len(head):]
        current = word
    if current:
        lines.append(current)
    return [indent + line for line in lines] or [indent]


def truncate(text: str, width: int) -> str:
    if display_width(text) <= width:
        return text
    out = ""
    for ch in text:
        if display_width(out + ch) > width - 1:
            break
        out += ch
    return out + "…"


# ── 틀 ─────────────────────────────────────────────────────

def title(text: str) -> str:
    return f"\n{text}\n{'─' * WIDTH}"


def header(text: str, subtitle: str = "") -> str:
    lines = ["", "━" * WIDTH, f"  {text}"]
    if subtitle:
        lines.append(f"  {subtitle}")
    lines.append("━" * WIDTH)
    return "\n".join(lines)


# ── 자산 현황 ───────────────────────────────────────────────

def render_assets(cfg: Config, snapshot: Snapshot) -> str:
    lines = [title(f"자산 현황  ({snapshot.month.label()} 말 기준)")]
    lines.append(
        f"  {pad('계좌', 26)}{pad('유동성', 10)}{pad('잔액', 14, 'right')}  비고"
    )
    for acc in cfg.accounts:
        amount = snapshot.balances.get(acc.id, 0)
        if amount == 0 and acc.id not in snapshot.balances:
            continue
        shown = -amount if acc.is_debt else amount
        lines.append(
            f"  {pad(truncate(acc.name, 25), 26)}"
            f"{pad(LIQUIDITY_LABELS[acc.liquidity], 10)}"
            f"{pad(man(shown), 14, 'right')}  {truncate(acc.note, 20)}"
        )

    primary = snapshot.total(cfg.accounts, tier="primary")
    secondary = snapshot.total(cfg.accounts, tier="secondary")
    debt = snapshot.debt_total(cfg.accounts)
    lines.append("  " + "─" * (WIDTH - 2))
    lines.append(f"  {pad('순자산', 36)}{pad(won(snapshot.net_worth(cfg.accounts)), 20, 'right')}")
    lines.append(f"  {pad('유동자산 1차 (즉시·1개월 내)', 36)}{pad(won(primary), 20, 'right')}")
    lines.append(f"  {pad('유동자산 2차 (투자자산 포함)', 36)}{pad(won(secondary), 20, 'right')}")
    if debt:
        lines.append(f"  {pad('부채', 36)}{pad(won(-debt), 20, 'right')}")
    lines.append(
        f"  {pad('비상금 (런웨이 계산에서 제외)', 36)}"
        f"{pad(won(-cfg.profile.emergency_reserve), 20, 'right')}"
    )
    lines.append(
        f"  {pad('→ 실제 쓸 수 있는 돈 (1차)', 36)}"
        f"{pad(won(primary - cfg.profile.emergency_reserve), 20, 'right')}"
    )
    return "\n".join(lines)


# ── 지출 ───────────────────────────────────────────────────

def render_burn(burn: BurnRate, cfg: Config) -> str:
    lines = [title("소진 속도")]
    if burn.is_estimated:
        lines.append("  기록된 지출이 없습니다. expenses.csv 를 채우면 여기가 실제 값으로 바뀝니다.")
        return "\n".join(lines)

    window = ", ".join(str(m) for m in burn.months_used)
    lines.append(f"  기준: {window} 평균 (비정기 지출 제외)")
    lines.append("")
    lines.append(f"  {pad('고정비', 18)}{pad(won(burn.fixed), 16, 'right')}   {bar(burn.fixed, burn.monthly)}")
    lines.append(f"  {pad('변동비', 18)}{pad(won(burn.variable), 16, 'right')}   {bar(burn.variable, burn.monthly)}")
    lines.append(f"  {pad('월 소진액', 18)}{pad(won(burn.monthly), 16, 'right')}")
    lines.append(f"  {pad('연 환산', 18)}{pad(won(burn.monthly * 12), 16, 'right')}")
    return "\n".join(lines)


# ── 런웨이 ─────────────────────────────────────────────────

def render_runway_table(cfg: Config, results: list[RunwayResult]) -> str:
    target = cfg.profile.target_runway_months
    lines = [title(f"런웨이  (목표 {target}개월)")]
    lines.append(f"  {pad('시나리오', 30)}{pad('월 지출', 12, 'right')}{pad('버티는 기간', 14, 'right')}   소진 예상")
    for result in results:
        if result.is_sustainable:
            months_text = "소진 안 됨"
            depletion = "—"
            mark = "○"
        else:
            months_text = f"{result.months_survived}개월"
            depletion = result.depletion_month.label()
            mark = "○" if result.months_survived >= target else "▲"
        lines.append(
            f"{mark} {pad(truncate(result.scenario.name, 29), 30)}"
            f"{pad(man(result.monthly_spend), 12, 'right')}"
            f"{pad(months_text, 14, 'right')}   {depletion}"
        )
    lines.append("")
    lines.append("  ○ 목표 달성   ▲ 목표 미달")
    return "\n".join(lines)


def render_runway_detail(cfg: Config, result: RunwayResult, *, max_rows: int = 30) -> str:
    lines = [title(f"월별 흐름 — {result.scenario.name}")]
    lines.append(
        f"  {pad('월', 10)}{pad('시작잔액', 12, 'right')}{pad('수입', 11, 'right')}"
        f"{pad('지출', 11, 'right')}{pad('예정지출', 11, 'right')}{pad('월말잔액', 12, 'right')}"
    )
    for row in result.rows[:max_rows]:
        lines.append(
            f"  {pad(str(row.month), 10)}"
            f"{pad(man(row.opening), 12, 'right')}"
            f"{pad(man(row.income) if row.income else '—', 11, 'right')}"
            f"{pad('-' + man(row.spend), 11, 'right')}"
            f"{pad('-' + man(row.scheduled) if row.scheduled else '—', 11, 'right')}"
            f"{pad(man(row.closing), 12, 'right')}"
        )
    if len(result.rows) > max_rows:
        lines.append(f"  … 이하 {len(result.rows) - max_rows}개월 생략")
    lines.append("")
    lines.append(f"  결론: {result.summary()}")
    return "\n".join(lines)


def _bullet(text: str, marker: str = "·") -> list[str]:
    """번진 문장을 터미널 폭에 맞춰 접는다. 이어지는 줄은 들여쓴다."""
    body = wrap(text, WIDTH - 6)
    return [f"  {marker} {body[0]}"] + [f"    {line}" for line in body[1:]]


def render_diagnosis(cfg: Config, snapshot: Snapshot, burn: BurnRate, results: list[RunwayResult]) -> str:
    """숫자를 보고 무엇을 해야 하는지까지 말해주는 부분."""
    target = cfg.profile.target_runway_months
    lines = [title("판단")]

    base = next((r for r in results if r.scenario.id == "base"), results[0])
    worst = next((r for r in results if r.scenario.id == "worst"), None)

    if base.is_sustainable:
        lines += _bullet("기본 시나리오에서는 유동자산이 바닥나지 않습니다. 수입이 지출을 덮고 있습니다.")
    elif base.months_survived >= target:
        lines += _bullet(
            f"기본 시나리오 {base.months_survived}개월로 목표 {target}개월을 넘깁니다. "
            "지금 구조를 유지해도 됩니다."
        )
    else:
        gap = target - base.months_survived
        lines += _bullet(f"기본 시나리오 {base.months_survived}개월로 목표에 {gap}개월 모자랍니다.")
        needed = required_spend_for(cfg, snapshot, base.scenario, burn, target)
        if needed is not None:
            cut = base.monthly_spend - needed
            lines += _bullet(
                f"목표를 채우려면 월 지출을 {won(needed)} 이하로, 지금보다 {won(cut)} 줄여야 합니다.",
                marker=" ",
            )
        else:
            lines += _bullet(
                "지출을 줄이는 것만으로는 목표에 닿지 않습니다. 수입 쪽을 봐야 합니다.", marker=" "
            )

    if worst and not worst.is_sustainable and worst.months_survived < target:
        lines += _bullet(
            f"최악 시나리오(실업급여 없음)에서는 {worst.months_survived}개월입니다. "
            f"{worst.depletion_month.label()}이 실질적인 마감기한입니다."
        )

    locked = [
        a
        for a in cfg.accounts
        if a.liquidity == "locked" and not a.is_debt and snapshot.balances.get(a.id)
    ]
    if locked:
        total_locked = sum(snapshot.balances.get(a.id, 0) for a in locked)
        lines += _bullet(
            f"인출 불가 자산이 {won(total_locked)} 있습니다({', '.join(a.name for a in locked)}). "
            "순자산은 커 보여도 이 돈은 생활비가 아닙니다."
        )

    if burn.fixed and burn.monthly:
        ratio = burn.fixed / burn.monthly * 100
        if ratio >= 45:
            lines += _bullet(
                f"지출의 {ratio:.0f}%가 고정비입니다. 변동비를 조여도 효과가 제한적이니 "
                "고정비 자체(보험·통신·주거·대출)를 먼저 봐야 합니다."
            )
    return "\n".join(lines)


# ── 월간 리포트 ─────────────────────────────────────────────

def render_month_income(income: list[IncomeRow], month: Month) -> str:
    """그 달에 실제로 들어온 돈."""
    sources = source_totals(income, month)
    lines = [title(f"{month.label()} 수입")]
    if not sources:
        lines.append("  이 달의 수입 기록이 없습니다.")
        return "\n".join(lines)

    biggest = max(sources.values())
    for name, amount in sources.items():
        lines.append(
            f"  {pad(truncate(name, 18), 18)}{pad(won(amount), 14, 'right')}  {bar(amount, biggest, 18)}"
        )
    lines.append("  " + "─" * (WIDTH - 2))
    lines.append(f"  {pad('합계', 18)}{pad(won(sum(sources.values())), 14, 'right')}")
    return "\n".join(lines)


def render_net_cashflow(
    expenses: list[ExpenseRow], income: list[IncomeRow], month: Month
) -> str:
    """그 달의 순현금흐름. 소득 공백기에는 거의 항상 음수이고, 그 크기가 곧 소진 속도다."""
    spent = monthly_expense_totals(expenses).get(month, {}).get("total", 0)
    earned = monthly_income_totals(income).get(month, 0)
    net = earned - spent
    lines = [title(f"{month.label()} 순현금흐름")]
    lines.append(f"  {pad('수입', 18)}{pad(won(earned), 16, 'right')}")
    lines.append(f"  {pad('지출', 18)}{pad(won(-spent), 16, 'right')}")
    lines.append("  " + "─" * (WIDTH - 2))
    lines.append(f"  {pad('순현금흐름', 18)}{pad(signed_man(net) + '원', 16, 'right')}")
    if net < 0:
        lines += _bullet(f"이 달 자산이 {won(-net)} 줄었습니다. 이 속도가 그대로 런웨이가 됩니다.")
    else:
        lines += _bullet("이 달은 수입이 지출을 덮었습니다. 런웨이가 줄지 않았습니다.")
    return "\n".join(lines)


def render_month(cfg: Config, expenses: list[ExpenseRow], month: Month) -> str:
    totals = monthly_expense_totals(expenses)
    this_month = totals.get(month)
    lines = [title(f"{month.label()} 지출")]
    if not this_month:
        lines.append("  이 달의 지출 기록이 없습니다.")
        return "\n".join(lines)

    previous = totals.get(month.plus(-1), {})
    categories = category_totals(expenses, month)
    biggest = max(categories.values()) if categories else 0

    for name, amount in categories.items():
        lines.append(
            f"  {pad(truncate(name, 18), 18)}{pad(won(amount), 14, 'right')}  {bar(amount, biggest, 18)}"
        )

    lines.append("  " + "─" * (WIDTH - 2))
    for kind, label in (("fixed", "고정비"), ("variable", "변동비"), ("irregular", "비정기")):
        if kind in this_month:
            lines.append(f"  {pad(label, 18)}{pad(won(this_month[kind]), 14, 'right')}")
    total = this_month.get("total", 0)
    lines.append(f"  {pad('합계', 18)}{pad(won(total), 14, 'right')}")

    if previous:
        delta = total - previous.get("total", 0)
        lines.append(
            f"  {pad('전월 대비', 18)}{pad(signed_man(delta) + '원', 14, 'right')}"
            f"   ({month.plus(-1)}: {won(previous.get('total', 0))})"
        )
    return "\n".join(lines)


# ── 체크리스트 ──────────────────────────────────────────────

def render_checklist(
    cfg: Config,
    statuses: list[checklist_mod.ChecklistStatus],
    *,
    show_done: bool = False,
    show_notes: bool = False,
) -> str:
    if cfg.profile.resignation_date is None:
        return "\n".join(
            [title("퇴사 후 행정")]
            + _bullet("profile.yaml 의 resignation_date 에 퇴사일을 넣으면 기한이 계산됩니다.")
        )

    lines = [title(f"퇴사 후 행정  (퇴사일 {cfg.profile.resignation_date})")]
    shown = [s for s in statuses if show_done or s.is_open]
    if not shown:
        lines.append("  남은 항목이 없습니다.")
        return "\n".join(lines)

    for status in shown:
        mark = checklist_mod.STATUS_MARKS[status.status]
        critical = " [중요]" if status.item.critical and status.is_open else ""
        lines.append(
            f"  {mark} {pad(truncate(status.item.name, 37), 39)}"
            f"{pad(str(status.due), 12)}{pad(status.when(), 12, 'right')}{critical}"
        )
        if show_notes and status.item.note:
            if status.item.where:
                lines.append(f"      창구: {status.item.where}")
            lines.extend(wrap(status.item.note, WIDTH, indent="      "))
    lines.append("")
    lines.append("  ! 기한 지남   ▲ 임박   · 예정   ✓ 완료")
    lines.append("  ※ 제도·기한은 바뀝니다. 신청 전 공단/고용센터에서 반드시 확인하세요.")
    return "\n".join(lines)


def render_warnings(cfg: Config, issues: list[str]) -> str:
    if not cfg.warnings and not issues:
        return ""
    lines = [title("확인 필요")]
    for text in cfg.warnings + issues:
        lines += _bullet(text)
    return "\n".join(lines)


def render_alerts(statuses: list[checklist_mod.ChecklistStatus]) -> str:
    urgent = [s for s in statuses if s.needs_attention]
    if not urgent:
        return ""
    lines = [title("기한 알림")]
    for status in urgent[:5]:
        mark = checklist_mod.STATUS_MARKS[status.status]
        lines.append(f"  {mark} {pad(truncate(status.item.name, 39), 41)}{status.when()} ({status.due})")
    if len(urgent) > 5:
        lines.append(f"  … 외 {len(urgent) - 5}건. 전체는 `fin checklist`")
    return "\n".join(lines)
