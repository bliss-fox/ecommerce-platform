import re
from dataclasses import dataclass
from decimal import Decimal

MONEY_PATTERN = re.compile(r"(?:0|[0-9]+)(?:\.[0-9]{1,2})?\Z")


def money_to_cents(value: str) -> int:
    if not isinstance(value, str) or not MONEY_PATTERN.fullmatch(value):
        raise ValueError("金额必须为非负十进制字符串，最多两位小数")
    amount = Decimal(value)
    if amount > Decimal("92233720368547758.07"):
        raise ValueError("金额超出 SQLite 整数分范围")
    return int(amount * 100)


@dataclass(frozen=True)
class Economics:
    known_cost: Decimal
    missing: tuple[str, ...]
    contribution: Decimal | None
    margin: Decimal | None
    break_even_cac: Decimal | None


def calculate(sale_price_cents: int, costs: dict[str, int | None]) -> Economics:
    required = {"unit_cost", "shipping", "packaging", "platform", "marketing", "after_sales"}
    if set(costs) != required:
        raise ValueError("必须提供全部六项费用字段")
    if type(sale_price_cents) is not int or sale_price_cents <= 0:
        raise ValueError("售价必须为正整数分")
    if any(v is not None and (type(v) is not int or v < 0) for v in costs.values()):
        raise ValueError("成本必须为非负整数分或未知")
    missing = tuple(name for name, value in costs.items() if value is None)
    known = sum(value for value in costs.values() if value is not None)
    non_marketing = [value for name, value in costs.items() if name != "marketing"]
    break_even = (
        Decimal(sale_price_cents - sum(non_marketing)) / 100
        if all(value is not None for value in non_marketing)
        else None
    )
    contribution = Decimal(sale_price_cents - known) / 100 if not missing else None
    margin = contribution / (Decimal(sale_price_cents) / 100) if contribution is not None else None
    return Economics(Decimal(known) / 100, missing, contribution, margin, break_even)
