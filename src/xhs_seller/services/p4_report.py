"""Read-only P4 report from current validated source snapshots."""

from collections import defaultdict
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import (
    CostAdjustment,
    Expense,
    ExpenseAllocation,
    OrderLine,
    SettlementSnapshot,
)
from xhs_seller.services.order_cost import (
    CATEGORIES,
    Adjustment,
    apply_adjustments,
    calculate_order,
    reconcile,
)


def summarize_skus(rows):
    by_sku = defaultdict(list)
    for row in rows:
        by_sku[row["sku_id"]].append(row)
    summaries = []
    for sku_id, sku_rows in sorted(by_sku.items()):
        costs = [row["cost"] for row in sku_rows]
        actual = [cost for cost in costs if cost.status == "actual"]
        estimated = [cost for cost in costs if cost.status == "estimated"]
        unknown = [cost for cost in costs if cost.status == "unknown"]
        summaries.append(
            {
                "sku_id": sku_id,
                "order_line_ids": [row["order_line_id"] for row in sku_rows],
                "net_sales_cents": sum(cost.net_sales_cents for cost in costs),
                "status": "unknown" if unknown else "estimated" if estimated else "actual",
                "variable_cost_cents": None
                if unknown
                else sum(cost.variable_cost_cents for cost in costs),
                "contribution_cents": None
                if unknown
                else sum(cost.contribution_cents for cost in costs),
                "actual_count": len(actual),
                "actual_contribution_cents": sum(cost.contribution_cents for cost in actual)
                if actual
                else None,
                "estimated_count": len(estimated),
                "estimated_contribution_cents": sum(cost.contribution_cents for cost in estimated)
                if estimated
                else None,
                "unknown_count": len(unknown),
            }
        )
    return summaries


def build_report(engine, as_of):
    moment = datetime.fromisoformat(as_of)
    if moment.utcoffset() is None:
        raise ValueError("报告时间必须包含时区")
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        orders = session.exec(select(OrderLine)).all()
        expenses = session.exec(select(Expense)).all()
        allocations = session.exec(select(ExpenseAllocation)).all()
        adjustments = session.exec(select(CostAdjustment)).all()
        settlements = session.exec(select(SettlementSnapshot)).all()
        records = [*orders, *expenses, *allocations, *adjustments, *settlements]
        if any(datetime.fromisoformat(row.updated_at) > moment for row in records):
            raise ValueError("当前数据包含报告时间之后的修订，不能回放历史快照")
        event_times = [o.paid_at for o in orders] + [
            o.refund_updated_at for o in orders if o.refund_updated_at
        ]
        event_times += [a.occurred_at for a in adjustments] + [s.cutoff_at for s in settlements]
        if any(datetime.fromisoformat(value) > moment for value in event_times):
            raise ValueError("当前数据包含报告时间之后的业务事件")
        local_date = moment.astimezone(ZoneInfo("Asia/Shanghai")).date()
        if any(datetime.fromisoformat(e.expense_date).date() > local_date for e in expenses):
            raise ValueError("当前数据包含报告日期之后的费用")
        expense_by_id = {e.expense_id: e for e in expenses}
        settlement_by_order = {s.order_line_id: s for s in settlements}
        sources_by_order = defaultdict(dict)
        for expense in expenses:
            if expense.allocation_mode == "direct":
                sources_by_order[expense.order_line_id][expense.category] = (
                    expense,
                    expense.amount_cents,
                    None,
                )
        for allocation in allocations:
            expense = expense_by_id[allocation.expense_id]
            sources_by_order[allocation.order_line_id][expense.category] = (
                expense,
                allocation.amount_cents,
                allocation,
            )
        adjustments_by_source = defaultdict(list)
        for adjustment in adjustments:
            adjustments_by_source[(adjustment.expense_id, adjustment.order_line_id)].append(
                adjustment
            )
        result_rows = []
        for order in sorted(orders, key=lambda row: row.order_line_id):
            sources = sources_by_order.get(order.order_line_id, {})
            costs = {}
            revisions = {
                "order": {order.order_line_id: order.revision},
                "expense": {},
                "allocation": {},
                "adjustment": {},
                "settlement": {},
            }
            for category in CATEGORIES:
                if category not in sources:
                    costs[category] = (None, "unknown")
                    continue
                expense, amount, allocation = sources[category]
                revisions["expense"][expense.expense_id] = expense.revision
                if allocation is not None:
                    revisions["allocation"][allocation.allocation_id] = allocation.revision
                relevant = adjustments_by_source.get((expense.expense_id, order.order_line_id), [])
                for adjustment in relevant:
                    revisions["adjustment"][adjustment.adjustment_id] = adjustment.revision
                costs[category] = apply_adjustments(
                    category,
                    amount,
                    expense.basis,
                    order.quantity,
                    order.returned_units,
                    [
                        Adjustment(a.kind, a.amount_cents, a.basis, a.recovered_units)
                        for a in relevant
                    ],
                )
            cost = calculate_order(order.paid_amount_cents, order.refunded_amount_cents, costs)
            settlement = settlement_by_order.get(order.order_line_id)
            reconciliation = None
            if settlement is not None:
                revisions["settlement"][settlement.settlement_id] = settlement.revision
                reconciliation = reconcile(
                    order.paid_amount_cents,
                    order.refunded_amount_cents,
                    settlement.covered_refunded_amount_cents,
                    settlement.platform_fee_deducted_cents,
                    settlement.other_withheld_cents,
                    settlement.settlement_adjustments_cents,
                    settlement.actual_received_cents,
                )
            result_rows.append(
                {
                    "order_line_id": order.order_line_id,
                    "sku_id": order.sku_id,
                    "cost": cost,
                    "categories": costs,
                    "settlement": reconciliation,
                    "revisions": revisions,
                }
            )
        unallocated = [e for e in expenses if e.allocation_mode == "unallocated"]
        overhead = [e for e in expenses if e.allocation_mode == "overhead"]
        subtotals = {}
        for status in ("actual", "estimated"):
            costs = [row["cost"] for row in result_rows if row["cost"].status == status]
            subtotals[status] = {
                "count": len(costs),
                "net_sales_cents": sum(cost.net_sales_cents for cost in costs),
                "variable_cost_cents": sum(cost.variable_cost_cents for cost in costs),
                "contribution_cents": sum(cost.contribution_cents for cost in costs),
            }
        settled = [row["settlement"] for row in result_rows if row["settlement"] is not None]
        settlement_summary = (
            {
                "recorded_count": len(settled),
                "missing_count": len(result_rows) - len(settled),
                "actual_cents": sum(item.actual_cents for item in settled),
                "refund_not_covered_cents": sum(item.refund_not_covered_cents for item in settled),
                "difference_cents": sum(item.difference_cents for item in settled),
                "difference_count": sum(item.difference_cents != 0 for item in settled),
                "positive_difference_cents": sum(
                    item.difference_cents for item in settled if item.difference_cents > 0
                ),
                "negative_difference_cents": sum(
                    item.difference_cents for item in settled if item.difference_cents < 0
                ),
            }
            if settled
            else None
        )
        return {
            "as_of": moment.isoformat(),
            "rows": result_rows,
            "cost_subtotals": subtotals,
            "sku_summaries": summarize_skus(result_rows),
            "settlement_summary": settlement_summary,
            "assigned": [
                {
                    "id": e.expense_id,
                    "revision": e.revision,
                    "category": e.category,
                    "amount_cents": e.amount_cents,
                    "basis": e.basis,
                    "allocation_mode": e.allocation_mode,
                }
                for e in expenses
                if e.allocation_mode in {"direct", "pool"}
            ],
            "unallocated": [
                {
                    "id": e.expense_id,
                    "revision": e.revision,
                    "category": e.category,
                    "amount_cents": e.amount_cents,
                    "basis": e.basis,
                }
                for e in unallocated
            ],
            "overhead": [
                {
                    "id": e.expense_id,
                    "revision": e.revision,
                    "amount_cents": e.amount_cents,
                    "basis": e.basis,
                }
                for e in overhead
            ],
        }
