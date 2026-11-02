"""Read-only, independently timestamped business reminder groups."""

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from xhs_seller.services.drafts import local_time
from xhs_seller.services.workbench_queries import inventory_counts, order_alerts, pending_proposals
from xhs_seller.ui_navigation import go
from xhs_seller.ui_p4 import money
from xhs_seller.ui_time import report_time_input

GROUPS = {
    "unknown": "成本待补订单",
    "estimated": "含估计成本订单",
    "settlement": "结算待核对订单",
    "unallocated": "未分摊费用",
}
BASIS = {"actual": "按当前实际成本", "estimated": "含估计成本", "unknown": "未知"}
CATEGORIES = {
    "goods": "采购",
    "outbound_shipping": "发货",
    "return_shipping": "退回运费",
    "packaging": "包装",
    "platform_fee": "平台",
    "marketing": "获客",
    "other": "其他",
}


def amount(cents):
    return "未知" if cents is None else f"{money(cents)} 元"


def show_alert(row, kind):
    with st.container(border=True):
        st.write(f"{row['id']} · " + "；".join(row["reasons"]))
        if kind == "unallocated":
            st.write(
                f"{BASIS[row['basis']]} · {amount(row['amount_cents'])} · 修订 {row['revision']}"
            )
        else:
            st.write(f"SKU：{row['sku_id']}；成本状态：{BASIS[row['cost_status']]}")
            if row["missing_categories"]:
                st.write("待补成本：" + "、".join(CATEGORIES[k] for k in row["missing_categories"]))
            if kind == "settlement":
                st.write(
                    f"结算差异：{amount(row['difference_cents'])}；待反映退款：{amount(row['refund_not_covered_cents'])}"
                )
            with st.expander(f"{row['id']} 来源修订"):
                st.json(row["revisions"])
            st.button(
                f"查看订单 {row['id']}",
                key=f"wb_{kind}_{row['id']}",
                on_click=go,
                args=("订单成本与对账", "order", row["id"], row["sku_id"]),
            )


def pager(result, key):
    st.session_state[key] = result.page
    st.caption(f"共 {result.total} 项 · 第 {result.page} / {result.pages} 页 · 每页最多 50 项")
    if result.pages > 1:
        st.number_input("页码", min_value=1, max_value=result.pages, step=1, key=key)


def render(engine, mode):
    st.header("经营工作台")
    st.caption("打开后先看要核对的事项。只覆盖已经录入的记录，不会联网更新。")
    context = st.session_state.get("wb_context", {})
    page_keys = {"proposals": "wb_proposal_page", **{kind: f"wb_{kind}_page" for kind in GROUPS}}
    for kind, key in page_keys.items():
        if key not in st.session_state:
            st.session_state[key] = context.get("pages", {}).get(kind, 1)
    try:
        products, quotes, skus = inventory_counts(engine)
        st.write(f"{products} 个候选 · {quotes} 份报价 · {skus} 个 SKU")
        if not (products or quotes or skus):
            st.info("尚未录入候选、报价或 SKU。可以先导入资料，或在手工编辑里逐条录入。")
    except SQLAlchemyError:
        st.error("录入概览读取失败，请核对本地数据库。")
    shortcuts = (
        ("离线协作", "规则评估"),
        ("数据导入", "导入资料"),
        ("编辑与导入", "手工录入"),
    )
    for column, (page, label) in zip(st.columns(len(shortcuts)), shortcuts, strict=True):
        with column:
            st.button(label, key=f"wb_go_{page}", on_click=go, args=(page,), width="stretch")
    as_of = report_time_input("经营提醒报告时点（含时区）", "wb_as_of")
    st.button("重新读取提醒", key="wb_refresh", type="primary")
    proposals = report = None
    try:
        proposals, read_at = pending_proposals(engine, st.session_state.get("wb_proposal_page", 1))
    except (ValueError, SQLAlchemyError):
        st.error("待审提案读取失败，请重新读取或核对本地数据库。")
    else:
        # Preserve hidden groups' pages against Streamlit's widget cleanup.
        st.session_state.wb_proposal_page = proposals.page
    try:
        report = order_alerts(
            engine, as_of, {kind: st.session_state.get(f"wb_{kind}_page", 1) for kind in GROUPS}
        )
    except ValueError as exc:
        st.error(f"订单提醒不可计算：{exc}。请核对时点并重新读取。")
    except (SQLAlchemyError, KeyError, TypeError, RuntimeError):
        st.error("订单提醒读取失败，请重新读取或核对本地数据。")
    else:
        for kind in GROUPS:
            st.session_state[f"wb_{kind}_page"] = report[kind].page
    labels = {"proposals": "待审提案", **GROUPS}
    totals = {
        "proposals": proposals.total if proposals is not None else None,
        **{kind: report[kind].total if report is not None else None for kind in GROUPS},
    }
    st.subheader("待处理事项")
    for column, (kind, label) in zip(st.columns(len(labels)), labels.items(), strict=True):
        with column:
            if totals[kind] is None:
                st.caption(label)
                st.write("读取失败")
            else:
                st.metric(f"{label}数", totals[kind])
    st.caption("各类事项可能重叠，请分别核对；不合并为待办总数。选择一类后查看明细。")
    if "wb_group" not in st.session_state:
        previous = context.get("group")
        st.session_state.wb_group = (
            previous if previous in labels
            else next((kind for kind in labels if totals[kind]), "proposals")
        )
    selected = st.radio("查看事项", list(labels), format_func=labels.get, horizontal=True, key="wb_group")
    # This non-widget context survives visits to detail pages. Space changes
    # still clear it together with every other non-navigation session value.
    st.session_state.wb_context = {
        "group": selected, "pages": {kind: st.session_state[key] for kind, key in page_keys.items()},
    }
    st.subheader(labels[selected])
    if selected == "proposals":
        if proposals is None:
            st.info("此类事项暂时无法读取，请重新读取提醒；其他正常类别仍可查看。")
            return
        st.caption(f"提案读取时间：{local_time(read_at)}；计数与本页列表来自同一读取快照。")
        pager(proposals, "wb_proposal_page")
        if not proposals.items:
            st.info("当前没有待审提案。")
        for proposal in proposals.items:
            with st.container(border=True):
                st.write(f"{proposal.proposal_id} · {proposal.target_type} / {proposal.target_id}")
                st.button(
                    f"查看提案 {proposal.proposal_id}",
                    key=f"wb_proposal_{proposal.proposal_id}",
                    on_click=go,
                    args=("本地提案与复盘", "proposal", proposal.proposal_id),
                )
        return
    if report is None:
        st.info("此类事项暂时无法读取，请核对报告时点后重新读取；其他正常类别仍可查看。")
        return
    st.caption(
        f"订单与费用读取时间：{local_time(report['read_at'])}；报告时点：{local_time(report['as_of'])}。这四组来自同一次核对。"
    )
    if not report["order_count"]:
        st.info("尚未录入订单；没有结算记录不等于到账为零。")
    result = report[selected]
    pager(result, f"wb_{selected}_page")
    if not result.items:
        st.info(f"当前没有{labels[selected]}。")
    for row in result.items:
        show_alert(row, selected)
