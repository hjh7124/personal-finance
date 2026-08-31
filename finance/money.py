"""금액 표기.

원 단위 숫자는 자릿수가 많아 눈으로 비교가 안 된다. 표에서는 만원 단위로
줄여 쓰고, 단독으로 강조할 때만 원 단위 전체를 쓴다.
"""

from __future__ import annotations


def won(amount: float) -> str:
    """1,234,567원"""
    return f"{round(amount):,}원"


def man(amount: float) -> str:
    """만원 단위로 축약. 표 안에서 자릿수를 맞추기 위한 표기."""
    value = amount / 10_000
    if abs(value) >= 10_000:
        return f"{value / 10_000:,.2f}억"
    if abs(value) >= 100:
        return f"{value:,.0f}만"
    return f"{value:,.1f}만"


def signed_man(amount: float) -> str:
    sign = "+" if amount > 0 else ""
    return f"{sign}{man(amount)}"


def bar(value: float, maximum: float, width: int = 24, fill: str = "█") -> str:
    """가로 막대. 비율을 눈으로 잡기 위한 것이므로 정밀도는 필요 없다."""
    if maximum <= 0:
        return ""
    filled = round(width * max(0.0, min(1.0, value / maximum)))
    return fill * filled + "·" * (width - filled)
