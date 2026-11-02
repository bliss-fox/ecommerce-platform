"""Local proposal review and read-only operating review."""

import json
from datetime import datetime

import streamlit as st
from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import OrderLine, ProductCandidate, ProposalAudit, Sku
from xhs_seller.services.drafts import local_time
from xhs_seller.services.proposal_queries import PAGE_SIZE, list_proposals, proposal_page_for_id
from xhs_seller.services.proposals import (
    ProposalAuditError,
    create_proposal,
    decide_proposal,
    export_todo,
    review,
    validate_proposal_history,
)
from xhs_seller.ui_database import recovery_key
from xhs_seller.ui_time import report_time_input

STATUS_FILTERS = {
    "全部状态": None,
    "待审核": "proposed",
    "已批准": "approved",
    "已拒绝": "rejected",
    "已取消": "cancelled",
    "已到期": "expired",
}
TARGET_FILTERS = {"全部目标": None, "候选": "product", "SKU": "sku", "订单": "order"}
STATUS_LABELS = {value: label for label, value in STATUS_FILTERS.items()}
TARGET_LABELS = {value: label for label, value in TARGET_FILTERS.items()}


def proposal_list_view(proposal):
    try:
        if datetime.fromisoformat(proposal.created_at).utcoffset() is None:
            raise ValueError
        created_at = local_time(proposal.created_at)
    except (ValueError, TypeError):
        # A corrupt row must remain selectable so its audit error can be shown.
        created_at = "时间无效，待核对"
    return {
        "提案 ID": proposal.proposal_id,
        "状态": STATUS_LABELS.get(proposal.status, proposal.status),
        "目标类型": TARGET_LABELS.get(proposal.target_type, proposal.target_type),
        "目标 ID": proposal.target_id,
        "建议类型": proposal.action_type,
        "创建时间（上海）": created_at,
        "修订": proposal.revision,
    }


def proposal_snapshot_view(snapshot):
    result = dict(snapshot)
    result["evidence_refs"] = json.loads(result.pop("evidence_refs_json"))
    for key in ("created_at", "updated_at", "decided_at"):
        if result[key] is not None:
            result[key] = local_time(result[key])
    return result


def audit_view(audit):
    result = audit.model_dump()
    result["recorded_at"] = local_time(result["recorded_at"])
    old = result.pop("old_json")
    result["old"] = proposal_snapshot_view(json.loads(old)) if old is not None else None
    result["new"] = proposal_snapshot_view(json.loads(result.pop("new_json")))
    return result


def render_create(engine, mode):
    st.caption("记录人工建议和证据；快照由人工填写，不会修改目标记录。")
    with Session(engine) as session:
        targets = {
            "product": [row.product_id for row in session.exec(select(ProductCandidate)).all()],
            "sku": [row.sku_id for row in session.exec(select(Sku)).all()],
            "order": [row.order_line_id for row in session.exec(select(OrderLine)).all()],
        }
    target_type = st.selectbox("目标类型", list(targets))
    if not targets[target_type]:
        st.info("此类型暂无目标记录。")
        return
    target_id = st.selectbox("目标 ID", sorted(targets[target_type]))
    st.caption("切换目标会清空未提交的提案内容，请先确认目标。")
    with st.form(recovery_key(f"p5_create_{mode}_{target_type}_{target_id}")):
        proposal_id = st.text_input("提案 ID（字母、数字、下划线或短横线）")
        action = st.selectbox(
            "建议类型", ["reprice", "list", "purchase", "refund", "advertise", "other"]
        )
        before = st.text_area("变更前快照")
        after = st.text_area("建议后快照")
        reason = st.text_area("建议原因")
        refs = st.text_area("证据索引（每行一个）")
        cost = st.text_input("预计成本（元；空白表示未知）")
        operator = st.text_input("创建人本地标识")
        submit = st.form_submit_button("创建待审提案")
    if submit:
        try:
            create_proposal(
                engine,
                proposal_id=proposal_id,
                action_type=action,
                target_type=target_type,
                target_id=target_id,
                before=before,
                after=after,
                reason=reason,
                evidence_refs=refs.splitlines(),
                estimated_cost=cost,
                operator=operator,
                real_data=mode == "真实数据",
            )
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            st.success("提案已创建，未执行任何经营操作。")


def render_decisions(engine, mode, focus_id=None):
    if focus_id is not None:
        st.session_state[f"p5_status_{mode}"] = "全部状态"
        st.session_state[f"p5_target_filter_{mode}"] = "全部目标"
        st.session_state[f"p5_id_filter_{mode}"] = ""
        st.info(f"定位提案：{focus_id}；已切换为全部筛选，显示当前状态。")
    notice = st.session_state.pop(f"p5_decision_notice_{mode}", None)
    if notice:
        st.success(notice)
    status_column, target_column, query_column = st.columns([1, 1, 2])
    with status_column:
        status = STATUS_FILTERS[
            st.selectbox("提案状态筛选", list(STATUS_FILTERS), key=f"p5_status_{mode}")
        ]
    with target_column:
        target_type = TARGET_FILTERS[
            st.selectbox("提案目标筛选", list(TARGET_FILTERS), key=f"p5_target_filter_{mode}")
        ]
    with query_column:
        query = st.text_input("提案或目标 ID 筛选", key=f"p5_id_filter_{mode}").strip()
    st.caption("ID 区分大小写、按字面包含匹配；切换筛选、页码或提案会清空未提交的决定。")
    filters = (status, target_type, query)
    page_key = f"p5_list_page_{mode}"
    if st.session_state.get(f"p5_list_filters_{mode}") != filters:
        st.session_state[f"p5_list_filters_{mode}"] = filters
        st.session_state[page_key] = 1
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        requested_page = st.session_state.get(page_key, 1)
        try:
            if focus_id is not None:
                requested_page = proposal_page_for_id(session, focus_id)
            result = list_proposals(
                session, status=status, target_type=target_type, query=query, page=requested_page
            )
        except ValueError as exc:
            st.error(str(exc))
            return
        st.session_state[page_key] = result.page
        if requested_page != result.page:
            st.info("匹配列表已变化，页码已调整到最后一页；请重新核对当前提案。")
        if not result.items:
            st.session_state.pop(f"p5_selection_context_{mode}", None)
            st.info("未找到符合筛选条件的提案，请调整筛选或创建提案。")
            return
        if result.pages > 1:
            st.number_input(
                "提案列表页", min_value=1, max_value=result.pages, step=1, key=page_key
            )
        start = (result.page - 1) * PAGE_SIZE
        st.caption(
            f"匹配 {result.total} 条，第 {result.page} / {result.pages} 页；"
            f"当前显示 {start + 1}–{start + len(result.items)}。按创建时间和提案 ID 升序。"
        )
        st.dataframe([proposal_list_view(p) for p in result.items], hide_index=True)
        ids = [p.proposal_id for p in result.items]
        selection_context = (filters, result.page, tuple(ids))
        selection_version_key = f"p5_selection_version_{mode}"
        if st.session_state.get(f"p5_selection_context_{mode}") != selection_context:
            st.session_state[f"p5_selection_context_{mode}"] = selection_context
            st.session_state[selection_version_key] = (
                st.session_state.get(selection_version_key, 0) + 1
            )
        selected_key = f"p5_selected_{mode}_{st.session_state[selection_version_key]}"
        if focus_id is not None:
            if focus_id not in ids:
                st.error("定位提案已变化，请重新选择；未自动选择其他提案。")
                return
            st.session_state[selected_key] = focus_id
        selected = st.selectbox("选择提案", ids, key=selected_key)
        proposal = next(p for p in result.items if p.proposal_id == selected)
        audits = session.exec(
            select(ProposalAudit)
            .where(ProposalAudit.proposal_id == selected)
            .order_by(ProposalAudit.recorded_at)
        ).all()
    context = (mode, selection_context, selected)
    if st.session_state.get("p5_decision_context") != context:
        st.session_state["p5_decision_context"] = context
        st.session_state["p5_decision_form_version"] = (
            st.session_state.get("p5_decision_form_version", 0) + 1
        )
    form_version = st.session_state["p5_decision_form_version"]
    revision_key = f"p5_seen_{mode}_{selected}"
    previous_revision = st.session_state.get(revision_key)
    if previous_revision is not None and previous_revision != proposal.revision:
        st.warning("提案已被其他会话修改；旧表单中的决定未记录，请查看当前状态和决策审计。")
    st.session_state[revision_key] = proposal.revision
    try:
        validate_proposal_history(proposal, audits)
    except ProposalAuditError as exc:
        st.error(str(exc))
        return
    st.json(proposal_snapshot_view(proposal.model_dump()))
    with st.expander("查看决策审计与前后值"):
        for audit in audits:
            st.json(audit_view(audit))
    if proposal.status == "proposed":
        st.caption("到期由人工确认；决定后不能更改，变更意图请创建新提案。")
        with st.form(recovery_key(f"p5_decide_{mode}_{selected}_{proposal.revision}_{form_version}")):
            decision = st.selectbox("决定", ["approved", "rejected", "cancelled", "expired"])
            note = st.text_area("决定依据")
            operator = st.text_input("决定人本地标识")
            submit = st.form_submit_button("记录本地决定")
        if submit:
            try:
                decide_proposal(
                    engine,
                    proposal_id=selected,
                    expected_revision=proposal.revision,
                    decision=decision,
                    note=note,
                    operator=operator,
                )
            except ValueError as exc:
                st.error(str(exc))
            else:
                st.session_state[revision_key] = proposal.revision + 1
                st.session_state[f"p5_decision_notice_{mode}"] = (
                    f"提案 {selected} 已记录本地决定：{STATUS_LABELS[decision]}；未执行经营操作。"
                )
                st.rerun()
    elif proposal.status == "approved":
        try:
            todo = export_todo(engine, selected)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.download_button("下载人工待办", todo, f"proposal-{selected}.md", "text/markdown")
    st.info("本地决定不触发平台操作，也不表示操作已经执行。")


def render_review(engine):
    as_of = report_time_input("复盘数据时点（含时区）", "p5_review_as_of")
    try:
        report, issues, draft = review(engine, as_of)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.caption(f"当前修订，数据时点：{local_time(report['as_of'])}；不得视为净利润或完整业务验收。")
    if issues:
        st.dataframe(
            [
                {**item, "revisions": json.dumps(item["revisions"], ensure_ascii=False)}
                for item in issues
            ],
            hide_index=True,
        )
    else:
        st.info("当前规则未发现列出的异常；仍须人工核验。")
    st.markdown(draft)
    st.download_button("下载复盘草稿", draft, "local-review.md", "text/markdown")


def render(engine, mode, focus_id=None):
    st.header("本地提案与复盘")
    if focus_id is not None:
        st.session_state.p5_operation = "审核与待办"
    page = st.radio("P5 操作", ["创建提案", "审核与待办", "异常与复盘"], horizontal=True, key="p5_operation")
    if page == "创建提案":
        render_create(engine, mode)
    elif page == "审核与待办":
        render_decisions(engine, mode, focus_id)
    else:
        render_review(engine)
