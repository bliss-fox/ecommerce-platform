import sqlite3

import pytest
from sqlalchemy import event
from sqlmodel import Session, select
from test_r1 import engine as engine_fixture

from xhs_seller.services.p4_report import build_report

engine = engine_fixture
AS_OF = "2026-11-01T12:00:00+08:00"


@pytest.mark.parametrize(
    "counts,confirmed_at",
    [
        ('{"private_raw_input": ', AS_OF),
        ("null", AS_OF),
        ('{"insert": 1}', AS_OF),
        ('{"insert": true, "update": 0, "noop": 0, "rejected": 0}', AS_OF),
        ('{"insert": -1, "update": 0, "noop": 0, "rejected": 0}', AS_OF),
        ('{"insert": "1", "update": 0, "noop": 0, "rejected": 0}', AS_OF),
        ('{"insert": 1, "update": 0, "noop": 0, "rejected": 1}', AS_OF),
        ('{"insert": 1, "update": 0, "noop": 0, "rejected": 0}', "private_raw_input"),
        ('{"insert": 1, "update": 0, "noop": 0, "rejected": 0}', "2026-11-01T12:00:00"),
    ],
)
def test_a40_invalid_receipt_metadata_is_rejected_without_writes(engine, counts, confirmed_at):
    from xhs_seller.models import ImportBatch
    from xhs_seller.services.imports import read_receipt

    with Session(engine) as session:
        batch = session.exec(select(ImportBatch)).one()
        batch.counts_json = counts
        batch.confirmed_at = confirmed_at
        batch_id = batch.batch_id
        session.commit()
    with sqlite3.connect(engine.url.database) as connection:
        before = list(connection.iterdump())
    with pytest.raises(ValueError, match="回执元数据无效") as error:
        read_receipt(engine, batch_id)
    assert "private_raw_input" not in str(error.value)
    with sqlite3.connect(engine.url.database) as connection:
        assert list(connection.iterdump()) == before


def test_a39_sources_match_report_and_reading_is_pure(engine):
    with sqlite3.connect(engine.url.database) as connection:
        before = list(connection.iterdump())
    report = build_report(engine, AS_OF)
    rows = {r["order_line_id"]: r for r in report["rows"]}
    first = rows["OL-01"]["source_records"]
    assert first["expense"]["E-MARKETING-POOL"]["amount_cents"] == 2200
    allocation = next(iter(first["allocation"].values()))
    assert allocation["amount_cents"] == 1000
    assert allocation["order_line_id"] == "OL-01"
    assert rows["OL-01"]["categories"]["marketing"] == (1000, "actual")
    assert rows["OL-03"]["cost"].contribution_cents == -1800
    assert (
        next(iter(rows["OL-03"]["source_records"]["adjustment"].values()))["amount_cents"] == -2400
    )
    assert rows["OL-04"]["source_records"]["settlement"] == {}
    assert rows["OL-04"]["cost"].contribution_cents is None
    for row in report["rows"]:
        for kind, records in row["source_records"].items():
            assert {key: record["revision"] for key, record in records.items()} == row["revisions"][
                kind
            ]
    with sqlite3.connect(engine.url.database) as connection:
        assert list(connection.iterdump()) == before


def test_a39_concurrent_write_does_not_mix_source_and_result(engine):
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    writes = []

    def revise(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT EXPENSE.") and not writes:
            with sqlite3.connect(engine.url.database) as connection:
                connection.execute(
                    "UPDATE expense SET revision=2, amount_cents=4100 WHERE expense_id='E-01-goods'"
                )
            writes.append(True)

    event.listen(engine, "before_cursor_execute", revise)
    try:
        report = build_report(engine, AS_OF)
    finally:
        event.remove(engine, "before_cursor_execute", revise)
    row = report["rows"][0]
    assert writes
    assert row["cost"].contribution_cents == 5300
    assert row["source_records"]["expense"]["E-01-goods"]["amount_cents"] == 4000
    assert row["source_records"]["expense"]["E-01-goods"]["revision"] == 1
    row = build_report(engine, AS_OF)["rows"][0]
    assert row["cost"].contribution_cents == 5200
    assert row["source_records"]["expense"]["E-01-goods"]["revision"] == 2
