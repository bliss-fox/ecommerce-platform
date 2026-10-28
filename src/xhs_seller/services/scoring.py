"""Deterministic, traceable P2 scoring for current input snapshots."""

import json
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from xhs_seller.services.economics import calculate

RULE_VERSION = "score-v0.3"
CHECK_TYPES = ("visual_demo", "fact_sheet", "media_rights", "sales_eligibility", "claims_review")
WEIGHTS = {
    "demand": Decimal("0.25"),
    "margin": Decimal("0.25"),
    "fulfillment": Decimal("0.20"),
    "content": Decimal("0.15"),
    "compliance": Decimal("0.15"),
}


def timestamp(value):
    value = datetime.fromisoformat(value) if isinstance(value, str) else value
    if value.utcoffset() is None:
        raise ValueError("报告时间必须带时区")
    return value


def fresh(value, as_of, days):
    elapsed = as_of - timestamp(value)
    return timedelta(0) <= elapsed <= timedelta(days=days)


def unresolved_failure(entity, current, history, as_of):
    id_field, result_field, time_field, evidence_field = (
        ("quote_id", "sample_result", "quoted_at", "sample_evidence_ref")
        if entity == "quote"
        else ("check_id", "result", "checked_at", "evidence_ref")
    )
    records = [current.model_dump()]
    for audit in history:
        if audit.entity_type == entity and audit.record_id == getattr(current, id_field):
            if audit.old_json is not None:
                records.append(json.loads(audit.old_json))
            records.append(json.loads(audit.new_json))
    eligible = [
        row
        for row in records
        if row[result_field] in {"pass", "fail"}
        and row[time_field] is not None
        and row[evidence_field] is not None
        and timestamp(row["updated_at"]) <= as_of
        and timestamp(row[time_field]) <= as_of
    ]
    if not eligible:
        return None
    latest = max(eligible, key=lambda row: row["revision"])
    if latest[result_field] == "fail":
        return {
            "revision": latest["revision"],
            "evidence_ref": latest[evidence_field],
            "observed_at": latest[time_field],
        }
    return None


def score(product, sku, quote, notes, links, checks, history, as_of, *, demo):
    as_of = timestamp(as_of)
    if sku.product_id != product.product_id or quote.product_id != product.product_id:
        raise ValueError("SKU 和报价必须属于所选候选")
    related_links = [link for link in links if link.product_id == product.product_id]
    note_by_id = {note.research_id: note for note in notes}
    related_notes = [note_by_id[link.research_id] for link in related_links]
    relevant_checks = [check for check in checks if check.product_id == product.product_id]
    if len({check.check_type for check in relevant_checks}) != len(relevant_checks):
        raise ValueError("候选核验类型重复")
    snapshots = [product, sku, quote, *related_links, *related_notes, *relevant_checks]
    if any(timestamp(item.updated_at) > as_of for item in snapshots):
        raise ValueError("当前记录修订晚于报告时间；目前不支持历史快照回放")
    if not demo and any(note.permission_basis == "synthetic" for note in related_notes):
        raise ValueError("真实库不能使用合成研究")
    evidence = []
    keys = {}
    for link in related_links:
        note = note_by_id[link.research_id]
        if not link.active:
            reason = "关联已停用"
        elif note.evidence_status != "verified":
            reason = "研究尚未核验或已拒绝"
        elif note.permission_basis == "unknown" or note.evidence_ref is None:
            reason = "缺少许可或证据索引"
        elif timestamp(note.captured_at) > as_of:
            reason = "采集时间在报告时间之后"
        elif not fresh(note.captured_at, as_of, 90):
            reason = "研究证据超过 90 天"
        else:
            reason = "有效"
        valid = reason == "有效"
        evidence.append(
            {
                "link_id": link.link_id,
                "link_revision": link.revision,
                "research_id": note.research_id,
                "research_revision": note.revision,
                "relationship": link.relationship,
                "active": link.active,
                "valid": valid,
                "validity_reason": reason,
                "dedupe_key": note.dedupe_key,
                "captured_at": note.captured_at,
                "permission_basis": note.permission_basis,
                "evidence_status": note.evidence_status,
                "source_ref": note.source_ref,
                "evidence_ref": note.evidence_ref,
                "summary": note.evidence_summary,
                "keyword": note.keyword,
                "pain_point": note.pain_point,
                "published_at": note.published_at,
            }
        )
        if valid:
            keys.setdefault(note.dedupe_key, set()).add(link.relationship)
    supports = sorted(
        k for k, values in keys.items() if "supports" in values and "contradicts" not in values
    )
    contradicts = sorted(k for k, values in keys.items() if "contradicts" in values)
    n = len(supports)
    demand = 100 if n >= 3 else 70 if n == 2 else 40 if n == 1 else None
    conflict = bool(contradicts and len(contradicts) >= n)
    if demand is not None and conflict:
        demand = min(demand, 40)

    inputs = {
        "unit_cost": quote.unit_cost_cents,
        "shipping": quote.shipping_per_order_cents,
        "packaging": sku.packaging_per_order_cents,
        "platform": sku.platform_fee_per_order_cents,
        "marketing": sku.marketing_per_order_cents,
        "after_sales": sku.after_sales_loss_per_order_cents,
    }
    economics = calculate(sku.sale_price_cents, inputs)
    quote_valid = fresh(quote.quoted_at, as_of, 30)
    assumptions_valid = fresh(sku.assumptions_as_of, as_of, 30)
    economic_valid = quote_valid and assumptions_valid and economics.margin is not None
    margin = None
    if economic_valid:
        ratio = economics.margin
        margin = (
            100
            if ratio >= Decimal("0.35")
            else 80
            if ratio >= Decimal("0.25")
            else 60
            if ratio >= Decimal("0.15")
            else 30
            if ratio > 0
            else 0
        )
    fulfillment_complete = (
        quote_valid
        and quote.sample_result in {"pass", "fail"}
        and quote.sample_evidence_ref is not None
        and quote.dropship_supported is not None
        and quote.lead_time_hours is not None
        and bool(quote.return_terms)
    )
    fulfillment = None
    if fulfillment_complete:
        fulfillment = (
            (40 if quote.sample_result == "pass" else 0)
            + (20 if quote.dropship_supported else 0)
            + (20 if quote.lead_time_hours <= 48 else 10 if quote.lead_time_hours <= 72 else 0)
            + 20
        )
    fulfillment_points = {
        "sample": (40 if quote.sample_result == "pass" else 0)
        if quote_valid and quote.sample_result in {"pass", "fail"} and quote.sample_evidence_ref
        else None,
        "dropship": (20 if quote.dropship_supported else 0)
        if quote_valid and quote.dropship_supported is not None
        else None,
        "lead_time": (
            20 if quote.lead_time_hours <= 48 else 10 if quote.lead_time_hours <= 72 else 0
        )
        if quote_valid and quote.lead_time_hours is not None
        else None,
        "return_terms": 20 if quote_valid and quote.return_terms else None,
    }
    by_type = {check.check_type: check for check in relevant_checks}
    valid_checks = {}
    check_reasons = {}
    for kind in CHECK_TYPES:
        item = by_type.get(kind)
        if item is None:
            reason = "未记录"
        elif item.result == "unknown":
            reason = "结果未知"
        elif item.checked_at is None or item.evidence_ref is None:
            raise ValueError(f"{kind} 核验结论缺少时间或依据")
        elif timestamp(item.checked_at) > as_of:
            reason = "核验时间在报告时间之后"
        elif not fresh(item.checked_at, as_of, 90):
            reason = "核验超过 90 天"
        else:
            reason = "有效"
        valid_checks[kind] = reason == "有效"
        check_reasons[kind] = reason

    def check_score(weights):
        if not all(valid_checks[kind] for kind in weights):
            return None
        return sum(points for kind, points in weights.items() if by_type[kind].result == "pass")

    check_weights = {
        "visual_demo": 40,
        "fact_sheet": 40,
        "media_rights": 20,
        "sales_eligibility": 50,
        "claims_review": 50,
    }
    check_points = {
        kind: (points if by_type[kind].result == "pass" else 0) if valid_checks[kind] else None
        for kind, points in check_weights.items()
    }
    content = check_score({"visual_demo": 40, "fact_sheet": 40, "media_rights": 20})
    compliance = check_score({"sales_eligibility": 50, "claims_review": 50})
    dimensions = {
        "demand": demand,
        "margin": margin,
        "fulfillment": fulfillment,
        "content": content,
        "compliance": compliance,
    }
    contributions = {
        key: Decimal(value) * WEIGHTS[key] if value is not None else None
        for key, value in dimensions.items()
    }
    subtotal = sum((value for value in contributions.values() if value is not None), Decimal(0))
    total = (
        subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if all(v is not None for v in dimensions.values())
        else None
    )
    slots = {
        "demand_1": n >= 1,
        "demand_2": n >= 2,
        "demand_3": n >= 3,
        "economics": economic_valid,
        "fulfillment": fulfillment_complete,
        **{kind: valid_checks[kind] for kind in CHECK_TYPES},
    }
    coverage = sum(slots.values()) * 10
    blocked = []
    failure = unresolved_failure("quote", quote, history, as_of)
    if failure is not None:
        blocked.append({"type": "sample", **failure})
    for kind in ("sales_eligibility", "claims_review", "media_rights"):
        item = by_type.get(kind)
        if item is not None:
            failure = unresolved_failure("check", item, history, as_of)
            if failure is not None:
                blocked.append({"type": kind, **failure})
    missing = [name for name, value in dimensions.items() if value is None]
    if quote.min_qty is None or quote.dropship_supported is None:
        missing.append("scenario_eligibility")
    inactive_reasons = []
    if product.status in {"archived", "rejected"}:
        inactive_reasons.append(f"product_{product.status}")
    if sku.status == "archived":
        inactive_reasons.append("sku_archived")
    if blocked:
        status = "blocked"
    elif inactive_reasons:
        status = "inactive"
    elif missing:
        status = "incomplete"
    elif quote.min_qty > 1 or not quote.dropship_supported:
        status = "scenario_mismatch"
    elif economics.contribution <= 0:
        status = "not_viable"
    elif contradicts:
        status = "review_required"
    else:
        status = "ready_for_manual_review"
    check_details = {
        kind: {
            "id": by_type[kind].check_id,
            "revision": by_type[kind].revision,
            "result": by_type[kind].result,
            "checked_at": by_type[kind].checked_at,
            "evidence_ref": by_type[kind].evidence_ref,
            "note": by_type[kind].note,
            "valid": valid_checks[kind],
            "validity_reason": check_reasons[kind],
        }
        if kind in by_type
        else None
        for kind in CHECK_TYPES
    }
    return {
        "rule_version": RULE_VERSION,
        "as_of": as_of.isoformat(),
        "product_id": product.product_id,
        "product_revision": product.revision,
        "product_status": product.status,
        "sku_id": sku.sku_id,
        "sku_revision": sku.revision,
        "sku_status": sku.status,
        "quote_id": quote.quote_id,
        "quote_revision": quote.revision,
        "synthetic_warning": demo,
        "dimensions": dimensions,
        "component_points": {"fulfillment": fulfillment_points, "checks": check_points},
        "contributions": contributions,
        "subtotal": subtotal,
        "total": total,
        "coverage": coverage,
        "slots": slots,
        "status": status,
        "inactive_reasons": inactive_reasons,
        "blocked": blocked,
        "missing": missing,
        "conflict": conflict,
        "support_keys": supports,
        "contradiction_keys": contradicts,
        "evidence": evidence,
        "checks": check_details,
        "check_reasons": check_reasons,
        "quote_valid": quote_valid,
        "assumptions_valid": assumptions_valid,
        "economics": {
            "sale_price_cents": sku.sale_price_cents,
            "cost_inputs_cents": inputs,
            "assumptions_as_of": sku.assumptions_as_of,
            "assumption_ref": sku.assumption_ref,
            "quote_source_ref": quote.source_ref,
            "known_cost": economics.known_cost,
            "missing": economics.missing,
            "contribution": economics.contribution,
            "margin": economics.margin,
            "break_even_cac": economics.break_even_cac,
        },
        "fulfillment_inputs": {
            "sample_result": quote.sample_result,
            "sample_evidence_ref": quote.sample_evidence_ref,
            "quoted_at": quote.quoted_at,
            "min_qty": quote.min_qty,
            "dropship_supported": quote.dropship_supported,
            "lead_time_hours": quote.lead_time_hours,
            "return_terms": quote.return_terms,
        },
    }


def rank(reports):
    return sorted(
        (report for report in reports if report["status"] == "ready_for_manual_review"),
        key=lambda report: (
            -report["total"],
            -report["coverage"],
            report["product_id"],
            report["sku_id"],
        ),
    )
