"""R1 A22–A26: reminders are derived, bounded, and preserve accounting semantics."""

from pathlib import Path

import pytest
from sqlmodel import Session

from xhs_seller.db import make_engine
from xhs_seller.models import Expense, SettlementSnapshot
from xhs_seller.seed_demo import seed
from xhs_seller.services import workbench_queries as workbench
from xhs_seller.services.imports import InputFile, commit, preflight
from xhs_seller.services.proposals import create_proposal, decide_proposal

AS_OF = "2026-11-01T12:00:00+08:00"


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "r1.db")
    seed(engine)
    root = Path(__file__).resolve().parents[1] / "examples" / "p4"
    files = [
        InputFile(kind, name, (root / name).read_bytes())
        for kind, name in (
            ("order", "orders.csv"),
            ("expense", "expenses.csv"),
            ("allocation", "expense_allocations.csv"),
            ("adjustment", "cost_adjustments.csv"),
            ("settlement", "settlements.csv"),
        )
    ]
    batch = preflight(
        engine,
        files,
        real_data=False,
        orders_are_mapped=True,
        settlements_cover_full_principal=True,
        costs_exclude_refunds=True,
    )
    assert not batch.errors
    commit(engine, batch, operator="test")
    return engine


def create(engine, name):
    return create_proposal(
        engine,
        proposal_id=name,
        action_type="reprice",
        target_type="sku",
        target_id="S-01",
        before="原状",
        after="人工建议",
        reason="合成测试",
        evidence_refs=["fixture://research"],
        estimated_cost="",
        operator="test",
        real_data=False,
    )


def test_a22_single_report_counts_dedupe_and_unknown(engine, monkeypatch):
    original = workbench.build_report
    calls = []

    def counted(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(workbench, "build_report", counted)
    report = workbench.order_alerts(engine, AS_OF)
    assert len(calls) == 1
    assert report["unknown"].total == 1
    assert [r["id"] for r in report["unknown"].items] == ["OL-04"]
    assert [r["id"] for r in report["settlement"].items] == ["OL-02", "OL-04", "OL-05"]
    assert report["settlement"].items[-1]["difference_cents"] == -50
    assert report["settlement"].items[1]["difference_cents"] is None
    with Session(engine) as session:
        session.get(SettlementSnapshot, "SET-02").actual_received_cents -= 50
        expense = session.get(Expense, "E-04-marketing")
        expense.basis, expense.amount_cents = "estimated", 700
        session.add(
            Expense(
                expense_id="E-UN",
                revision=1,
                updated_at=AS_OF,
                expense_date="2026-10-27",
                category="marketing",
                amount_cents=100,
                basis="actual",
                allocation_mode="unallocated",
                source_ref="fixture://unallocated",
            )
        )
        session.commit()
    report = workbench.order_alerts(engine, AS_OF)
    assert report["unknown"].total == 0 and report["estimated"].total == 1
    assert report["settlement"].total == 3
    assert len(report["settlement"].items[0]["reasons"]) == 2
    assert report["unallocated"].total == 1


def test_a22_proposals_bounded_and_stable(engine):
    for i in range(101):
        create(engine, f"P-{i:03}")
    pages = [workbench.pending_proposals(engine, page=i)[0] for i in (1, 2, 3)]
    assert [len(p.items) for p in pages] == [50, 50, 1]
    assert len({r.proposal_id for p in pages for r in p.items}) == 101
    decide_proposal(
        engine,
        proposal_id="P-000",
        expected_revision=1,
        decision="approved",
        note="人工核对",
        operator="test",
    )
    assert workbench.pending_proposals(engine)[0].total == 100


def test_a25_report_failure_is_not_zero(engine):
    with pytest.raises(ValueError):
        workbench.order_alerts(engine, "2020-01-01T00:00:00+08:00")
    create(engine, "P-ok")
    assert workbench.pending_proposals(engine)[0].total == 1


def test_a24_alert_pagination_is_bounded_and_clamps():
    rows = [{"id": f"OL-{i:03}"} for i in range(101)]
    pages = [workbench.paginate(rows, p) for p in (1, 2, 3, 99)]
    assert [len(p.items) for p in pages] == [50, 50, 1, 1]
    assert pages[-1].page == 3


def test_a24_workbench_reads_preserve_all_business_and_audit_rows(engine):
    from test_m1 import dump_database

    create(engine, "P-readonly")
    before = dump_database(engine)
    workbench.order_alerts(engine, AS_OF)
    workbench.pending_proposals(engine)
    workbench.inventory_counts(engine)
    assert dump_database(engine) == before
