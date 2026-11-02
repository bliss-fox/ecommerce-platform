"""One read transaction, immutable serialization, and collection-aware baselines."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import or_, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from xhs_seller.models import (
    CandidateCheck,
    DraftAsset,
    DraftFact,
    ProductCandidate,
    ProductResearchLink,
    ResearchNote,
    RevisionAudit,
    Sku,
    SupplierQuote,
)
from xhs_seller.services.scoring import timestamp

MODELS = {
    "sku": (Sku, "sku_id"),
    "product": (ProductCandidate, "product_id"),
    "quote": (SupplierQuote, "quote_id"),
    "check": (CandidateCheck, "check_id"),
    "link": (ProductResearchLink, "link_id"),
    "research": (ResearchNote, "research_id"),
    "fact": (DraftFact, "fact_id"),
    "asset": (DraftAsset, "asset_id"),
    "audit": (RevisionAudit, "audit_id"),
}


def canonical(value):
    """Decimal is a string, never a binary float; timestamp fields normalize to UTC."""

    def normalize(item):
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, (tuple, list)):
            return [normalize(v) for v in item]
        if isinstance(item, dict):
            return {
                key: timestamp(v).astimezone(UTC).isoformat()
                if key.endswith("_at") or key in {"as_of", "assumptions_as_of"}
                else normalize(v)
                for key, v in item.items()
                if v is not None
            } | {key: None for key, v in item.items() if v is None}
        return item

    return json.dumps(
        normalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def space_key(engine, mode):
    if mode not in {"合成演示", "真实数据"}:
        raise ValueError("数据空间无效")
    path = str(Path(engine.url.database).resolve()).casefold()
    return ("demo:" if mode == "合成演示" else "real:") + hashlib.sha256(path.encode()).hexdigest()


@dataclass(frozen=True)
class Snapshot:
    space: str
    sku_ids: tuple[str, ...]
    as_of: str
    read_at: str
    payload: str
    baseline: str

    def rows(self):
        return json.loads(self.payload)


def read_snapshot(engine, mode, sku_ids, as_of, *, check_time=True):
    ids = tuple(sorted(sku_ids))
    if not 1 <= len(ids) <= 3 or len(set(ids)) != len(ids):
        raise ValueError("一次请选择 1–3 个不同的 SKU")
    as_of = timestamp(as_of).astimezone(UTC)
    space = space_key(engine, mode)
    with Session(engine) as session:
        session.exec(text("BEGIN"))

        def fetch(model, condition):
            return session.exec(select(model).where(condition)).all()

        skus = fetch(Sku, Sku.sku_id.in_(ids))
        if len(skus) != len(ids):
            raise ValueError("所选 SKU 不存在，请重新选择")
        products = {s.product_id for s in skus}
        quotes = fetch(SupplierQuote, SupplierQuote.product_id.in_(products))
        checks = fetch(CandidateCheck, CandidateCheck.product_id.in_(products))
        links = fetch(ProductResearchLink, ProductResearchLink.product_id.in_(products))
        rows = {
            "sku": skus,
            "product": fetch(ProductCandidate, ProductCandidate.product_id.in_(products)),
            "quote": quotes,
            "check": checks,
            "link": links,
            "research": fetch(
                ResearchNote, ResearchNote.research_id.in_({r.research_id for r in links})
            ),
            "fact": fetch(DraftFact, DraftFact.sku_id.in_(ids)),
            "asset": fetch(DraftAsset, DraftAsset.sku_id.in_(ids)),
            "audit": fetch(
                RevisionAudit,
                or_(
                    (RevisionAudit.entity_type == "quote")
                    & RevisionAudit.record_id.in_({q.quote_id for q in quotes}),
                    (RevisionAudit.entity_type == "check")
                    & RevisionAudit.record_id.in_({c.check_id for c in checks}),
                ),
            ),
        }
        data = {
            kind: [
                row.model_dump() for row in sorted(items, key=lambda r: getattr(r, MODELS[kind][1]))
            ]
            for kind, items in rows.items()
        }
        read_at = datetime.now(UTC).isoformat()
    for row in data["audit"]:
        for key in ("old_json", "new_json"):
            if row[key] is not None:
                try:
                    row[key] = canonical(json.loads(row[key]))
                except (ValueError, TypeError, AttributeError) as exc:
                    raise ValueError("评分来源审计无法解析，请先核对本地记录") from exc
    if check_time:
        for kind, items in data.items():
            if kind == "audit":
                continue
            if any(timestamp(item["updated_at"]) > as_of for item in items):
                raise ValueError("当前来源修订晚于报告时间；不支持历史回放")
        if any(f["checked_at"] and timestamp(f["checked_at"]) > as_of for f in data["fact"]):
            raise ValueError("事实核验晚于报告时间")
    payload = canonical(data)
    baseline = hashlib.sha256(
        canonical({"space": space, "sku_ids": ids, "payload": json.loads(payload)}).encode()
    ).hexdigest()
    return Snapshot(space, ids, as_of.isoformat(), read_at, payload, baseline)


def current(engine, mode, snapshot):
    """Fail closed when records disappear, a collection changes, or reading fails."""
    if snapshot.space != space_key(engine, mode):
        return False
    try:
        return (
            read_snapshot(engine, mode, snapshot.sku_ids, snapshot.as_of, check_time=False).baseline
            == snapshot.baseline
        )
    except (SQLAlchemyError, ValueError, TypeError, KeyError, AttributeError):
        return False


def references(data):
    return tuple(
        sorted(
            f"{kind}:{row[MODELS[kind][1]]}" + (f":{row['revision']}" if kind != "audit" else "")
            for kind, rows in data.items()
            for row in rows
        )
    )
