"""Read-only, bounded proposal queries for the local review page."""

from dataclasses import dataclass

from sqlalchemy import func, or_
from sqlmodel import Session, select

from xhs_seller.models import ActionProposal
from xhs_seller.services.drafts import clean_text
from xhs_seller.services.imports import ID_PATTERN
from xhs_seller.services.proposals import DECISIONS, TARGETS

PAGE_SIZE = 50


def proposal_page_for_id(session, proposal_id):
    """Locate in the unfiltered A21 order within the caller's read transaction."""
    target = session.get(ActionProposal, proposal_id)
    if target is None:
        raise ValueError("定位提案不存在，请重新选择")
    preceding = session.exec(select(func.count()).select_from(ActionProposal).where(or_(
        ActionProposal.created_at < target.created_at,
        (ActionProposal.created_at == target.created_at) & (ActionProposal.proposal_id < proposal_id),
    ))).one()
    return preceding // PAGE_SIZE + 1


@dataclass(frozen=True)
class ProposalPage:
    items: tuple[ActionProposal, ...]
    total: int
    page: int
    pages: int


def list_proposals(
    session: Session, *, status=None, target_type=None, query="", page=1
) -> ProposalPage:
    """Count and fetch at most one page; the caller supplies a read transaction.

    SQLite instr treats ID fragments literally and preserves case, including '_'.
    """
    if status is not None and status not in DECISIONS | {"proposed"}:
        raise ValueError("提案状态筛选无效")
    if target_type is not None and target_type not in TARGETS:
        raise ValueError("提案目标筛选无效")
    if type(page) is not int or page < 1:
        raise ValueError("提案页码必须为正整数")
    query = clean_text(query)
    if query and not ID_PATTERN.fullmatch(query):
        raise ValueError("ID 筛选仅接受字母、数字、下划线或短横线，最多 64 字符")
    conditions = []
    if status is not None:
        conditions.append(ActionProposal.status == status)
    if target_type is not None:
        conditions.append(ActionProposal.target_type == target_type)
    if query:
        conditions.append(
            or_(
                func.instr(ActionProposal.proposal_id, query) > 0,
                func.instr(ActionProposal.target_id, query) > 0,
            )
        )
    total = session.exec(
        select(func.count()).select_from(ActionProposal).where(*conditions)
    ).one()
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    items = session.exec(
        select(ActionProposal)
        .where(*conditions)
        .order_by(ActionProposal.created_at, ActionProposal.proposal_id)
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()
    return ProposalPage(tuple(items), total, page, pages)
