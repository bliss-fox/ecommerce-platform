"""Pure order-line contribution calculations; amounts are integer CNY cents."""

from dataclasses import dataclass
from decimal import Decimal

CATEGORIES = (
    "goods",
    "outbound_shipping",
    "return_shipping",
    "packaging",
    "platform_fee",
    "marketing",
    "other",
)


@dataclass(frozen=True)
class OrderResult:
    net_sales_cents: int
    known_cost_cents: int
    variable_cost_cents: int | None
    contribution_cents: int | None
    margin: Decimal | None
    missing: tuple[str, ...]
    status: str


def calculate_order(paid_cents, refunded_cents, costs):
    if (
        type(paid_cents) is not int
        or type(refunded_cents) is not int
        or not 0 <= refunded_cents <= paid_cents
    ):
        raise ValueError("支付和退款金额无效")
    if set(costs) != set(CATEGORIES):
        raise ValueError("必须提供七类成本，未归属的类别须标为未知")
    for amount, basis in costs.values():
        if basis not in {"actual", "estimated", "unknown"}:
            raise ValueError("成本依据无效")
        if basis == "unknown":
            if amount is not None:
                raise ValueError("未知成本金额必须为空")
        elif type(amount) is not int or amount < 0:
            raise ValueError("已知成本必须为非负整数分")
    missing = tuple(name for name in CATEGORIES if costs[name][0] is None)
    known = sum(amount for amount, _ in costs.values() if amount is not None)
    net = paid_cents - refunded_cents
    variable = None if missing else known
    contribution = None if missing else net - known
    margin = Decimal(contribution) / Decimal(net) if contribution is not None and net else None
    status = (
        "unknown"
        if missing
        else "estimated"
        if any(basis == "estimated" for _, basis in costs.values())
        else "actual"
    )
    return OrderResult(net, known, variable, contribution, margin, missing, status)


def allocate_by_quantity(amount_cents, quantities):
    if type(amount_cents) is not int or amount_cents < 0 or not quantities:
        raise ValueError("分摊金额或订单为空")
    if any(type(quantity) is not int or quantity < 1 for quantity in quantities.values()):
        raise ValueError("分摊数量必须为正整数")
    total = sum(quantities.values())
    parts = {key: divmod(amount_cents * quantity, total) for key, quantity in quantities.items()}
    remainder = amount_cents - sum(part[0] for part in parts.values())
    ranked = sorted(parts, key=lambda key: (-parts[key][1], key))
    extra = set(ranked[:remainder])
    return {key: parts[key][0] + (key in extra) for key in sorted(parts)}


@dataclass(frozen=True)
class Reconciliation:
    expected_cents: int
    actual_cents: int
    difference_cents: int
    refund_not_covered_cents: int


def reconcile(
    paid_cents,
    refunded_cents,
    covered_refund_cents,
    platform_cents,
    withheld_cents,
    adjustments_cents,
    received_cents,
):
    values = (
        paid_cents,
        refunded_cents,
        covered_refund_cents,
        platform_cents,
        withheld_cents,
        adjustments_cents,
        received_cents,
    )
    if any(type(value) is not int for value in values):
        raise ValueError("结算金额必须为整数分")
    if not 0 <= covered_refund_cents <= refunded_cents <= paid_cents:
        raise ValueError("结算覆盖退款超出订单累计退款")
    if platform_cents < 0 or withheld_cents < 0:
        raise ValueError("现金扣款必须非负")
    expected = (
        paid_cents - covered_refund_cents - platform_cents - withheld_cents + adjustments_cents
    )
    return Reconciliation(
        expected, received_cents, received_cents - expected, refunded_cents - covered_refund_cents
    )


@dataclass(frozen=True)
class Adjustment:
    kind: str
    amount_cents: int
    basis: str
    recovered_units: int | None


def apply_adjustments(category, base_cents, base_basis, quantity, returned_units, adjustments):
    """Apply independent current adjustment snapshots to one assigned cost source."""
    if category not in CATEGORIES:
        raise ValueError("订单变量费用类别无效")
    if (
        type(quantity) is not int
        or type(returned_units) is not int
        or not 0 <= returned_units <= quantity
        or quantity < 1
    ):
        raise ValueError("订单数量或退回数量无效")
    if base_basis not in {"actual", "estimated", "unknown"}:
        raise ValueError("成本依据无效")
    if base_basis == "unknown":
        if base_cents is not None:
            raise ValueError("未知成本金额必须为空")
    elif type(base_cents) is not int or base_cents < 0:
        raise ValueError("基础成本必须为非负整数分")
    recovered_units = 0
    recovered_amount = 0
    delta = 0
    estimated = base_basis == "estimated"
    for adjustment in adjustments:
        if type(adjustment.amount_cents) is not int or adjustment.basis not in {
            "actual",
            "estimated",
        }:
            raise ValueError("调整金额或依据无效")
        estimated = estimated or adjustment.basis == "estimated"
        if adjustment.kind == "recovery":
            if (
                category != "goods"
                or adjustment.amount_cents > 0
                or type(adjustment.recovered_units) is not int
                or adjustment.recovered_units < 1
            ):
                raise ValueError("回收仅适用于商品且必须注明退回件数")
            if base_cents is None:
                raise ValueError("未知商品成本无法验证回收上限")
            # Integer arithmetic keeps ROUND_HALF_UP exact even for large amounts.
            numerator = base_cents * adjustment.recovered_units
            limit = (2 * numerator + quantity) // (2 * quantity)
            if -adjustment.amount_cents > limit:
                raise ValueError("单次回收超过采购成本上限")
            recovered_units += adjustment.recovered_units
            recovered_amount -= adjustment.amount_cents
        elif adjustment.kind == "correction":
            if adjustment.recovered_units is not None:
                raise ValueError("更正不可指定回收件数")
        else:
            raise ValueError("调整类型无效")
        delta += adjustment.amount_cents
    if recovered_units > returned_units or (
        base_cents is not None and recovered_amount > base_cents
    ):
        raise ValueError("累计回收超过退回数量或原成本")
    if base_cents is None:
        return None, "unknown"
    if base_cents + delta < 0:
        raise ValueError("调整后成本不能为负")
    return base_cents + delta, "estimated" if estimated else "actual"
