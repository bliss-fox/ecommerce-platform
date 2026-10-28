from decimal import Decimal

import pytest

from xhs_seller.services.order_cost import CATEGORIES, allocate_by_quantity, calculate_order


def cost_map(values, *, estimated=()):
    return {
        name: (value, "estimated" if name in estimated else "actual")
        for name, value in zip(CATEGORIES, values, strict=True)
    }


@pytest.mark.parametrize(
    ("paid", "refund", "values", "net", "cost", "profit", "margin"),
    [
        (
            12000,
            0,
            [4000, 800, 0, 200, 600, 1000, 100],
            12000,
            6700,
            5300,
            Decimal(53) / Decimal(120),
        ),
        (10000, 2000, [3000, 600, 0, 100, 500, 800, 0], 8000, 5000, 3000, Decimal("0.375")),
        (8000, 8000, [0, 600, 600, 200, 0, 400, 0], 0, 1800, -1800, None),
        (
            6000,
            0,
            [1800, 600, 0, 100, 300, 400, 0],
            6000,
            3200,
            2800,
            Decimal(28) / Decimal(60),
        ),
    ],
)
def test_order_amounts(paid, refund, values, net, cost, profit, margin):
    result = calculate_order(paid, refund, cost_map(values))
    assert (
        result.net_sales_cents,
        result.variable_cost_cents,
        result.contribution_cents,
        result.margin,
    ) == (net, cost, profit, margin)


def test_unknown_and_estimated_cost():
    costs = cost_map([3000, 600, 0, 100, 450, None, 0])
    costs["marketing"] = (None, "unknown")
    result = calculate_order(9000, 0, costs)
    assert result.known_cost_cents == 4150
    assert result.missing == ("marketing",)
    assert result.contribution_cents is None
    assert result.status == "unknown"
    costs["marketing"] = (700, "estimated")
    result = calculate_order(9000, 0, costs)
    assert result.variable_cost_cents == 4850
    assert result.contribution_cents == 4150
    assert result.status == "estimated"


def test_stable_allocation_in_cents():
    assert allocate_by_quantity(10, {"OL-C": 1, "OL-A": 1, "OL-B": 1}) == {
        "OL-A": 4,
        "OL-B": 3,
        "OL-C": 3,
    }
    with pytest.raises(ValueError):
        allocate_by_quantity(10, {"OL-A": 0})


def test_settlement_refund_and_difference():
    from xhs_seller.services.order_cost import reconcile

    old = reconcile(10000, 2000, 0, 500, 0, 0, 9500)
    assert (old.expected_cents, old.difference_cents, old.refund_not_covered_cents) == (
        9500,
        0,
        2000,
    )
    current = reconcile(10000, 2000, 2000, 500, 0, 0, 7500)
    assert (
        current.expected_cents,
        current.actual_cents,
        current.difference_cents,
        current.refund_not_covered_cents,
    ) == (7500, 7500, 0, 0)
    discrepancy = reconcile(6000, 0, 0, 300, 0, 0, 5650)
    assert discrepancy.difference_cents == -50
    with pytest.raises(ValueError):
        reconcile(10000, 2000, 2001, 500, 0, 0, 7500)


def test_recovery_limits_and_unknown():
    from xhs_seller.services.order_cost import Adjustment, apply_adjustments

    recovery = Adjustment("recovery", -2400, "actual", 2)
    assert apply_adjustments("goods", 2400, "actual", 2, 2, [recovery]) == (0, "actual")
    assert apply_adjustments("goods", 2400, "actual", 2, 2, []) == (2400, "actual")
    result = calculate_order(8000, 8000, cost_map([2400, 600, 600, 200, 0, 400, 0]))
    assert (result.variable_cost_cents, result.contribution_cents) == (4200, -4200)
    assert apply_adjustments("goods", None, "unknown", 2, 2, []) == (None, "unknown")
    with pytest.raises(ValueError):
        apply_adjustments(
            "goods", 2400, "actual", 2, 2, [Adjustment("recovery", -2401, "actual", 2)]
        )
    with pytest.raises(ValueError):
        apply_adjustments(
            "goods", 2400, "actual", 2, 2, [Adjustment("recovery", -2400, "actual", 3)]
        )
    with pytest.raises(ValueError):
        apply_adjustments("packaging", 2400, "actual", 2, 2, [recovery])
    with pytest.raises(ValueError):
        apply_adjustments("goods", 2400, "actual", 2, 2, [recovery, recovery])
    assert apply_adjustments(
        "goods", 2400, "actual", 2, 2, [Adjustment("correction", 100, "estimated", None)]
    ) == (2500, "estimated")

    # Half a cent rounds up, including when large values exceed Decimal's default precision.
    assert apply_adjustments(
        "goods", 1, "actual", 2, 1, [Adjustment("recovery", -1, "actual", 1)]
    ) == (0, "actual")
    large = 9_223_372_036_854_775_807
    assert apply_adjustments(
        "goods",
        large,
        "actual",
        large,
        large - 1,
        [Adjustment("recovery", -(large - 1), "actual", large - 1)],
    ) == (1, "actual")


def test_p4_synthetic_baseline_amounts():
    import csv
    from pathlib import Path

    from xhs_seller.services.economics import money_to_cents
    from xhs_seller.services.order_cost import Adjustment, apply_adjustments, reconcile

    root = Path(__file__).resolve().parents[1] / "examples" / "p4"

    def rows(name):
        with (root / name).open(encoding="utf-8-sig", newline="") as stream:
            return list(csv.DictReader(stream))

    orders = rows("orders.csv")
    expenses = rows("expenses.csv")
    allocations = rows("expense_allocations.csv")
    adjustments = rows("cost_adjustments.csv")
    settlements = {row["order_line_id"]: row for row in rows("settlements.csv")}
    results = {}
    reconciliations = {}
    for order in orders:
        order_id = order["order_line_id"]
        costs = {}
        for expense in expenses:
            if expense["allocation_mode"] == "direct" and expense["order_line_id"] == order_id:
                base = money_to_cents(expense["amount"]) if expense["amount"] else None
            elif expense["allocation_mode"] == "pool":
                allocation = next(
                    (
                        a
                        for a in allocations
                        if a["expense_id"] == expense["expense_id"]
                        and a["order_line_id"] == order_id
                    ),
                    None,
                )
                if allocation is None:
                    continue
                base = money_to_cents(allocation["amount"])
            else:
                continue
            changes = []
            for row in adjustments:
                if row["expense_id"] == expense["expense_id"] and row["order_line_id"] == order_id:
                    sign = -1 if row["amount"].startswith("-") else 1
                    changes.append(
                        Adjustment(
                            row["kind"],
                            sign * money_to_cents(row["amount"].lstrip("-")),
                            row["basis"],
                            int(row["recovered_units"]) if row["recovered_units"] else None,
                        )
                    )
            costs[expense["category"]] = apply_adjustments(
                expense["category"],
                base,
                expense["basis"],
                int(order["quantity"]),
                int(order["returned_units"]),
                changes,
            )
        paid, refunded = (
            money_to_cents(order["paid_amount"]),
            money_to_cents(order["refunded_amount"]),
        )
        results[order_id] = calculate_order(paid, refunded, costs)
        if order_id in settlements:
            row = settlements[order_id]
            reconciliations[order_id] = reconcile(
                paid,
                refunded,
                money_to_cents(row["covered_refunded_amount"]),
                money_to_cents(row["platform_fee_deducted"]),
                money_to_cents(row["other_withheld"]),
                money_to_cents(row["settlement_adjustments"]),
                money_to_cents(row["actual_received"]),
            )
    assert [results[k].contribution_cents for k in sorted(results)] == [
        5300,
        3000,
        -1800,
        None,
        2800,
    ]
    assert results["OL-04"].known_cost_cents == 4150
    assert sum(r.net_sales_cents for r in results.values()) == 35000
    assert (
        sum(r.contribution_cents for r in results.values() if r.contribution_cents is not None)
        == 9300
    )
    assert sum(r.actual_cents for r in reconciliations.values()) == 26550
    assert sum(r.refund_not_covered_cents for r in reconciliations.values()) == 2000
    assert sum(r.difference_cents for r in reconciliations.values()) == -50
