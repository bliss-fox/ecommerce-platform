"""P4 offline import confirmation and read-only reconciliation page."""

import csv
import hashlib
import io
import json
import zipfile
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import Expense, ExpenseAllocation, OrderLine
from xhs_seller.services.drafts import local_time
from xhs_seller.services.imports import BaselineChangedError, InputFile, commit, preflight
from xhs_seller.services.order_cost import allocate_by_quantity
from xhs_seller.services.p4_report import build_report
from xhs_seller.ui_csv import warn_formula_fields
from xhs_seller.ui_preview import preview_page
from xhs_seller.ui_time import report_time_input

FILES = {
    "order": ("订单行", "orders.csv"),
    "expense": ("费用", "expenses.csv"),
    "allocation": ("费用分摊", "expense_allocations.csv"),
    "adjustment": ("成本调整", "cost_adjustments.csv"),
    "settlement": ("结算快照", "settlements.csv"),
}


def money(cents):
    if cents is None:
        return "未知"
    sign = "-" if cents < 0 else ""
    value = abs(cents)
    return f"{sign}{value // 100}.{value % 100:02d}"


def csv_bytes(rows, headers):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode("utf-8-sig")


def render_import(engine, mode):
    st.caption("选择一组文件预检；全部通过后，确认才会写入。订单须使用去身份化业务键。")
    inputs = []
    for kind, (label, _) in FILES.items():
        upload = st.file_uploader(f"{label} CSV", type="csv", key=f"p4_upload_{mode}_{kind}")
        if upload is not None:
            inputs.append(InputFile(kind, upload.name, upload.getvalue()))
    fingerprint = [(i.kind, i.name, hashlib.sha256(i.content).hexdigest()) for i in inputs]
    reset_confirmation = False
    for confirmation, kinds in (
        ("mapping", {"order"}),
        ("full_principal", {"settlement"}),
        ("refunds_excluded", {"expense", "adjustment"}),
    ):
        source_key = f"p4_confirmation_source_{mode}_{confirmation}"
        checkbox_key = f"p4_{confirmation}_{mode}"
        source = tuple(item for item in fingerprint if item[0] in kinds)
        if st.session_state.get(source_key) != source:
            reset_confirmation = reset_confirmation or st.session_state.get(checkbox_key, False)
            st.session_state[checkbox_key] = False
            st.session_state[source_key] = source
    if reset_confirmation:
        st.info("相关文件已变更，请重新核对并确认对应的来源条件。")
    has_orders = any(item.kind == "order" for item in inputs)
    has_settlements = any(item.kind == "settlement" for item in inputs)
    has_costs = any(item.kind in {"expense", "adjustment"} for item in inputs)
    mapped = (
        st.checkbox(
            "订单支付金额已按契约映射，不混入独立运费收入、补贴、税费或跨币种项目",
            key=f"p4_mapping_{mode}",
        )
        if has_orders
        else False
    )
    full_principal = (
        st.checkbox(
            "每条结算快照覆盖整条订单行本金，不含部分本金结算", key=f"p4_full_principal_{mode}"
        )
        if has_settlements
        else False
    )
    costs_exclude_refunds = (
        st.checkbox("费用和成本调整不包含订单退款金额", key=f"p4_refunds_excluded_{mode}")
        if has_costs
        else False
    )
    fingerprint += [
        ("mapping", mapped),
        ("full_principal", full_principal),
        ("refunds_excluded", costs_exclude_refunds),
    ]
    if st.session_state.get("p4_mode") != mode or (
        st.session_state.get("p4_source") == "upload"
        and st.session_state.get("p4_fingerprint") != fingerprint
    ):
        st.session_state.pop("p4_preview", None)
    if st.button("预检 P4 所选文件", disabled=not inputs):
        st.session_state.p4_preview = preflight(
            engine,
            inputs,
            real_data=mode == "真实数据",
            orders_are_mapped=mapped,
            settlements_cover_full_principal=full_principal,
            costs_exclude_refunds=costs_exclude_refunds,
        )
        st.session_state.p4_mode = mode
        st.session_state.p4_source = "upload"
        st.session_state.p4_fingerprint = fingerprint
    if mode == "合成演示" and st.button("预检 P4 合成示例", disabled=bool(inputs)):
        directory = Path(__file__).resolve().parents[2] / "examples" / "p4"
        samples = [
            InputFile(kind, name, (directory / name).read_bytes())
            for kind, (_, name) in FILES.items()
        ]
        st.session_state.p4_preview = preflight(
            engine,
            samples,
            real_data=False,
            orders_are_mapped=True,
            settlements_cover_full_principal=True,
            costs_exclude_refunds=True,
        )
        st.session_state.p4_mode = mode
        st.session_state.p4_source = "sample"
    if st.session_state.get("p4_source") == "sample" and inputs:
        st.session_state.pop("p4_preview", None)
    preview = st.session_state.get("p4_preview")
    if preview is None:
        return
    st.subheader("预检结果")
    st.write(
        f"新增 {preview.counts['insert']}，更新 {preview.counts['update']}，不变 {preview.counts['noop']}，错误 {preview.counts['rejected']}"
    )
    for warning in preview_page(
        preview.warnings, batch_id=preview.batch_id, kind="p4_warning", label="预检警告页"
    ):
        st.warning(warning)
    if preview.errors:
        st.error("存在错误，整批不能提交。")
        errors = [vars(error) for error in preview.errors]
        st.dataframe(errors, hide_index=True)
        warn_formula_fields(errors, {"record_id": "记录 ID"}, "P4 错误报告")
        st.download_button(
            "下载 P4 错误报告", csv_bytes(errors, list(errors[0])), "p4-errors.csv", "text/csv"
        )
        return
    st.caption("金额以分显示；请核对变更前后内容及缺失费用。")
    changes = preview_page(
        preview.changes, batch_id=preview.batch_id, kind="p4_change", label="预检变更页"
    )
    if len(preview.changes) > len(changes):
        st.caption("请逐页核对；确认时提交整批预检结果。")
    for change in changes:
        with st.expander(f"{FILES[change.kind][0]} {change.record_id} · {change.action}"):
            st.json({"原值": change.old, "新值": change.new})
    with st.form("p4_confirm"):
        operator = st.text_input("P4 本地操作者标识")
        confirm = st.form_submit_button("确认并原子提交 P4 数据")
    if confirm:
        try:
            commit(engine, preview, operator=operator)
        except BaselineChangedError as exc:
            st.session_state.pop("p4_preview", None)
            st.error(str(exc))
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state.pop("p4_preview", None)
            st.success("已提交，业务记录与修订审计保存在同一事务。")


def render_allocation_proposal(engine):
    st.caption("建议按订单数量分摊整数分；生成的 CSV 需返回导入页面预检并确认。")
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        all_expenses = session.exec(select(Expense)).all()
        allocations = session.exec(select(ExpenseAllocation)).all()
        orders = session.exec(select(OrderLine)).all()
    expenses = [expense for expense in all_expenses if expense.allocation_mode == "unallocated"]
    if not expenses or not orders:
        st.info("需要已保存的未分摊费用和订单才能生成建议。")
        return
    by_id = {expense.expense_id: expense for expense in expenses}
    expense_id = st.selectbox("待分摊费用", sorted(by_id))
    selected = st.multiselect("参与分摊的订单", sorted(order.order_line_id for order in orders))
    if not selected:
        return
    expense = by_id[expense_id]
    category_expenses = {
        item.expense_id: item for item in all_expenses if item.category == expense.category
    }
    assigned_orders = {
        item.order_line_id
        for item in category_expenses.values()
        if item.allocation_mode == "direct"
    }
    assigned_orders.update(
        item.order_line_id for item in allocations if item.expense_id in category_expenses
    )
    conflicts = sorted(set(selected) & assigned_orders)
    if conflicts:
        st.error("以下订单已有同类成本来源，不能重复分摊：" + "、".join(conflicts))
        st.caption("如需更正已归属费用，请修订现有来源；未知费用也不能与新来源重复归属。")
        return
    quantities = {
        order.order_line_id: order.quantity for order in orders if order.order_line_id in selected
    }
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    if now <= datetime.fromisoformat(expense.updated_at):
        st.error("当前时间未晚于费用的更新时间，不能生成可提交的修订。请核对费用时间和系统时钟。")
        return
    amounts = allocate_by_quantity(expense.amount_cents, quantities)
    stamp = now.isoformat()
    rows = [
        {
            "allocation_id": "AL-"
            + hashlib.sha256(f"{expense_id}:{order_id}".encode()).hexdigest()[:24],
            "revision": 1,
            "updated_at": stamp,
            "expense_id": expense_id,
            "order_line_id": order_id,
            "amount": money(amount),
        }
        for order_id, amount in amounts.items()
    ]
    st.dataframe(rows, hide_index=True)
    expense_row = {
        "expense_id": expense.expense_id,
        "revision": expense.revision + 1,
        "updated_at": stamp,
        "expense_date": expense.expense_date,
        "category": expense.category,
        "amount": money(expense.amount_cents),
        "basis": expense.basis,
        "allocation_mode": "pool",
        "order_line_id": "",
        "source_ref": expense.source_ref,
        "remark": expense.remark or "",
    }
    warn_formula_fields(
        [expense_row],
        {"expense_id": "费用 ID", "source_ref": "来源索引", "remark": "备注"},
        "费用池修订 CSV",
    )
    warn_formula_fields(
        rows, {"expense_id": "费用 ID", "order_line_id": "订单行 ID"}, "建议分摊 CSV"
    )
    allocation_csv = csv_bytes(rows, list(rows[0]))
    expense_csv = csv_bytes([expense_row], list(expense_row))
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("expenses.csv", expense_csv)
        archive.writestr("expense_allocations.csv", allocation_csv)
    st.caption("配套文件包包含同次生成的费用池修订和分摊明细；解压后一起上传并预检。")
    st.download_button(
        "下载配套分摊文件包",
        bundle.getvalue(),
        "allocation-proposal.zip",
        "application/zip",
        on_click="ignore",
    )
    st.download_button(
        "下载建议分摊 CSV",
        allocation_csv,
        "expense_allocations.csv",
        "text/csv",
        on_click="ignore",
    )
    st.download_button(
        "下载费用池修订 CSV",
        expense_csv,
        "expenses.csv",
        "text/csv",
        on_click="ignore",
    )


def report_rows(report):
    display_as_of = local_time(report["as_of"])
    result = []
    for row in report["rows"]:
        cost, settlement = row["cost"], row["settlement"]
        result.append(
            {
                "as_of": display_as_of,
                "order_line_id": row["order_line_id"],
                "sku_id": row["sku_id"],
                "net_sales_cny": money(cost.net_sales_cents),
                "known_cost_cny": money(cost.known_cost_cents),
                "variable_cost_cny": money(cost.variable_cost_cents),
                "contribution_cny": money(cost.contribution_cents),
                "margin": ""
                if cost.margin is None
                else f"{(cost.margin * 100).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}%",
                "cost_status": cost.status,
                "missing_categories": ",".join(cost.missing),
                "actual_received_cny": money(settlement.actual_cents)
                if settlement
                else "未记录结算",
                "expected_received_cny": money(settlement.expected_cents)
                if settlement
                else "未记录结算",
                "difference_cny": money(settlement.difference_cents)
                if settlement
                else "未记录结算",
                "refund_not_covered_cny": money(settlement.refund_not_covered_cents)
                if settlement
                else "未记录结算",
                "reconciliation_status": (
                    "未记录结算"
                    if settlement is None
                    else "待核对"
                    if settlement.difference_cents != 0
                    else "已对平，退款待反映"
                    if settlement.refund_not_covered_cents > 0
                    else "已对平"
                ),
                "source_revisions": json.dumps(
                    row["revisions"], ensure_ascii=False, sort_keys=True
                ),
            }
        )
        result[-1].update(
            {f"cost_{name}_cny": money(value[0]) for name, value in row["categories"].items()}
        )
        result[-1].update(
            {f"cost_{name}_basis": value[1] for name, value in row["categories"].items()}
        )
    return result


def sku_report_rows(report):
    result = []
    for item in report["sku_summaries"]:
        result.append(
            {
                "as_of": local_time(report["as_of"]),
                "sku_id": item["sku_id"],
                "order_count": len(item["order_line_ids"]),
                "order_line_ids": ",".join(item["order_line_ids"]),
                "net_sales_cny": money(item["net_sales_cents"]),
                "cost_status": item["status"],
                "variable_cost_cny": money(item["variable_cost_cents"]),
                "contribution_cny": money(item["contribution_cents"]),
                "actual_count": item["actual_count"],
                "actual_contribution_cny": money(item["actual_contribution_cents"])
                if item["actual_count"]
                else "无",
                "estimated_count": item["estimated_count"],
                "estimated_contribution_cny": money(item["estimated_contribution_cents"])
                if item["estimated_count"]
                else "无",
                "unknown_count": item["unknown_count"],
                "unallocated_expenses": "有，经营结果不完整" if report["unallocated"] else "无",
            }
        )
    return result


def render_report(engine):
    st.caption("报告使用当前修订，不回放历史快照；贡献利润不是净利润或现金流。")
    as_of = report_time_input("报告 as_of（含时区）", "p4_report_as_of")
    try:
        report = build_report(engine, as_of)
    except ValueError as exc:
        st.error(str(exc))
        return
    display_as_of = local_time(report["as_of"])
    st.caption(f"报告时点（上海）：{display_as_of}")
    rows = report_rows(report)
    if not rows:
        st.info("暂无订单数据。")
    else:
        st.dataframe(rows, hide_index=True)
        warn_formula_fields(
            rows, {"order_line_id": "订单行 ID", "sku_id": "SKU ID"}, "订单明细 CSV"
        )
        st.download_button(
            "下载订单成本与对账明细",
            csv_bytes(rows, list(rows[0])),
            "p4-order-report.csv",
            "text/csv",
        )
        sku_rows = sku_report_rows(report)
        st.subheader("SKU 汇总")
        st.caption("仅汇总订单行的派生成本；来源修订可通过订单行 ID 在明细中核对。")
        st.dataframe(sku_rows, hide_index=True)
        warn_formula_fields(
            sku_rows, {"sku_id": "SKU ID", "order_line_ids": "订单行 ID"}, "SKU 汇总 CSV"
        )
        st.download_button(
            "下载 SKU 汇总", csv_bytes(sku_rows, list(sku_rows[0])), "p4-sku-report.csv", "text/csv"
        )
        cost_rows = report["rows"]
        unknown = [r for r in cost_rows if r["cost"].status == "unknown"]
        settlement = report["settlement_summary"]
        st.write(f"净销售合计：{money(sum(r['cost'].net_sales_cents for r in cost_rows))} 元")
        for status, label in (("actual", "按当前实际成本"), ("estimated", "含估计成本")):
            subtotal = report["cost_subtotals"][status]
            if subtotal["count"]:
                st.write(
                    f"{label}（{subtotal['count']} 行）：净销售 {money(subtotal['net_sales_cents'])} 元；变量成本 {money(subtotal['variable_cost_cents'])} 元；贡献利润 {money(subtotal['contribution_cents'])} 元。"
                )
            else:
                st.write(f"{label}：0 行。")
        st.write(f"未知成本：{len(unknown)} 行，不计入以上成本和贡献利润小计。")
        if settlement is not None:
            st.write(
                f"已记录累计到账：{money(settlement['actual_cents'])} 元（{settlement['recorded_count']} 行）；待反映退款：{money(settlement['refund_not_covered_cents'])} 元；结算差异净额：{money(settlement['difference_cents'])} 元；待核对：{settlement['difference_count']} 行（正差额：{money(settlement['positive_difference_cents'])} 元，负差额：{money(settlement['negative_difference_cents'])} 元）。未记录结算 {settlement['missing_count']} 行。"
            )
        else:
            st.write("未记录结算；到账、待反映退款和结算差异未知。")
    if report["unallocated"]:
        st.warning("存在未分配费用，汇总不是完整经营结果。")
    assigned = report["assigned"]
    for basis, label in (("actual", "实际"), ("estimated", "估计")):
        matching = [item for item in assigned if item["basis"] == basis]
        if matching:
            subtotal = sum(item["amount_cents"] for item in matching)
            st.write(
                f"原始已归属费用（{label}）：{money(subtotal)} 元（{len(matching)} 项）；费用池只计一次，未含后续调整。"
            )
        else:
            st.write(f"原始已归属费用（{label}）：未记录。")
    st.write(
        f"原始已归属费用中未知金额：{sum(item['basis'] == 'unknown' for item in assigned)} 项。"
    )
    st.write("未分摊费用", report["unallocated"])
    st.write("固定费用（不计入订单贡献）", report["overhead"])
    checklist = []
    for group in ("assigned", "unallocated", "overhead"):
        for item in report[group]:
            checklist.append(
                {
                    "as_of": display_as_of,
                    "type": group,
                    "id": item["id"],
                    "revision": item["revision"],
                    "category": item.get("category", "overhead"),
                    "basis": item["basis"],
                    "amount_cny": money(item["amount_cents"]),
                }
            )
    for row in report["rows"]:
        if row["cost"].missing:
            checklist.append(
                {
                    "as_of": display_as_of,
                    "type": "unknown_order_cost",
                    "id": row["order_line_id"],
                    "revision": json.dumps(row["revisions"], ensure_ascii=False, sort_keys=True),
                    "category": ",".join(row["cost"].missing),
                    "basis": "unknown",
                    "amount_cny": "未知",
                }
            )
    headers = ["as_of", "type", "id", "revision", "category", "basis", "amount_cny"]
    warn_formula_fields(checklist, {"id": "记录 ID"}, "费用核对 CSV")
    st.download_button(
        "下载费用核对表", csv_bytes(checklist, headers), "p4-checklist.csv", "text/csv"
    )


def render(engine, mode):
    st.header("订单成本与对账")
    page = st.radio("P4 操作", ["报表与核对", "CSV 导入", "分摊建议"], horizontal=True)
    if page == "CSV 导入":
        render_import(engine, mode)
    elif page == "分摊建议":
        render_allocation_proposal(engine)
    else:
        render_report(engine)
