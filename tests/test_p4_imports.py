import csv
import io
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from xhs_seller.db import make_engine
from xhs_seller.models import Expense, ImportBatch, OrderLine
from xhs_seller.seed_demo import seed
from xhs_seller.services.imports import BaselineChangedError, InputFile, commit, preflight

BASE = Path(__file__).resolve().parents[1] / "examples"
FILES = {
    "order": "orders.csv",
    "expense": "expenses.csv",
    "allocation": "expense_allocations.csv",
    "adjustment": "cost_adjustments.csv",
    "settlement": "settlements.csv",
}


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "p4.db")
    seed(engine)
    return engine


def files():
    return [
        InputFile(kind, name, (BASE / "p4" / name).read_bytes()) for kind, name in FILES.items()
    ]


def one(kind, name, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return InputFile(kind, name, output.getvalue().encode())


def read(name):
    with (BASE / "p4" / name).open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def check(engine, inputs):
    return preflight(
        engine,
        inputs,
        real_data=False,
        orders_are_mapped=True,
        settlements_cover_full_principal=True,
        costs_exclude_refunds=True,
    )


def codes(preview):
    return {item.error_code for item in preview.errors}


def load(engine):
    result = check(engine, files())
    assert not result.errors, result.errors
    commit(engine, result, operator="local")


def test_baseline_idempotency_and_revisions(engine):
    load(engine)
    preview = check(engine, files())
    assert not preview.errors, preview.errors
    assert preview.counts["noop"] == sum(len(read(name)) for name in FILES.values())
    for kind, name in [
        ("expense", "expense_actual_revision2.csv"),
        ("settlement", "settlement_after_refund_revision2.csv"),
    ]:
        source = InputFile(kind, name, (BASE / "import_cases" / name).read_bytes())
        preview = check(engine, [source])
        assert not preview.errors, preview.errors
        assert preview.counts["update"] == 1
        commit(engine, preview, operator="local")
        assert check(engine, [source]).counts["noop"] == 1
    assert "STALE_REVISION" in codes(check(engine, files()))
    with Session(engine) as session:
        assert session.get(Expense, "E-04-marketing").amount_cents == 700
        assert len(session.exec(select(OrderLine)).all()) == 5


def test_cross_record_rejections_and_rollback(engine):
    inputs = files()
    pool = read("expense_allocations.csv")
    pool[0]["amount"] = "9.99"
    inputs[2] = one("allocation", "expense_allocations.csv", pool)
    assert "ALLOCATION_MISMATCH" in codes(check(engine, inputs))
    with Session(engine) as session:
        assert not session.exec(select(OrderLine)).all()
    inputs = files()
    row = read("cost_adjustments.csv")[0]
    row["amount"] = "-24.01"
    inputs[3] = one("adjustment", "cost_adjustments.csv", [row])
    assert "RECOVERY_LIMIT" in codes(check(engine, inputs))
    inputs = files()
    expenses = read("expenses.csv")
    extra = expenses[0].copy()
    extra["expense_id"] = "E-DUP"
    extra["source_ref"] = "fixture://duplicate"
    expenses.append(extra)
    inputs[1] = one("expense", "expenses.csv", expenses)
    assert "DOUBLE_COST_SOURCE" in codes(check(engine, inputs))
    extra["source_ref"] = expenses[0]["source_ref"]
    assert "BUSINESS_KEY_CONFLICT" in codes(
        check(engine, [*inputs[:1], one("expense", "expenses.csv", expenses), *inputs[2:]])
    )


def test_large_invalid_order_batch_reports_each_source_row_without_committing(engine):
    template = read("orders.csv")[0]
    records = [
        dict(
            template,
            order_line_id=f"OL-BULK-{i:04}",
            order_id=f"ORDER-BULK-{i:04}",
            sku_id="SKU-MISSING",
        )
        for i in range(512)
    ]
    preview = check(engine, [one("order", "bulk.csv", records)])
    errors = [item for item in preview.errors if item.error_code == "FK_NOT_FOUND"]
    assert len(errors) == len(records)
    assert {(item.file, item.row_number, item.record_id) for item in errors} == {
        ("1. bulk.csv", i + 2, f"OL-BULK-{i:04}") for i in range(len(records))
    }
    assert preview.counts == {"insert": 0, "update": 0, "noop": 0, "rejected": 512}
    with pytest.raises(ValueError, match="预检错误"):
        commit(engine, preview, operator="local")
    with Session(engine) as session:
        assert not session.exec(select(OrderLine)).all()


def test_baseline_change_and_database_failure_are_atomic(engine):
    preview = check(engine, files())
    assert not preview.errors, preview.errors
    with Session(engine) as session:
        from xhs_seller.models import Sku

        session.get(Sku, "S-01").title = "changed"
        session.commit()
    with pytest.raises(BaselineChangedError):
        commit(engine, preview, operator="local")
    preview = check(engine, files())

    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO expense "):
            raise RuntimeError("simulated failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            commit(engine, preview, operator="local")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with Session(engine) as session:
        assert not session.exec(select(OrderLine)).all()
        assert not session.exec(select(ImportBatch)).all()


def test_report_uses_current_snapshots_and_separates_metrics(engine):
    from xhs_seller.services.p4_report import build_report

    load(engine)
    report = build_report(engine, "2026-10-27T12:00:00+08:00")
    rows = {row["order_line_id"]: row for row in report["rows"]}
    assert [rows[k]["cost"].contribution_cents for k in sorted(rows)] == [
        5300,
        3000,
        -1800,
        None,
        2800,
    ]
    assert sum(row["cost"].net_sales_cents for row in rows.values()) == 35000
    assert (
        sum(
            row["cost"].contribution_cents
            for row in rows.values()
            if row["cost"].contribution_cents is not None
        )
        == 9300
    )
    assert (
        sum(
            row["cost"].variable_cost_cents
            for row in rows.values()
            if row["cost"].variable_cost_cents is not None
        )
        == 16700
    )
    assert (
        sum(row["settlement"].actual_cents for row in rows.values() if row["settlement"]) == 26550
    )
    assert rows["OL-02"]["settlement"].refund_not_covered_cents == 2000
    assert rows["OL-04"]["settlement"] is None
    assert rows["OL-05"]["settlement"].difference_cents == -50
    assert rows["OL-01"]["revisions"]["expense"]["E-MARKETING-POOL"] == 1
    with pytest.raises(ValueError, match="之后的修订"):
        build_report(engine, "2026-10-27T07:59:59+08:00")
    for kind, name in [
        ("expense", "expense_actual_revision2.csv"),
        ("settlement", "settlement_after_refund_revision2.csv"),
    ]:
        item = InputFile(kind, name, (BASE / "import_cases" / name).read_bytes())
        preview = check(engine, [item])
        assert not preview.errors, preview.errors
        commit(engine, preview, operator="local")
    updated = build_report(engine, "2026-10-28T12:00:00+08:00")
    assert sum(row["cost"].contribution_cents for row in updated["rows"]) == 13450
    assert sum(row["cost"].variable_cost_cents for row in updated["rows"]) == 21550
    assert (
        sum(row["settlement"].actual_cents for row in updated["rows"] if row["settlement"]) == 24550
    )
    row2 = next(row for row in updated["rows"] if row["order_line_id"] == "OL-02")
    assert row2["revisions"]["settlement"]["SET-02"] == 2


def test_source_scope_requires_explicit_confirmation(engine):
    result = preflight(engine, files(), real_data=False)
    assert "SCHEMA_ERROR" in codes(result)
    assert any("本金" in e.message for e in result.errors)
    assert any("映射" in e.message for e in result.errors)
    result = check(engine, files())
    assert not result.errors
    metadata = {item["kind"]: item for item in result.files}
    assert metadata["order"]["order_mapping_confirmed"] is True
    assert metadata["settlement"]["full_principal_confirmed"] is True


def test_order_keys_ownership_and_recovery_revalidated_on_parent_revision(engine):
    load(engine)
    order = read("orders.csv")[2]
    order["order_line_id"] = "OL-OTHER"
    assert "BUSINESS_KEY_CONFLICT" in codes(check(engine, [one("order", "orders.csv", [order])]))
    order = read("orders.csv")[2]
    order["revision"] = "2"
    order["updated_at"] = "2026-10-28T08:00:00+08:00"
    order["returned_units"] = "1"
    assert "RECOVERY_LIMIT" in codes(check(engine, [one("order", "orders.csv", [order])]))
    order["returned_units"] = "2"
    order["sku_id"] = "S-01"
    assert "BUSINESS_KEY_CONFLICT" in codes(check(engine, [one("order", "orders.csv", [order])]))


def test_pool_and_settlement_updates_validate_dependents(engine):
    load(engine)
    expense = next(row for row in read("expenses.csv") if row["expense_id"] == "E-MARKETING-POOL")
    expense["revision"] = "2"
    expense["updated_at"] = "2026-10-28T08:00:00+08:00"
    expense["amount"] = "22.01"
    assert "ALLOCATION_MISMATCH" in codes(
        check(engine, [one("expense", "expenses.csv", [expense])])
    )
    settlement = next(row for row in read("settlements.csv") if row["settlement_id"] == "SET-02")
    settlement["revision"] = "2"
    settlement["updated_at"] = "2026-10-28T08:00:00+08:00"
    settlement["cutoff_at"] = "2026-10-22T18:00:00+08:00"
    assert "TIME_ORDER_ERROR" in codes(
        check(engine, [one("settlement", "settlements.csv", [settlement])])
    )
    settlement["cutoff_at"] = "2026-10-27T18:00:00+08:00"
    settlement["covered_refunded_amount"] = "20.01"
    assert "TYPE_ERROR" in codes(
        check(engine, [one("settlement", "settlements.csv", [settlement])])
    )


def test_real_space_rejects_synthetic_p4_sources(engine):
    result = preflight(
        engine,
        files(),
        real_data=True,
        orders_are_mapped=True,
        settlements_cover_full_principal=True,
        costs_exclude_refunds=True,
    )
    assert "PRIVACY_RISK" in codes(result)


def test_p4_deduplication_estimates_and_cost_source_conflicts(engine):
    inputs = files()
    adj = read("cost_adjustments.csv")[0]
    duplicate = check(
        engine, [*inputs[:3], one("adjustment", "cost_adjustments.csv", [adj, adj]), *inputs[4:]]
    )
    assert "DUPLICATE_ID_IN_BATCH" in codes(duplicate)
    allocation = read("expense_allocations.csv")
    allocation[0]["amount"] = "10.01"
    assert "ALLOCATION_MISMATCH" in codes(
        check(
            engine,
            [*inputs[:2], one("allocation", "expense_allocations.csv", allocation), *inputs[3:]],
        )
    )
    expense = read("expenses.csv")
    extra = next(row.copy() for row in expense if row["expense_id"] == "E-01-goods")
    extra.update(
        expense_id="E-MARK-DIRECT",
        category="marketing",
        source_ref="fixture://expense/extra-marketing",
        amount="10.00",
    )
    assert "DOUBLE_COST_SOURCE" in codes(
        check(engine, [inputs[0], one("expense", "expenses.csv", [*expense, extra]), *inputs[2:]])
    )
    adj["source_ref"] = expense[0]["source_ref"]
    assert "BUSINESS_KEY_CONFLICT" in codes(
        check(engine, [*inputs[:3], one("adjustment", "cost_adjustments.csv", [adj]), *inputs[4:]])
    )


def test_estimated_revisions_do_not_become_actual_profit(engine):
    from xhs_seller.services.p4_report import build_report

    load(engine)
    row = next(row for row in read("expenses.csv") if row["expense_id"] == "E-04-marketing")
    row.update(
        revision="2", updated_at="2026-10-28T08:00:00+08:00", amount="7.00", basis="estimated"
    )
    preview = check(engine, [one("expense", "expenses.csv", [row])])
    assert not preview.errors
    commit(engine, preview, operator="local")
    report = build_report(engine, "2026-10-28T12:00:00+08:00")
    item = next(row for row in report["rows"] if row["order_line_id"] == "OL-04")
    assert item["cost"].contribution_cents == 4150
    assert item["cost"].status == "estimated"
    assert item["revisions"]["expense"]["E-04-marketing"] == 2


def test_report_coverage_and_exports_keep_sources_and_unknowns(engine):
    from xhs_seller.services.p4_report import build_report
    from xhs_seller.ui_p4 import csv_bytes, report_rows

    load(engine)
    expense = read("expenses.csv")[0].copy()
    expense.update(
        expense_id="E-UNALLOCATED",
        category="marketing",
        amount="5.00",
        allocation_mode="unallocated",
        order_line_id="",
        source_ref="fixture://expense/unallocated",
    )
    overhead = expense.copy()
    overhead.update(
        expense_id="E-OVERHEAD",
        category="overhead",
        allocation_mode="overhead",
        source_ref="fixture://expense/overhead",
    )
    preview = check(engine, [one("expense", "expenses.csv", [expense, overhead])])
    assert not preview.errors
    commit(engine, preview, operator="local")
    report = build_report(engine, "2026-10-27T12:00:00+08:00")
    assert report["unallocated"][0]["amount_cents"] == 500
    assert report["overhead"][0]["amount_cents"] == 500
    assert len([e for e in report["assigned"] if e["id"] == "E-MARKETING-POOL"]) == 1
    rows = report_rows(report)
    unknown = next(row for row in rows if row["order_line_id"] == "OL-04")
    assert unknown["contribution_cny"] == "未知"
    assert unknown["actual_received_cny"] == "未记录结算"
    assert "marketing" in unknown["missing_categories"]
    assert "E-04-marketing" in unknown["source_revisions"]
    exported = csv_bytes(rows, list(rows[0])).decode("utf-8-sig")
    assert "2026-10-27T12:00:00+08:00" in exported
    assert "未记录结算" in exported
    assert "cost_marketing_basis" in exported
    assert (
        next(row for row in rows if row["order_line_id"] == "OL-05")["reconciliation_status"]
        == "待核对"
    )
    assert (
        next(row for row in rows if row["order_line_id"] == "OL-02")["reconciliation_status"]
        == "已对平，退款待反映"
    )


def test_sku_summary_keeps_unknown_and_estimated_orders_distinct(engine):
    from xhs_seller.services.p4_report import build_report
    from xhs_seller.ui_p4 import sku_report_rows

    load(engine)
    report = build_report(engine, "2026-10-27T12:00:00+08:00")
    summaries = {row["sku_id"]: row for row in report["sku_summaries"]}
    assert summaries["S-01"]["order_line_ids"] == ["OL-01", "OL-05"]
    assert summaries["S-01"]["net_sales_cents"] == 18000
    assert summaries["S-01"]["variable_cost_cents"] == 9900
    assert summaries["S-01"]["contribution_cents"] == 8100
    assert summaries["S-02"]["contribution_cents"] == 1200
    assert summaries["S-03"]["status"] == "unknown"
    assert summaries["S-03"]["contribution_cents"] is None

    order = read("orders.csv")[0]
    order.update(order_line_id="OL-NEW", order_id="ORDER-NEW", quantity="1", paid_amount="10.00")
    estimate = next(row for row in read("expenses.csv") if row["expense_id"] == "E-05-goods")
    estimate.update(revision="2", updated_at="2026-10-27T10:00:00+08:00", basis="estimated")
    unallocated = estimate.copy()
    unallocated.update(
        expense_id="E-UNALLOCATED-NEW",
        revision="1",
        category="marketing",
        allocation_mode="unallocated",
        order_line_id="",
        source_ref="fixture://expense/unallocated-new",
    )
    preview = check(
        engine,
        [
            one("order", "orders.csv", [order]),
            one("expense", "expenses.csv", [estimate, unallocated]),
        ],
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    report = build_report(engine, "2026-10-27T12:00:00+08:00")
    sku = next(row for row in report["sku_summaries"] if row["sku_id"] == "S-01")
    assert sku["net_sales_cents"] == 19000
    assert sku["status"] == "unknown"
    assert sku["variable_cost_cents"] is None and sku["contribution_cents"] is None
    assert (sku["actual_count"], sku["actual_contribution_cents"]) == (1, 5300)
    assert (sku["estimated_count"], sku["estimated_contribution_cents"]) == (1, 2800)
    assert sku["unknown_count"] == 1
    exported = next(row for row in sku_report_rows(report) if row["sku_id"] == "S-01")
    assert exported["contribution_cny"] == "未知"
    assert exported["unallocated_expenses"] == "有，经营结果不完整"
    assert "OL-NEW" in exported["order_line_ids"]


def test_settlement_summary_is_unknown_when_no_settlement_is_recorded(engine):
    from xhs_seller.services.p4_report import build_report

    preview = check(engine, [one("order", "orders.csv", [read("orders.csv")[0]])])
    assert not preview.errors
    commit(engine, preview, operator="local")
    report = build_report(engine, "2026-10-27T12:00:00+08:00")
    assert len(report["rows"]) == 1
    assert report["rows"][0]["settlement"] is None
    assert report["settlement_summary"] is None


def test_pool_and_allocations_change_together(engine):
    from xhs_seller.services.p4_report import build_report

    load(engine)
    pool = next(row for row in read("expenses.csv") if row["expense_id"] == "E-MARKETING-POOL")
    pool.update(revision="2", updated_at="2026-10-28T08:00:00+08:00", amount="22.01")
    allocation = read("expense_allocations.csv")[0]
    allocation.update(revision="2", updated_at="2026-10-28T08:00:00+08:00", amount="10.01")
    preview = check(
        engine,
        [
            one("expense", "expenses.csv", [pool]),
            one("allocation", "allocations.csv", [allocation]),
        ],
    )
    assert not preview.errors, preview.errors
    commit(engine, preview, operator="local")
    report = build_report(engine, "2026-10-28T12:00:00+08:00")
    row = next(item for item in report["rows"] if item["order_line_id"] == "OL-01")
    assert row["cost"].contribution_cents == 5299
    assert row["revisions"]["expense"]["E-MARKETING-POOL"] == 2
    assert row["revisions"]["allocation"]["AL-01"] == 2


def test_report_keeps_pool_adjustments_bound_to_their_order(engine):
    from xhs_seller.services.p4_report import build_report

    load(engine)
    adjustment = read("cost_adjustments.csv")[0]
    adjustment.update(
        adjustment_id="ADJ-POOL-01",
        expense_id="E-MARKETING-POOL",
        order_line_id="OL-01",
        kind="correction",
        amount="1.00",
        basis="estimated",
        recovered_units="",
        source_ref="fixture://correction/pool-01",
    )
    second = dict(
        adjustment,
        adjustment_id="ADJ-POOL-02",
        amount="-0.50",
        basis="actual",
        source_ref="fixture://correction/pool-02",
    )
    preview = check(engine, [one("adjustment", "adjustments.csv", [adjustment, second])])
    assert not preview.errors
    commit(engine, preview, operator="local")
    report = build_report(engine, "2026-10-27T12:00:00+08:00")
    rows = {row["order_line_id"]: row for row in report["rows"]}
    assert rows["OL-01"]["categories"]["marketing"] == (1050, "estimated")
    assert rows["OL-01"]["cost"].contribution_cents == 5250
    assert rows["OL-01"]["revisions"]["adjustment"]["ADJ-POOL-01"] == 1
    assert rows["OL-01"]["revisions"]["adjustment"]["ADJ-POOL-02"] == 1
    assert rows["OL-02"]["categories"]["marketing"] == (800, "actual")
    assert rows["OL-02"]["cost"].contribution_cents == 3000
    assert "ADJ-POOL-01" not in rows["OL-02"]["revisions"]["adjustment"]
    assert rows["OL-03"]["categories"]["goods"] == (0, "actual")
    assert rows["OL-03"]["revisions"]["adjustment"]["ADJ-RETURN-03"] == 1


def test_existing_database_is_extended_without_rebuilding_business_data(tmp_path):
    from xhs_seller.db import init_db
    from xhs_seller.models import ModelBase, ProductCandidate

    engine = make_engine(tmp_path / "existing.db")
    p4_tables = {
        "order_line",
        "expense",
        "expense_allocation",
        "cost_adjustment",
        "settlement_snapshot",
    }
    original_tables = [
        table for table in ModelBase.metadata.sorted_tables if table.name not in p4_tables
    ]
    ModelBase.metadata.create_all(engine, tables=original_tables)
    with Session(engine) as session:
        session.add(
            ProductCandidate(
                product_id="PRESERVED",
                revision=1,
                updated_at="2026-10-27T00:00:00+00:00",
                product_name="测试",
                category="测试",
                status="researching",
                risk_notes=None,
            )
        )
        session.commit()
    init_db(engine)
    init_db(engine)
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PRESERVED").product_name == "测试"
        assert not session.exec(select(OrderLine)).all()


def test_p3_database_upgrade_preserves_drafts_and_supports_p4_p5(tmp_path, monkeypatch):
    from sqlalchemy import inspect

    from xhs_seller import seed_demo
    from xhs_seller.db import init_db
    from xhs_seller.models import (
        ContentDraft,
        DraftAudit,
        DraftFact,
        ModelBase,
        ProductCandidate,
        Sku,
        SupplierQuote,
    )
    from xhs_seller.services.drafts import create_draft, save_fact
    from xhs_seller.services.proposals import create_proposal

    engine = make_engine(tmp_path / "p3-database.db")
    later_tables = {
        "order_line",
        "expense",
        "expense_allocation",
        "cost_adjustment",
        "settlement_snapshot",
        "action_proposal",
        "proposal_audit",
    }
    old_tables = [
        table for table in ModelBase.metadata.sorted_tables if table.name not in later_tables
    ]
    ModelBase.metadata.create_all(engine, tables=old_tables)
    with monkeypatch.context() as patch:
        patch.setattr(seed_demo, "init_db", lambda _engine: None)
        seed_demo.seed(engine)
    save_fact(
        engine,
        sku_id="S-01",
        kind="product_fact",
        fact_text="合成待核实事实",
        status="unverified",
        evidence_ref="",
        checked_at="",
        operator="local",
        real_data=False,
    )
    draft = create_draft(engine, sku_id="S-01", operator="local")
    models = (ProductCandidate, SupplierQuote, Sku, DraftFact, ContentDraft, DraftAudit)

    def snapshots():
        with Session(engine) as session:
            return {
                model.__name__: [row.model_dump() for row in session.exec(select(model)).all()]
                for model in models
            }

    original = snapshots()
    assert len(inspect(engine).get_table_names()) == 13
    init_db(engine)
    init_db(engine)
    assert len(inspect(engine).get_table_names()) == 20
    assert snapshots() == original
    with engine.connect() as connection:
        assert not connection.exec_driver_sql("PRAGMA foreign_key_check").all()

    preview = check(engine, [one("order", "orders.csv", [read("orders.csv")[0]])])
    assert not preview.errors
    commit(engine, preview, operator="local")
    proposal = create_proposal(
        engine,
        proposal_id="P-UPGRADE",
        action_type="reprice",
        target_type="sku",
        target_id="S-01",
        before="原值",
        after="建议",
        reason="合成测试",
        evidence_refs=["fixture://upgrade"],
        estimated_cost="",
        operator="local",
        real_data=False,
    )
    with Session(engine) as session:
        assert session.get(ContentDraft, draft.draft_id).facts_json == draft.facts_json
        assert session.get(OrderLine, "OL-01") is not None
        assert proposal.status == "proposed"
    engine.dispose()


def test_zero_amount_pool_requires_an_order_allocation(engine):
    expense = read("expenses.csv")[0]
    expense.update(
        expense_id="E-ZERO-POOL",
        amount="0.00",
        allocation_mode="pool",
        order_line_id="",
        source_ref="fixture://expense/zero-pool",
    )
    pool_file = one("expense", "expenses.csv", [expense])
    empty = check(engine, [pool_file])
    assert "ALLOCATION_MISMATCH" in codes(empty)
    with pytest.raises(ValueError, match="预检错误"):
        commit(engine, empty, operator="local")
    with Session(engine) as session:
        assert session.get(Expense, "E-ZERO-POOL") is None
    order = read("orders.csv")[0]
    allocation = read("expense_allocations.csv")[0]
    allocation.update(allocation_id="AL-ZERO", expense_id="E-ZERO-POOL", amount="0.00")
    valid = check(
        engine,
        [
            one("order", "orders.csv", [order]),
            pool_file,
            one("allocation", "allocations.csv", [allocation]),
        ],
    )
    assert not valid.errors
    commit(engine, valid, operator="local")


def test_settlement_cutoff_cannot_precede_payment(engine):
    order = read("orders.csv")[0]
    settlement = read("settlements.csv")[0]
    settlement["cutoff_at"] = "2026-10-20T09:59:59+08:00"
    inputs = [
        one("order", "orders.csv", [order]),
        one("settlement", "settlements.csv", [settlement]),
    ]
    invalid = check(engine, inputs)
    assert any(
        item.error_code == "TIME_ORDER_ERROR" and item.field == "cutoff_at"
        for item in invalid.errors
    )
    with pytest.raises(ValueError, match="预检错误"):
        commit(engine, invalid, operator="local")
    with Session(engine) as session:
        assert not session.exec(select(OrderLine)).all()
    # Equivalent instants with different offsets remain valid.
    settlement["cutoff_at"] = "2026-10-20T02:00:00Z"
    valid = check(
        engine,
        [one("order", "orders.csv", [order]), one("settlement", "settlements.csv", [settlement])],
    )
    assert not valid.errors
    commit(engine, valid, operator="local")
    order.update(
        revision="2", updated_at="2026-10-28T08:00:00+08:00", paid_at="2026-10-21T10:00:00+08:00"
    )
    changed_parent = check(engine, [one("order", "orders.csv", [order])])
    assert "TIME_ORDER_ERROR" in codes(changed_parent)
    with pytest.raises(ValueError, match="预检错误"):
        commit(engine, changed_parent, operator="local")
    with Session(engine) as session:
        assert session.get(OrderLine, "OL-01").revision == 1


def test_settlement_fractional_times_are_compared_chronologically(engine):
    order = read("orders.csv")[0]
    settlement = read("settlements.csv")[0]
    settlement.update(
        cutoff_at="2026-10-27T08:00:00.000001+08:00", updated_at="2026-10-27T08:00:00+08:00"
    )
    invalid = check(
        engine,
        [one("order", "orders.csv", [order]), one("settlement", "settlements.csv", [settlement])],
    )
    assert "TIME_ORDER_ERROR" in codes(invalid)
    load(engine)
    settlement = read("settlements.csv")[0]
    settlement.update(
        revision="2",
        updated_at="2026-10-28T08:00:00+08:00",
        cutoff_at="2026-10-26T18:00:00.000001+08:00",
    )
    valid = check(engine, [one("settlement", "settlements.csv", [settlement])])
    assert not valid.errors


def test_report_exports_display_as_of_in_shanghai_for_utc_input(engine):
    from xhs_seller.services.p4_report import build_report
    from xhs_seller.ui_p4 import csv_bytes, report_rows

    load(engine)
    local = build_report(engine, "2026-10-27T12:00:00+08:00")
    utc = build_report(engine, "2026-10-27T04:00:00Z")
    assert [row["cost"] for row in local["rows"]] == [row["cost"] for row in utc["rows"]]
    displayed = report_rows(utc)
    assert {row["as_of"] for row in displayed} == {"2026-10-27T12:00:00+08:00"}
    exported = csv_bytes(displayed, list(displayed[0])).decode("utf-8-sig")
    assert "2026-10-27T12:00:00+08:00" in exported
