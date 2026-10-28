"""Local-only proposal decisions and evidence-backed P4 review."""

import json
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import ActionProposal, OrderLine, ProductCandidate, ProposalAudit, Sku
from xhs_seller.services.drafts import (
    clean_text,
    escape_markdown,
    local_time,
    now,
    validate_operator,
    validate_ref,
    validate_revision_time,
)
from xhs_seller.services.imports import ID_PATTERN, SENSITIVE_TEXT, dumps, money_to_cents
from xhs_seller.services.p4_report import build_report

ACTIONS = {"list", "reprice", "purchase", "refund", "advertise", "other"}
TARGETS = {"product": ProductCandidate, "sku": Sku, "order": OrderLine}
DECISIONS = {"approved", "rejected", "cancelled", "expired"}


def create_proposal(
    engine,
    *,
    proposal_id,
    action_type,
    target_type,
    target_id,
    before,
    after,
    reason,
    evidence_refs,
    estimated_cost,
    operator,
    real_data,
):
    operator = validate_operator(operator)
    proposal_id = proposal_id.strip()
    if not ID_PATTERN.fullmatch(proposal_id) or SENSITIVE_TEXT.search(proposal_id):
        raise ValueError("提案 ID 格式无效")
    if action_type not in ACTIONS or target_type not in TARGETS:
        raise ValueError("提案类型或目标类型无效")
    before = clean_text(before, required=True)
    after = clean_text(after, required=True)
    reason = clean_text(reason, required=True)
    refs = [validate_ref(ref, real_data=real_data) for ref in evidence_refs if ref.strip()]
    if not refs:
        raise ValueError("至少填写一个证据索引")
    cost = money_to_cents(estimated_cost.strip()) if estimated_cost.strip() else None
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        if session.get(ActionProposal, proposal_id) is not None:
            raise ValueError("提案 ID 已存在")
        if session.get(TARGETS[target_type], target_id) is None:
            raise ValueError("提案目标不存在")
        timestamp = now()
        proposal = ActionProposal(
            proposal_id=proposal_id,
            revision=1,
            updated_at=timestamp,
            action_type=action_type,
            target_type=target_type,
            target_id=target_id,
            before_snapshot=before,
            after_snapshot=after,
            reason=reason,
            evidence_refs_json=dumps(refs),
            estimated_cost_cents=cost,
            status="proposed",
            created_at=timestamp,
            decided_at=None,
            decided_by=None,
        )
        session.add(proposal)
        session.add(
            ProposalAudit(
                audit_id=str(uuid4()),
                proposal_id=proposal_id,
                operator=operator,
                recorded_at=timestamp,
                decision_note="创建提案",
                old_json=None,
                new_json=dumps(proposal.model_dump()),
            )
        )
        session.commit()
        session.refresh(proposal)
        return proposal


def decide_proposal(engine, *, proposal_id, expected_revision, decision, note, operator):
    operator = validate_operator(operator)
    note = clean_text(note, required=True)
    if decision not in DECISIONS:
        raise ValueError("决定状态无效")
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        proposal = session.get(ActionProposal, proposal_id)
        if proposal is None:
            raise ValueError("提案不存在")
        if proposal.revision != expected_revision or proposal.status != "proposed":
            raise ValueError("提案已变化或已结束，请刷新；改变意图须创建新提案")
        old = proposal.model_dump()
        timestamp = validate_revision_time(now(), proposal.updated_at)
        proposal.status = decision
        proposal.revision += 1
        proposal.updated_at = proposal.decided_at = timestamp
        proposal.decided_by = operator
        session.add(
            ProposalAudit(
                audit_id=str(uuid4()),
                proposal_id=proposal_id,
                operator=operator,
                recorded_at=timestamp,
                decision_note=note,
                old_json=dumps(old),
                new_json=dumps(proposal.model_dump()),
            )
        )
        session.commit()
        session.refresh(proposal)
        return proposal


def export_todo(engine, proposal_id):
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        proposal = session.get(ActionProposal, proposal_id)
        if proposal is None or proposal.status != "approved":
            raise ValueError("只有已批准提案可导出待办")
        audits = session.exec(
            select(ProposalAudit).where(ProposalAudit.proposal_id == proposal_id)
        ).all()
        decisions = [
            item
            for item in audits
            if item.old_json is not None
            and json.loads(item.old_json)["status"] == "proposed"
            and json.loads(item.new_json) == proposal.model_dump()
            and item.operator == proposal.decided_by
            and item.recorded_at == proposal.decided_at
        ]
        if len(decisions) != 1:
            raise ValueError("批准决策审计缺失或不一致，无法导出待办")
        decision = decisions[0]
        lines = [
            "# 本地待办（未执行）",
            "",
            f"提案：{proposal.proposal_id}；修订：{proposal.revision}",
            f"目标：{proposal.target_type} / {proposal.target_id}",
            f"类型：{proposal.action_type}",
            f"批准：{proposal.decided_by} / {local_time(proposal.decided_at)}",
            f"决定依据：{escape_markdown(decision.decision_note)}",
            f"决策审计 ID：{decision.audit_id}",
            f"原状：{escape_markdown(proposal.before_snapshot)}",
            f"建议：{escape_markdown(proposal.after_snapshot)}",
            f"原因：{escape_markdown(proposal.reason)}",
            f"预计成本（分）：{proposal.estimated_cost_cents if proposal.estimated_cost_cents is not None else '未知'}",
            "证据：",
        ]
        lines.extend(f"- {escape_markdown(ref)}" for ref in json.loads(proposal.evidence_refs_json))
        lines.append("\n仅供人工处理；此文件不代表任何平台操作已执行。")
        return "\n".join(lines) + "\n"


def review(engine, as_of):
    report = build_report(engine, as_of)
    issues = []
    if not report["rows"]:
        issues.append(
            {"type": "no_orders", "id": "", "detail": "暂无订单，不能推断经营结果", "revisions": {}}
        )
    for row in report["rows"]:
        cost, settlement = row["cost"], row["settlement"]

        def add(kind, detail, row=row):
            issues.append(
                {
                    "type": kind,
                    "id": row["order_line_id"],
                    "detail": detail,
                    "revisions": row["revisions"],
                }
            )

        if cost.missing:
            add("unknown_cost", "未知费用：" + ", ".join(cost.missing))
        estimated = [name for name, (_, basis) in row["categories"].items() if basis == "estimated"]
        if estimated:
            add("estimated_cost", "估计费用：" + ", ".join(estimated) + "；贡献利润不是已确认利润")
        if cost.contribution_cents is not None and cost.contribution_cents < 0:
            add("negative_contribution", f"贡献利润 {cost.contribution_cents} 分（{cost.status}）")
        if settlement is None:
            add("missing_settlement", "未记录结算，到账及差异未知")
        else:
            if settlement.difference_cents:
                add("settlement_difference", f"结算差异 {settlement.difference_cents} 分")
            if settlement.refund_not_covered_cents:
                add(
                    "refund_not_covered", f"结算未反映退款 {settlement.refund_not_covered_cents} 分"
                )
    for item in report["unallocated"]:
        issues.append(
            {
                "type": "unallocated",
                "id": item["id"],
                "detail": f"未分摊费用：{item['category']}，金额分：{item['amount_cents']}，依据：{item['basis']}",
                "revisions": {"expense": {item["id"]: item["revision"]}},
            }
        )
    for item in report["overhead"]:
        if item["basis"] != "actual":
            issues.append(
                {
                    "type": "overhead_unconfirmed",
                    "id": item["id"],
                    "detail": f"固定费用依据：{item['basis']}；金额分：{item['amount_cents']}",
                    "revisions": {"expense": {item["id"]: item["revision"]}},
                }
            )
    lines = [
        "# 经营复盘草稿",
        "",
        f"数据时点：{local_time(report['as_of'])}",
        "基于当前修订，不回放历史快照；贡献利润不是净利润。",
        "",
        "## 异常、缺失与待核对",
    ]
    for item in issues:
        lines.append(
            f"- {escape_markdown(item['type'])} / {escape_markdown(item['id'])}：{escape_markdown(item['detail'])}；来源修订：{escape_markdown(dumps(item['revisions']))}"
        )
    if not issues:
        lines.append("- 当前规则未发现列出的异常；不代表数据完整或已完成经营核验。")
    lines.extend(
        ["", "## 人工复核", "- 核对来源真实性、未解决差异及下一步；本草稿不执行任何经营操作。"]
    )
    return report, issues, "\n".join(lines) + "\n"
