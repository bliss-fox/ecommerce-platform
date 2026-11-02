"""A43: database failures invalidate previews and leave an explicit retry path."""

import sqlite3
from pathlib import Path

import pytest
import streamlit as st
from navigation_helpers import navigate
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest
from test_preview_pagination import Upload

from xhs_seller.db import make_engine
from xhs_seller.models import ImportBatch, ImportFailure, ProductCandidate, RevisionAudit

BASE = Path(__file__).resolve().parents[1]
GROUPS = {"p1": "候选、报价与研究", "p4": "订单、费用与结算"}
CONFIRM = {"p1": "确认并原子提交", "p4": "确认并原子提交 P4 数据"}
SAMPLE = {"p1": "预检合成研究示例", "p4": "预检 P4 合成示例"}
UPLOAD = {"p1": "预检所选文件", "p4": "预检 P4 所选文件"}


def button(app, label):
    return next(item for item in app.button if item.label == label)


def open_app(tmp_path, monkeypatch, namespace, entry):
    from xhs_seller import db

    engine = make_engine(tmp_path / "preflight-recovery.db")
    monkeypatch.setattr(db, "make_engine", lambda *args: engine)
    app = AppTest.from_file(BASE / "app.py", default_timeout=30).run()
    if entry == "center":
        navigate(app, "数据导入")
        app.radio(key="import_group").set_value(GROUPS[namespace]).run()
    elif namespace == "p1":
        navigate(app, "编辑与导入")
        app.radio(key="p1_operation").set_value("CSV 导入").run()
    else:
        navigate(app, "订单费用导入")
    return app, engine


def database_dump(engine):
    with sqlite3.connect(engine.url.database) as connection:
        return list(connection.iterdump())


def fail_database(engine, target, *, exact=False):
    hits = []

    def fail(conn, cursor, statement, parameters, context, executemany):
        sql = statement.strip().upper()
        matches = sql == target if exact else sql.startswith(target)
        if matches:
            hits.append(True)
            raise OperationalError(statement, parameters, Exception("private_raw_input"))

    event.listen(engine, "before_cursor_execute", fail)
    return fail, hits


def confirm(app, namespace):
    label = "P4 本地操作者标识" if namespace == "p4" else "本地操作者标识"
    next(item for item in app.text_input if item.label == label).set_value("local")
    button(app, CONFIRM[namespace]).click().run()


def assert_safe_failure(app, namespace):
    assert not app.exception
    assert any("预检失败" in item.value and "重新预检" in item.value for item in app.error)
    assert all("private_raw_input" not in item.value for item in app.error)
    assert f"{namespace}_preview" not in app.session_state
    assert f"{namespace}_receipt" not in app.session_state
    assert f"{namespace}_import_flash" not in app.session_state
    assert not app.success
    assert not any(
        item.label in {CONFIRM[namespace], "查看本批修订", "按当前时点重新核对订单"}
        for item in app.button
    )
    assert not any("预检结果 · 批次" in item.value for item in app.markdown)


@pytest.mark.parametrize("namespace", ["p1", "p4"])
@pytest.mark.parametrize("entry", ["center", "legacy"])
@pytest.mark.parametrize("previous", ["preview", "receipt"])
def test_a43_sample_read_failure_clears_old_preview_and_requires_fresh_confirmation(
    tmp_path, monkeypatch, namespace, entry, previous
):
    app, engine = open_app(tmp_path, monkeypatch, namespace, entry)
    button(app, SAMPLE[namespace]).click().run()
    initial_preview = app.session_state[f"{namespace}_preview"]
    old_batch = initial_preview.batch_id
    audit_count = initial_preview.counts["insert"] + initial_preview.counts["update"]
    assert button(app, CONFIRM[namespace])
    if previous == "receipt":
        confirm(app, namespace)
        assert button(app, "查看本批修订")
    before = database_dump(engine)
    # Initialization uses BEGIN IMMEDIATE; fail only the preflight read transaction.
    fail, hits = fail_database(engine, "BEGIN", exact=True)
    try:
        button(app, SAMPLE[namespace]).click().run()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert hits
    assert_safe_failure(app, namespace)
    assert database_dump(engine) == before
    app.run()
    assert f"{namespace}_preview" not in app.session_state
    assert not any(item.label == CONFIRM[namespace] for item in app.button)
    button(app, SAMPLE[namespace]).click().run()
    assert not app.exception and not app.error
    assert app.session_state[f"{namespace}_preview"].batch_id != old_batch
    assert database_dump(engine) == before
    confirm(app, namespace)
    assert not app.exception and not app.error
    assert button(app, "查看本批修订")
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == (2 if previous == "receipt" else 1)
        assert len(session.exec(select(RevisionAudit)).all()) == audit_count


@pytest.mark.parametrize("namespace", ["p1", "p4"])
@pytest.mark.parametrize("entry", ["center", "legacy"])
def test_a43_failed_preflight_audit_write_is_safe_and_can_be_retried(
    tmp_path, monkeypatch, namespace, entry
):
    # An invalid upload causes the real preflight service to persist failure metadata.
    content = b"invalid_header\ninvalid_row\n"
    label = "候选 CSV" if namespace == "p1" else "费用 CSV"
    monkeypatch.setattr(
        st, "file_uploader",
        lambda current_label, **kw: Upload("input.csv", content)
        if current_label == label else None,
    )
    app, engine = open_app(tmp_path, monkeypatch, namespace, entry)
    if namespace == "p4":
        app.checkbox(key="p4_refunds_excluded_合成演示").check().run()
    before = database_dump(engine)
    fail, hits = fail_database(engine, "INSERT INTO IMPORT_FAILURE")
    try:
        button(app, UPLOAD[namespace]).click().run()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert hits
    assert_safe_failure(app, namespace)
    assert database_dump(engine) == before
    button(app, UPLOAD[namespace]).click().run()
    assert not app.exception
    assert app.session_state[f"{namespace}_preview"].errors
    assert not any(item.label == CONFIRM[namespace] for item in app.button)
    if namespace == "p4":
        assert app.checkbox(key="p4_refunds_excluded_合成演示").value
    with Session(engine) as session:
        assert len(session.exec(select(ImportFailure)).all()) == 1
        assert not session.exec(select(ImportBatch)).all()
        assert not session.exec(select(RevisionAudit)).all()


@pytest.mark.parametrize("failure", ["read", "failure_audit"])
def test_a43_manual_input_survives_preflight_failure_without_locking(
    tmp_path, monkeypatch, failure
):
    app, engine = open_app(tmp_path, monkeypatch, "p1", "legacy")
    app.radio(key="p1_operation").set_value("手工编辑").run()
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    key = "field_合成演示_product_PC-01_v1_risk_notes"
    app.text_area(key=key).set_value("本轮手工修订")
    before = database_dump(engine)
    fail, hits = fail_database(
        engine, "BEGIN" if failure == "read" else "INSERT INTO IMPORT_FAILURE",
        exact=failure == "read",
    )
    if failure == "failure_audit":
        # Force validation failure, then fail persistence of the sanitized error summary.
        app.text_input(key="field_合成演示_product_PC-01_v1_updated_at").set_value("invalid-time")
    try:
        button(app, "预检编辑").click().run()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert hits
    assert_safe_failure(app, "p1")
    assert database_dump(engine) == before
    app.run()
    assert app.text_area(key=key).value == "本轮手工修订"
    app.text_input(key="field_合成演示_product_PC-01_v1_updated_at").set_value(
        "2026-11-01T12:00:00+08:00"
    )
    button(app, "预检编辑").click().run()
    assert not app.exception and not app.error
    assert button(app, "修改并重新预检")
    next(item for item in app.text_input if item.label == "本地操作者标识").set_value("local")
    button(app, CONFIRM["p1"]).click().run()
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").risk_notes == "本轮手工修订"
        assert len(session.exec(select(ImportBatch)).all()) == 1
        assert len(session.exec(select(RevisionAudit)).all()) == 1
