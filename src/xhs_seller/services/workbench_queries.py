"""R1 read-only reminders. No new accounting rules or persistent to-do records."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, text
from sqlmodel import Session, select

from xhs_seller.models import ProductCandidate, Sku, SupplierQuote
from xhs_seller.services.p4_report import build_report
from xhs_seller.services.proposal_queries import PAGE_SIZE, list_proposals


@dataclass(frozen=True)
class AlertPage:
    items: tuple[dict, ...]
    total: int
    page: int
    pages: int


def paginate(rows, page=1):
    if type(page) is not int or page < 1:
        raise ValueError("页码必须为正整数")
    pages = max(1, (len(rows) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    return AlertPage(tuple(rows[(page - 1) * PAGE_SIZE : page * PAGE_SIZE]), len(rows), page, pages)


def inventory_counts(engine):
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        return tuple(
            session.exec(select(func.count()).select_from(model)).one()
            for model in (ProductCandidate, SupplierQuote, Sku)
        )


def pending_proposals(engine, page=1):
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        result = list_proposals(session, status="proposed", page=page)
        read_at = datetime.now(UTC).isoformat()
    return result, read_at


def order_alerts(engine, as_of, pages=None):
    report = build_report(engine, as_of)
    read_at = datetime.now(UTC).isoformat()
    groups = {k: [] for k in ("unknown", "estimated", "settlement", "unallocated")}
    for row in report["rows"]:
        settlement = row["settlement"]
        details = {
            "id": row["order_line_id"],
            "sku_id": row["sku_id"],
            "contribution_cents": row["cost"].contribution_cents,
            "cost_status": row["cost"].status,
            "missing_categories": row["cost"].missing,
            "revisions": row["revisions"],
            "difference_cents": settlement.difference_cents if settlement else None,
            "refund_not_covered_cents": settlement.refund_not_covered_cents if settlement else None,
        }
        if row["cost"].status == "unknown":
            groups["unknown"].append(
                {**details, "reasons": ("成本信息待补",)}
            )
        elif row["cost"].status == "estimated":
            groups["estimated"].append({**details, "reasons": ("含估计成本，不能视为实际确认",)})
        reasons = []
        if settlement is None:
            reasons.append("未记录结算")
        else:
            if settlement.difference_cents != 0:
                reasons.append("结算差异非零")
            if settlement.refund_not_covered_cents > 0:
                reasons.append("退款尚未反映在结算")
        if reasons:
            groups["settlement"].append({**details, "reasons": tuple(reasons)})
    groups["unallocated"] = [
        {**item, "reasons": ("未分摊费用，经营结果不完整",)} for item in report["unallocated"]
    ]
    return {
        "as_of": report["as_of"],
        "read_at": read_at,
        "order_count": len(report["rows"]),
        **{
            k: paginate(sorted(items, key=lambda row: row["id"]), (pages or {}).get(k, 1))
            for k, items in groups.items()
        },
    }
