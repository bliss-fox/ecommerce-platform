import csv
import io
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from xhs_seller.db import make_engine
from xhs_seller.models import ImportBatch, ImportFailure, ProductCandidate, RevisionAudit
from xhs_seller.seed_demo import seed
from xhs_seller.services.imports import (
    BaselineChangedError,
    InputFile,
    commit,
)
from xhs_seller.services.imports import (
    preflight as run_preflight,
)


def preflight(engine, files, **kwargs):
    kwargs.setdefault("real_data", False)
    return run_preflight(engine, files, **kwargs)


BASE = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "test.db")
    seed(engine)
    return engine


def source(kind, path):
    path = BASE / path
    return InputFile(kind, path.name, path.read_bytes())


def content(kind, filename, rows):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return InputFile(kind, filename, buffer.getvalue().encode("utf-8"))


def rows(path):
    with (BASE / path).open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def codes(preview):
    return {error.error_code for error in preview.errors}


def test_large_duplicate_sku_batch_reports_each_conflicting_row(engine):
    template = rows("p1/skus.csv")[0]
    items = [dict(template, sku_id=f"S-BULK-{i:04}", sku_code="BULK-CODE") for i in range(512)]
    preview = preflight(engine, [content("sku", "bulk.csv", items)])
    errors = [item for item in preview.errors if item.error_code == "BUSINESS_KEY_CONFLICT"]
    assert len(errors) == 511
    assert {(item.file, item.row_number, item.record_id) for item in errors} == {
        ("1. bulk.csv", i + 2, f"S-BULK-{i:04}") for i in range(1, 512)
    }
    assert preview.counts == {"insert": 1, "update": 0, "noop": 0, "rejected": 511}
    with pytest.raises(ValueError, match="预检错误"):
        commit(engine, preview, operator="local")


def test_a05_idempotent_reorder_and_normalization(engine):
    files = [
        source("product", "p1/product_candidates.csv"),
        source("quote", "p1/supplier_quotes.csv"),
        source("sku", "p1/skus.csv"),
    ]
    result = preflight(engine, files)
    assert not result.errors
    assert result.counts == {"insert": 0, "update": 0, "noop": 12, "rejected": 0}
    commit(engine, result, operator="local")
    reordered = source("product", "import_cases/products_reordered.csv")
    result = preflight(engine, [reordered])
    assert not result.errors and result.counts["noop"] == 3
    sku = rows("p1/skus.csv")[0]
    sku["packaging_per_order"] = ".80"  # not a valid money representation
    assert "TYPE_ERROR" in codes(preflight(engine, [content("sku", "sku.csv", [sku])]))
    sku["packaging_per_order"] = "0.8"
    sku["updated_at"] = "2026-10-27T00:00:00Z"
    assert preflight(engine, [content("sku", "renamed.csv", [sku])]).counts["noop"] == 1
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == 1
        assert not session.exec(select(RevisionAudit)).all()


def test_a06_revision_conflict_history_and_duplicate(engine):
    conflict = preflight(
        engine, [source("product", "import_cases/product_same_revision_conflict.csv")]
    )
    assert "REVISION_CONFLICT" in codes(conflict)
    updated = preflight(engine, [source("product", "import_cases/product_revision2.csv")])
    assert not updated.errors and updated.counts["update"] == 1
    commit(engine, updated, operator="local")
    assert (
        preflight(engine, [source("product", "import_cases/product_revision2.csv")]).counts["noop"]
        == 1
    )
    assert "STALE_REVISION" in codes(
        preflight(engine, [source("product", "import_cases/product_stale_revision1.csv")])
    )
    with Session(engine) as session:
        audit = session.exec(select(RevisionAudit)).one()
        assert audit.old_revision == 1 and audit.new_revision == 2
        assert len(session.exec(select(ProductCandidate)).all()) == 3
    row = rows("p1/skus.csv")[0]
    row["sku_id"] = "S-NEW"
    assert "BUSINESS_KEY_CONFLICT" in codes(preflight(engine, [content("sku", "x.csv", [row])]))
    row = rows("p1/product_candidates.csv")[0]
    assert "DUPLICATE_ID_IN_BATCH" in codes(
        preflight(engine, [content("product", "x.csv", [row, row])])
    )


def test_a07_invalid_file_privacy_and_baseline(engine):
    preview = preflight(engine, [source("product", "import_cases/products_atomic_error.csv")])
    err = preview.errors[0]
    assert (err.row_number, err.field, err.error_code) == (3, "status", "TYPE_ERROR")
    with pytest.raises(ValueError):
        commit(engine, preview, operator="local")
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-04") is None
    row = rows("p1/product_candidates.csv")[0]
    row["phone"] = "13800138000"
    private = preflight(engine, [content("product", "x.csv", [row])])
    assert "PRIVACY_RISK" in codes(private)
    assert "13800138000" not in repr(private.errors)
    row.pop("phone")
    row["updated_at"] = "2026-10-27T08:00:00"
    assert "TYPE_ERROR" in codes(preflight(engine, [content("product", "x.csv", [row])]))
    row = rows("p1/skus.csv")[0]
    row["sku_id"] = "S-NEW"
    row["sku_code"] = "NEW"
    row["quote_id"] = "Q-MISSING"
    assert "FK_NOT_FOUND" in codes(preflight(engine, [content("sku", "x.csv", [row])]))
    preview = preflight(engine, [source("product", "import_cases/product_revision2.csv")])
    with Session(engine) as session:
        item = session.get(ProductCandidate, "PC-02")
        item.risk_notes = "changed"
        session.commit()
    with pytest.raises(BaselineChangedError):
        commit(engine, preview, operator="local")


def test_a07_write_failure_rolls_back_everything(engine):
    product = rows("p1/product_candidates.csv")[0]
    product["product_id"] = "PC-NEW"
    quote = rows("p1/supplier_quotes.csv")[0]
    quote["quote_id"] = "Q-NEW"
    quote["product_id"] = "PC-NEW"
    preview = preflight(
        engine, [content("product", "p.csv", [product]), content("quote", "q.csv", [quote])]
    )
    assert not preview.errors
    observed = []

    def fail_after_product(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO product_candidate"):
            observed.append("product")
        if statement.startswith("INSERT INTO supplier_quote"):
            assert observed == ["product"]
            raise RuntimeError("simulated write failure")

    event.listen(engine, "before_cursor_execute", fail_after_product)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            commit(engine, preview, operator="local")
    finally:
        event.remove(engine, "before_cursor_execute", fail_after_product)
    assert observed == ["product"]
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-NEW") is None
        assert not session.exec(select(ImportBatch)).all()
        assert not session.exec(select(RevisionAudit)).all()


def test_real_database_rejects_fixture_reference(engine):
    result = preflight(engine, [source("quote", "p1/supplier_quotes.csv")], real_data=True)
    assert "PRIVACY_RISK" in codes(result)


def test_privacy_sanitizes_errors_and_sqlite_range(engine):
    row = rows("p1/supplier_quotes.csv")[0]
    row["source_ref"] = "https://example.test/?uid=12345"
    result = preflight(engine, [content("quote", "13800138000.csv", [row])])
    assert "PRIVACY_RISK" in codes(result)
    assert "13800138000" not in repr(result)
    assert "12345" not in repr(result.errors)
    row = rows("p1/skus.csv")[0]
    row["sale_price"] = "999999999999999999999999.99"
    assert "TYPE_ERROR" in codes(preflight(engine, [content("sku", "x.csv", [row])]))


def test_failed_preflight_records_sanitized_metadata_only(engine):
    row = rows("p1/product_candidates.csv")[0]
    row["risk_notes"] = "13800138000"
    preview = preflight(engine, [content("product", "phone-13800138000.csv", [row])])
    assert "PRIVACY_RISK" in codes(preview)
    with Session(engine) as session:
        failure = session.exec(select(ImportFailure)).one()
        assert "13800138000" not in failure.errors_json + failure.files_json + failure.counts_json
        assert session.get(ProductCandidate, "PC-01").risk_notes != row["risk_notes"]


def test_previews_are_bound_to_database_and_cross_file_refs(engine, tmp_path):
    product = rows("p1/product_candidates.csv")[0]
    product["product_id"] = "PC-NEW"
    quote = rows("p1/supplier_quotes.csv")[0]
    quote["product_id"] = "PC-NEW"
    quote["quote_id"] = "Q-NEW"
    result = preflight(
        engine, [content("quote", "q.csv", [quote]), content("product", "p.csv", [product])]
    )
    assert not result.errors and result.counts["insert"] == 2
    other = make_engine(tmp_path / "other.db")
    seed(other)
    with pytest.raises(ValueError, match="数据库"):
        commit(other, result, operator="local")
    commit(engine, result, operator="local")
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-NEW") is not None


def test_reordered_columns_and_unknown_column(engine):
    row = rows("p1/product_candidates.csv")[0]
    backwards = dict(reversed(list(row.items())))
    assert preflight(engine, [content("product", "renamed.csv", [backwards])]).counts["noop"] == 1
    row["extra"] = "irrelevant"
    assert "SCHEMA_ERROR" in codes(preflight(engine, [content("product", "x.csv", [row])]))


def test_duplicate_is_reported_even_when_first_row_invalid(engine):
    row = rows("p1/product_candidates.csv")[0]
    row["status"] = "not_valid"
    result = preflight(engine, [content("product", "duplicate.csv", [row, row])])
    assert {"TYPE_ERROR", "DUPLICATE_ID_IN_BATCH"} <= codes(result)
    assert result.counts["rejected"] == 2


def test_two_errors_in_one_row_count_as_one_rejected_row(engine):
    row = rows("p1/product_candidates.csv")[0]
    row["status"] = "not_valid"
    row["updated_at"] = "2026-10-27"
    result = preflight(engine, [content("product", "bad.csv", [row])])
    assert len(result.errors) == 2
    assert result.counts["rejected"] == 1


def test_real_import_rejects_uppercase_fixture_ref(engine):
    row = rows("p1/supplier_quotes.csv")[0]
    row["source_ref"] = "FIXTURE://quote/Q-01A"
    row["sample_evidence_ref"] = "local/sample-01"
    result = preflight(engine, [content("quote", "quotes.csv", [row])], real_data=True)
    assert any(
        error.field == "source_ref" and error.error_code == "PRIVACY_RISK"
        for error in result.errors
    )


def test_preflight_reads_consistent_snapshot_during_concurrent_commit(engine):
    import sqlite3

    from xhs_seller.services.imports import digest, snapshot

    path = engine.url.database
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    with Session(engine) as session:
        expected_baseline = digest(snapshot(session))
    writes = []

    def concurrent_commit(conn, cursor, statement, parameters, context, executemany):
        if not writes and statement.lstrip().upper().startswith("SELECT SUPPLIER_QUOTE."):
            writes.append(True)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE product_candidate SET product_name='new', revision=2, "
                    "updated_at='2026-10-27T01:00:00+00:00' WHERE product_id='PC-02'"
                )
                connection.execute(
                    "UPDATE supplier_quote SET unit_cost_cents=1200, revision=2, "
                    "updated_at='2026-10-27T01:00:00+00:00' WHERE quote_id='Q-01A'"
                )

    event.listen(engine, "before_cursor_execute", concurrent_commit)
    try:
        result = preflight(engine, [source("product", "import_cases/product_revision2.csv")])
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_commit)
    assert writes and not result.errors
    assert result.baseline == expected_baseline
    with pytest.raises(BaselineChangedError):
        commit(engine, result, operator="local")
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 1
        assert not session.exec(select(ImportBatch)).all()


def test_fractional_second_revisions_and_publication_time(engine):
    product = rows("p1/product_candidates.csv")[0]
    product.update(revision="2", updated_at="2026-10-27T00:00:00.000001Z", risk_notes="新修订")
    valid = preflight(engine, [content("product", "p.csv", [product])])
    assert not valid.errors
    research = rows("p2/research_notes.csv")[0]
    research.update(
        captured_at="2026-10-27T08:00:00+08:00", published_at="2026-10-27T00:00:00.000001Z"
    )
    invalid = preflight(engine, [content("research", "r.csv", [research])])
    assert any(
        item.error_code == "TIME_ORDER_ERROR" and item.field == "published_at"
        for item in invalid.errors
    )


@pytest.mark.parametrize("filenames", [("same.csv", "same.csv"), ("phone.csv", "email.csv")])
def test_preflight_distinguishes_files_with_same_display_name(engine, filenames):
    product = rows("p1/product_candidates.csv")[0]
    quote = rows("p1/supplier_quotes.csv")[0]
    product["category"] = ""
    first = content("product", filenames[0], [product])
    second = content("quote", filenames[1], [quote])

    preview = preflight(engine, [first, second])
    assert preview.counts == {"insert": 0, "update": 0, "noop": 1, "rejected": 1}
    assert len({item["label"] for item in preview.files}) == 2
    assert preview.errors[0].file == preview.files[0]["label"]
    assert preview.changes[0].file == preview.files[1]["label"]
    if filenames[0] != filenames[1]:
        assert [item["name"] for item in preview.files] == ["redacted.csv", "redacted.csv"]

    quote["supplier_name"] = ""
    invalid = preflight(engine, [first, content("quote", filenames[1], [quote])])
    assert invalid.counts["rejected"] == 2
    assert len({error.file for error in invalid.errors}) == 2


def test_error_report_uses_start_line_of_multiline_csv_records(engine):
    products = rows("p1/product_candidates.csv")[:2]
    products[0].update(
        revision="2",
        updated_at="2026-10-27T01:00:00+00:00",
        risk_notes="第一行\n第二行",
    )
    products[1]["category"] = ""
    preview = preflight(engine, [content("product", "products.csv", products)])
    assert [(issue.row_number, issue.field) for issue in preview.errors] == [(4, "category")]
    assert preview.changes[0].row_number == 2
    assert preview.counts == {"insert": 0, "update": 1, "noop": 0, "rejected": 1}

    products[0]["category"] = ""
    preview = preflight(engine, [content("product", "products.csv", products)])
    assert [issue.row_number for issue in preview.errors] == [2, 4]


def test_malformed_csv_reports_physical_start_line(engine):
    product = rows("p1/product_candidates.csv")[0]
    product["risk_notes"] = "第一行\n第二行"
    first = content("product", "products.csv", [product])
    invalid = InputFile("product", first.name, first.content + b'"unterminated')
    preview = preflight(engine, [invalid])
    assert [(issue.row_number, issue.error_code) for issue in preview.errors] == [
        (4, "SCHEMA_ERROR")
    ]
