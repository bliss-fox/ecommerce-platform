"""Local proposal review and read-only operating review."""

import json

import streamlit as st
from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import ActionProposal, OrderLine, ProductCandidate, ProposalAudit, Sku
from xhs_seller.services.drafts import local_time
from xhs_seller.services.proposals import create_proposal, decide_proposal, export_todo, review
from xhs_seller.ui_time import report_time_input


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
    with st.form(f"p5_create_{mode}_{target_type}_{target_id}"):
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


def render_decisions(engine, mode):
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        proposals = session.exec(select(ActionProposal).order_by(ActionProposal.created_at)).all()
        if not proposals:
            st.info("暂无提案。")
            return
        selected = st.selectbox("选择提案", [p.proposal_id for p in proposals])
        proposal = next(p for p in proposals if p.proposal_id == selected)
        audits = session.exec(
            select(ProposalAudit)
            .where(ProposalAudit.proposal_id == selected)
            .order_by(ProposalAudit.recorded_at)
        ).all()
    revision_key = f"p5_seen_{mode}_{selected}"
    previous_revision = st.session_state.get(revision_key)
    if previous_revision is not None and previous_revision != proposal.revision:
        st.warning("提案已被其他会话修改；旧表单中的决定未记录，请查看当前状态和决策审计。")
    st.session_state[revision_key] = proposal.revision
    st.json(proposal_snapshot_view(proposal.model_dump()))
    with st.expander("查看决策审计与前后值"):
        for audit in audits:
            st.json(audit_view(audit))
    if proposal.status == "proposed":
        st.caption("到期由人工确认；决定后不能更改，变更意图请创建新提案。")
        with st.form(f"p5_decide_{mode}_{selected}_{proposal.revision}"):
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


def render(engine, mode):
    st.header("本地提案与复盘")
    page = st.radio("P5 操作", ["创建提案", "审核与待办", "异常与复盘"], horizontal=True)
    if page == "创建提案":
        render_create(engine, mode)
    elif page == "审核与待办":
        render_decisions(engine, mode)
    else:
        render_review(engine)
