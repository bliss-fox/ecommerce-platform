"""Validate the complete proposed P4 ledger before any writes."""

from collections import defaultdict

from xhs_seller.services.order_cost import CATEGORIES, Adjustment, apply_adjustments


def validate_p4(candidate, preview, issue):
    orders = candidate["order"]
    expenses = candidate["expense"]
    allocations = candidate["allocation"]
    adjustments = candidate["adjustment"]
    settlements = candidate["settlement"]
    changes_by_record = {}
    for change in preview.changes:
        changes_by_record.setdefault((change.kind, change.record_id), change)

    def error(kind, record_id, field, code, message):
        change = changes_by_record.get((kind, record_id))
        # A change to a parent can invalidate an unchanged dependent record.
        if change is None and preview.changes:
            change = preview.changes[0]
        if change is not None:
            issue(preview, change.file, change.row_number, change.record_id, field, code, message)

    def unique(kind, rows, fields):
        seen = {}
        for key, row in rows.items():
            value = tuple(row[f] for f in fields)
            if value in seen:
                error(kind, key, ",".join(fields), "BUSINESS_KEY_CONFLICT", "业务键已存在")
            else:
                seen[value] = key

    unique("order", orders, ("store_id", "order_id", "line_item_id"))
    unique("allocation", allocations, ("expense_id", "order_line_id"))
    unique("settlement", settlements, ("order_line_id",))

    for key, row in orders.items():
        if row["sku_id"] not in candidate["sku"]:
            error("order", key, "sku_id", "FK_NOT_FOUND", "SKU 不存在")
        if row["refunded_amount_cents"] > row["paid_amount_cents"]:
            error("order", key, "refunded_amount", "TYPE_ERROR", "退款超过支付")
        if row["returned_units"] > row["quantity"]:
            error("order", key, "returned_units", "TYPE_ERROR", "退回数量超过订单数量")
        if row["refunded_amount_cents"] > 0 and not row["refund_updated_at"]:
            error("order", key, "refund_updated_at", "TYPE_ERROR", "已退款须提供退款时间")

    refs = {}
    for kind, records in (("expense", expenses), ("adjustment", adjustments)):
        for key, row in records.items():
            if row["source_ref"] in refs:
                error(kind, key, "source_ref", "BUSINESS_KEY_CONFLICT", "成本证据索引重复")
            else:
                refs[row["source_ref"]] = (kind, key)

    by_expense = defaultdict(list)
    sources = {}

    def add_source(expense_id, order_id, amount):
        row = expenses[expense_id]
        source_key = (order_id, row["category"])
        if source_key in sources:
            error(
                "expense",
                expense_id,
                "category",
                "DOUBLE_COST_SOURCE",
                "同订单同类别存在多个成本来源",
            )
        else:
            sources[source_key] = (expense_id, amount)

    for key, row in allocations.items():
        expense = expenses.get(row["expense_id"])
        if expense is None or row["order_line_id"] not in orders:
            error("allocation", key, "", "FK_NOT_FOUND", "费用或订单不存在")
            continue
        if expense["allocation_mode"] != "pool":
            error("allocation", key, "expense_id", "TYPE_ERROR", "分摊只能引用费用池")
            continue
        by_expense[row["expense_id"]].append(row)
        add_source(row["expense_id"], row["order_line_id"], row["amount_cents"])

    for key, row in expenses.items():
        mode, basis, amount = row["allocation_mode"], row["basis"], row["amount_cents"]
        if (basis == "unknown") != (amount is None):
            error("expense", key, "amount", "TYPE_ERROR", "未知依据须为空，其他依据须有金额")
        if basis == "unknown" and mode != "direct":
            error("expense", key, "basis", "TYPE_ERROR", "未知费用只能直接归属")
        if (mode == "overhead") != (row["category"] == "overhead"):
            error("expense", key, "category", "TYPE_ERROR", "固定费用须使用 overhead 模式和类别")
        if mode == "direct":
            if row["order_line_id"] not in orders:
                error("expense", key, "order_line_id", "FK_NOT_FOUND", "直接费用须关联有效订单")
            else:
                add_source(key, row["order_line_id"], amount)
        elif row["order_line_id"] is not None:
            error("expense", key, "order_line_id", "TYPE_ERROR", "非直接费用不能指定订单")
        if mode == "pool" and (
            not by_expense[key]
            or (amount is not None and sum(a["amount_cents"] for a in by_expense[key]) != amount)
        ):
            error("expense", key, "amount", "ALLOCATION_MISMATCH", "费用池必须有分摊且金额合计一致")

    grouped_adjustments = defaultdict(list)
    for key, row in adjustments.items():
        expense = expenses.get(row["expense_id"])
        order = orders.get(row["order_line_id"])
        source = sources.get((row["order_line_id"], expense["category"])) if expense else None
        if expense is None or order is None or source is None or source[0] != row["expense_id"]:
            error("adjustment", key, "", "FK_NOT_FOUND", "调整须引用已归属该订单的费用")
            continue
        grouped_adjustments[(row["order_line_id"], expense["category"])].append(
            Adjustment(row["kind"], row["amount_cents"], row["basis"], row["recovered_units"])
        )

    for (order_id, category), (expense_id, amount) in sources.items():
        order = orders[order_id]
        try:
            apply_adjustments(
                category,
                amount,
                expenses[expense_id]["basis"],
                order["quantity"],
                order["returned_units"],
                grouped_adjustments[(order_id, category)],
            )
        except ValueError as exc:
            error("expense", expense_id, "", "RECOVERY_LIMIT", str(exc))

    for key, row in settlements.items():
        order = orders.get(row["order_line_id"])
        if order is None:
            error("settlement", key, "order_line_id", "FK_NOT_FOUND", "订单不存在")
            continue
        if row["cutoff_at"] < order["paid_at"]:
            error(
                "settlement",
                key,
                "cutoff_at",
                "TIME_ORDER_ERROR",
                "结算截止时间不能早于订单付款时间",
            )
        if row["covered_refunded_amount_cents"] > order["refunded_amount_cents"]:
            error(
                "settlement", key, "covered_refunded_amount", "TYPE_ERROR", "覆盖退款超过当前退款"
            )
        if row["cutoff_at"] > row["updated_at"]:
            error("settlement", key, "cutoff_at", "TIME_ORDER_ERROR", "截止时间晚于修订时间")

    for key in orders:
        missing = [
            category
            for category in CATEGORIES
            if (key, category) not in sources or sources[(key, category)][1] is None
        ]
        if missing:
            preview.warnings.append(f"{key} 未知或未归属费用：{', '.join(missing)}")
