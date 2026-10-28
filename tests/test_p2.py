import csv
import io
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from sqlmodel import Session, select

from xhs_seller.db import make_engine
from xhs_seller.models import (
    CandidateCheck,
    ProductCandidate,
    ProductResearchLink,
    ResearchNote,
    RevisionAudit,
    Sku,
    SupplierQuote,
)
from xhs_seller.seed_demo import seed
from xhs_seller.services.imports import InputFile, commit, preflight
from xhs_seller.services.scoring import rank, score

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "p2"
AS_OF = datetime.fromisoformat("2026-10-27T12:00:00+08:00")


def files():
    return [
        InputFile(kind, name, (EXAMPLES / name).read_bytes())
        for kind, name in (
            ("research", "research_notes.csv"),
            ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )
    ]


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    batch = preflight(engine, files(), real_data=False)
    assert not batch.errors
    assert batch.counts["insert"] == 29
    commit(engine, batch, operator="test")
    return engine


def report(engine, product_id="PC-01", sku_id="S-01", quote_id="Q-01A", as_of=AS_OF):
    with Session(engine) as session:
        return score(
            session.get(ProductCandidate, product_id),
            session.get(Sku, sku_id),
            session.get(SupplierQuote, quote_id),
            session.exec(select(ResearchNote)).all(),
            session.exec(select(ProductResearchLink)).all(),
            session.exec(select(CandidateCheck)).all(),
            session.exec(select(RevisionAudit)).all(),
            as_of,
            demo=True,
        )


def test_a09_reference_scores_and_coverage(engine):
    first = report(engine)
    assert first["dimensions"] == {
        "demand": 100,
        "margin": 80,
        "fulfillment": 100,
        "content": 100,
        "compliance": 100,
    }
    assert first["total"] == Decimal("95.00")
    assert first["component_points"]["fulfillment"] == {
        "sample": 40,
        "dropship": 20,
        "lead_time": 20,
        "return_terms": 20,
    }
    assert first["component_points"]["checks"]["media_rights"] == 20
    assert first["coverage"] == 100 and first["status"] == "ready_for_manual_review"
    assert first["synthetic_warning"] and len(first["evidence"]) == 3
    second = report(engine, "PC-02", "S-02", "Q-02A")
    assert second["dimensions"]["demand"] == 70
    assert second["dimensions"]["content"] is None and second["total"] is None
    assert second["coverage"] == 80 and second["status"] == "incomplete"
    assert len(second["contradiction_keys"]) == 1
    assert rank([second, first]) == [first]


def test_a10_deduplication_and_contradiction(engine):
    with Session(engine) as session:
        note = session.get(ResearchNote, "R-01")
        note.dedupe_key = "ORIGIN-02"
        session.commit()
    result = report(engine)
    assert result["dimensions"]["demand"] == 70 and result["coverage"] == 90
    with Session(engine) as session:
        link = session.get(ProductResearchLink, "L-02")
        link.relationship = "contradicts"
        session.commit()
    result = report(engine)
    assert result["dimensions"]["demand"] == 40
    assert result["conflict"] and result["status"] == "review_required"


def test_a10_expired_failure_still_blocks_until_evidenced_pass(engine):
    with (EXAMPLES / "candidate_checks.csv").open(encoding="utf-8", newline="") as stream:
        original = next(
            row for row in csv.DictReader(stream) if row["check_id"] == "C-01-sales_eligibility"
        )

    def update(revision, result, checked_at, evidence):
        row = dict(
            original,
            revision=str(revision),
            result=result,
            checked_at=checked_at,
            evidence_ref=evidence,
            updated_at=f"2026-10-27T00:0{revision}:00+00:00",
        )
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        batch = preflight(
            engine, [InputFile("check", "update.csv", buffer.getvalue().encode())], real_data=False
        )
        assert not batch.errors
        commit(engine, batch, operator="test")

    update(2, "fail", (AS_OF - timedelta(days=1)).isoformat(), "fixture://fail")
    result = report(engine)
    assert result["status"] == "blocked" and result["total"] is not None
    update(3, "fail", (AS_OF - timedelta(days=91)).isoformat(), "fixture://fail")
    result = report(engine)
    assert result["status"] == "blocked" and result["total"] is None
    update(4, "unknown", "", "")
    result = report(engine)
    assert result["status"] == "blocked" and result["blocked"][0]["revision"] == 3
    update(5, "pass", (AS_OF - timedelta(days=1)).isoformat(), "fixture://pass")
    assert report(engine)["status"] == "ready_for_manual_review"


def test_a10_future_and_old_inputs_and_synthetic_rejection(engine, tmp_path):
    with Session(engine) as session:
        sku = session.get(Sku, "S-01")
        sku.assumptions_as_of = (AS_OF + timedelta(hours=1)).isoformat()
        session.commit()
    result = report(engine)
    assert result["dimensions"]["margin"] is None and result["status"] == "incomplete"
    with Session(engine) as session:
        sku = session.get(Sku, "S-01")
        sku.assumptions_as_of = (AS_OF - timedelta(days=31)).isoformat()
        session.commit()
    assert report(engine)["dimensions"]["margin"] is None
    with Session(engine) as session:
        quote = session.get(SupplierQuote, "Q-01A")
        quote.quoted_at = (AS_OF + timedelta(hours=1)).isoformat()
        session.commit()
    result = report(engine)
    assert result["dimensions"]["fulfillment"] is None
    other = make_engine(tmp_path / "real.db")
    seed(other)
    assert any(
        error.error_code == "PRIVACY_RISK"
        for error in preflight(other, files(), real_data=True).errors
    )


def test_gate_priority_and_scenario_mismatch(engine):
    with Session(engine) as session:
        quote = session.get(SupplierQuote, "Q-01A")
        quote.min_qty = 10
        quote.dropship_supported = False
        session.commit()
    result = report(engine)
    assert result["dimensions"]["fulfillment"] == 80
    assert result["status"] == "scenario_mismatch"
    with Session(engine) as session:
        quote = session.get(SupplierQuote, "Q-01A")
        quote.min_qty = 1
        quote.dropship_supported = True
        sku = session.get(Sku, "S-01")
        sku.sale_price_cents = 1000
        session.commit()
    result = report(engine)
    assert result["dimensions"]["margin"] == 0 and result["status"] == "not_viable"
    with Session(engine) as session:
        quote = session.get(SupplierQuote, "Q-01A")
        quote.sample_result = "fail"
        sku = session.get(Sku, "S-01")
        sku.assumptions_as_of = (AS_OF - timedelta(days=31)).isoformat()
        session.commit()
    assert report(engine)["status"] == "blocked"


def test_evidence_validity_and_current_revision_not_backdated(engine):
    with Session(engine) as session:
        note = session.get(ResearchNote, "R-01")
        note.captured_at = (AS_OF + timedelta(hours=1)).isoformat()
        session.commit()
    result = report(engine)
    assert result["dimensions"]["demand"] == 70 and result["coverage"] == 90
    assert (
        next(item for item in result["evidence"] if item["research_id"] == "R-01")[
            "validity_reason"
        ]
        == "采集时间在报告时间之后"
    )
    with Session(engine) as session:
        note = session.get(ResearchNote, "R-01")
        note.captured_at = (AS_OF - timedelta(days=90, seconds=1)).isoformat()
        session.commit()
    assert report(engine)["dimensions"]["demand"] == 70
    with Session(engine) as session:
        note = session.get(ResearchNote, "R-01")
        note.updated_at = (AS_OF + timedelta(hours=1)).isoformat()
        session.commit()
    with pytest.raises(ValueError, match="历史快照"):
        report(engine)
    with pytest.raises(ValueError, match="时区"):
        report(engine, as_of="2026-10-27T12:00:00")


def test_p2_real_synthetic_without_fixture_reference_is_rejected(engine):
    with (EXAMPLES / "research_notes.csv").open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    row["source_ref"] = "https://example.test/research"
    row["evidence_ref"] = "https://example.test/evidence"
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    preview = preflight(
        engine, [InputFile("research", "research.csv", buffer.getvalue().encode())], real_data=True
    )
    assert any(
        item.field == "permission_basis" and item.error_code == "PRIVACY_RISK"
        for item in preview.errors
    )


def test_p2_relationship_uniqueness_and_verification_requirements(engine):
    with (EXAMPLES / "product_research_links.csv").open(encoding="utf-8", newline="") as stream:
        link = next(csv.DictReader(stream))
    link["link_id"] = "L-NEW"
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(link))
    writer.writeheader()
    writer.writerow(link)
    preview = preflight(
        engine, [InputFile("link", "link.csv", buf.getvalue().encode())], real_data=False
    )
    assert any(item.error_code == "BUSINESS_KEY_CONFLICT" for item in preview.errors)
    with (EXAMPLES / "candidate_checks.csv").open(encoding="utf-8", newline="") as stream:
        check = next(csv.DictReader(stream))
    check["evidence_ref"] = ""
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(check))
    writer.writeheader()
    writer.writerow(check)
    preview = preflight(
        engine, [InputFile("check", "check.csv", buf.getvalue().encode())], real_data=False
    )
    assert any(
        item.field == "evidence_ref" and item.error_code == "TYPE_ERROR" for item in preview.errors
    )


def test_historical_sample_failure_needs_new_pass(engine):
    sample_file = EXAMPLES.parent / "p1" / "supplier_quotes.csv"
    with sample_file.open(encoding="utf-8", newline="") as stream:
        original = next(row for row in csv.DictReader(stream) if row["quote_id"] == "Q-01A")

    def update(revision, sample_result, quoted_at, evidence):
        row = dict(
            original,
            revision=str(revision),
            sample_result=sample_result,
            quoted_at=quoted_at,
            sample_evidence_ref=evidence,
            updated_at=f"2026-10-27T0{revision - 1}:00:00+00:00",
        )
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        preview = preflight(
            engine, [InputFile("quote", "q.csv", buf.getvalue().encode())], real_data=False
        )
        assert not preview.errors
        commit(engine, preview, operator="test")

    update(2, "fail", (AS_OF - timedelta(days=31)).isoformat(), "fixture://fail")
    assert report(engine)["status"] == "blocked"
    update(3, "unknown", (AS_OF - timedelta(days=1)).isoformat(), "")
    result = report(engine)
    assert result["status"] == "blocked" and result["blocked"][0]["revision"] == 2
    update(4, "pass", (AS_OF - timedelta(days=1)).isoformat(), "fixture://pass")
    assert report(engine)["status"] == "ready_for_manual_review"


def test_conflicting_evidence_remains_in_manual_review_group(engine):
    with Session(engine) as session:
        check = session.get(CandidateCheck, "C-02-media_rights")
        check.result = "pass"
        check.checked_at = (AS_OF - timedelta(days=1)).isoformat()
        check.evidence_ref = "fixture://media"
        session.commit()
    result = report(engine, "PC-02", "S-02", "Q-02A")
    assert result["total"] is not None and result["status"] == "review_required"
    assert result not in rank([report(engine), result])


def test_p2_bad_cross_file_reference_rejects_entire_batch(engine):
    with (EXAMPLES / "product_research_links.csv").open(encoding="utf-8", newline="") as stream:
        row = next(csv.DictReader(stream))
    row["link_id"] = "L-NOT-FOUND"
    row["research_id"] = "R-NOT-FOUND"
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    preview = preflight(
        engine, [InputFile("link", "link.csv", buf.getvalue().encode())], real_data=False
    )
    assert any(issue.error_code == "FK_NOT_FOUND" for issue in preview.errors)
    with pytest.raises(ValueError, match="预检错误"):
        commit(engine, preview, operator="test")
    with Session(engine) as session:
        assert session.get(ProductResearchLink, "L-NOT-FOUND") is None


@pytest.mark.parametrize(
    ("cost", "expected"),
    [
        (6500, 100),
        (6501, 80),
        (7500, 80),
        (7501, 60),
        (8500, 60),
        (8501, 30),
        (9999, 30),
        (10000, 0),
        (10001, 0),
    ],
)
def test_margin_thresholds_are_exact(engine, cost, expected):
    with Session(engine) as session:
        sku = session.get(Sku, "S-01")
        quote = session.get(SupplierQuote, "Q-01A")
        sku.sale_price_cents = 10000
        sku.packaging_per_order_cents = 0
        sku.platform_fee_per_order_cents = 0
        sku.marketing_per_order_cents = 0
        sku.after_sales_loss_per_order_cents = 0
        quote.unit_cost_cents = cost
        quote.shipping_per_order_cents = 0
        session.commit()
    assert report(engine)["dimensions"]["margin"] == expected


@pytest.mark.parametrize(
    ("product_status", "sku_status", "reason"),
    [
        ("archived", "draft", "product_archived"),
        ("rejected", "draft", "product_rejected"),
        ("researching", "archived", "sku_archived"),
    ],
)
def test_inactive_candidates_keep_score_but_leave_review_ranking(
    engine, product_status, sku_status, reason
):
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        sku = session.get(Sku, "S-01")
        product.status, sku.status = product_status, sku_status
        session.commit()
    result = report(engine)
    assert result["rule_version"] == "score-v0.3"
    assert result["status"] == "inactive"
    assert reason in result["inactive_reasons"]
    assert result["total"] == Decimal("95.00") and result["coverage"] == 100
    assert not rank([result])


def test_blocked_inactive_and_restored_status_are_visible(engine):
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        product.status = "archived"
        quote = session.get(SupplierQuote, "Q-01A")
        quote.sample_result = "fail"
        session.commit()
    result = report(engine)
    assert result["status"] == "blocked"
    assert "product_archived" in result["inactive_reasons"]
    assert result["blocked"] and not rank([result])
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        product.status = "researching"
        session.commit()
    assert report(engine)["status"] == "blocked"
    with Session(engine) as session:
        quote = session.get(SupplierQuote, "Q-01A")
        quote.sample_result = "pass"
        session.commit()
    result = report(engine)
    assert result["status"] == "ready_for_manual_review"
    assert not result["inactive_reasons"] and rank([result]) == [result]
