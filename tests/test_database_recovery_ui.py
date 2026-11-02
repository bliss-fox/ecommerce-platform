"""A44: contain partial pages and distinguish rollback from post-commit failure."""

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest
from navigation_helpers import navigate
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest
from test_r1 import create
from test_r1 import engine as engine_fixture

from xhs_seller.agents.snapshot import read_snapshot
from xhs_seller.db import init_db, make_engine
from xhs_seller.models import (
    ActionProposal,
    DraftAudit,
    DraftFact,
    ProposalAudit,
)
from xhs_seller.services.drafts import create_draft
from xhs_seller.services.imports import InputFile, commit, preflight
from xhs_seller.services.proposals import decide_proposal

engine = engine_fixture
ROOT = Path(__file__).resolve().parents[1]


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def open_app(engine, monkeypatch):
    from xhs_seller import db

    monkeypatch.setattr(db, "make_engine", lambda *args: engine)
    return AppTest.from_file(ROOT / "app.py", default_timeout=30).run()


def dump(engine):
    with sqlite3.connect(engine.url.database) as connection:
        return list(connection.iterdump())


@contextmanager
def database_failure(engine, match, *, after=False, skip=0):
    hits = []
    matches = []

    def fail(conn, cursor, statement, parameters, context, executemany):
        if match(statement.upper()):
            matches.append(True)
            if len(matches) > skip:
                hits.append(True)
                raise OperationalError(statement, parameters, Exception("private_raw_input"))

    name = "after_cursor_execute" if after else "before_cursor_execute"
    event.listen(engine, name, fail)
    try:
        yield hits
    finally:
        event.remove(engine, name, fail)


def assert_fault(app):
    assert not app.exception
    assert any("数据库操作失败" in item.value for item in app.error)
    assert all("private_raw_input" not in item.value for item in app.error)
    assert widget(app, "button", "重新读取页面")
    assert not app.success
    assert not app.dataframe and not app.json and not app.code
    assert not app.get("download_button")
    assert not app.text_input and not app.text_area
    assert [item.label for item in app.button] == ["重新读取页面"]


READ_PATHS = [
    ("候选与报价", None, "PRODUCT_CANDIDATE"),
    ("SKU 经济性", None, "SUPPLIER_QUOTE"),
    ("研究与评分", None, "RESEARCH_NOTE"),
    ("离线草稿", None, "DRAFT_FACT"),
    ("离线协作", None, "SKU"),
    ("订单成本与对账", None, "EXPENSE"),
    ("异常与复盘", None, "ORDER_LINE"),
    ("本地提案与复盘", "创建提案", "ORDER_LINE"),
    ("本地提案与复盘", "审核与待办", "PROPOSAL_AUDIT"),
    ("编辑与导入", "手工编辑", "PRODUCT_CANDIDATE"),
    ("编辑与导入", "修订历史", "IMPORT_BATCH"),
]


@pytest.mark.parametrize("page,operation,table", READ_PATHS)
def test_a44_read_failure_removes_partial_page_and_requires_explicit_reload(
    engine, monkeypatch, page, operation, table, caplog
):
    create(engine, "P-A")
    app = open_app(engine, monkeypatch)
    navigate(app, page)
    if operation:
        key = "p5_operation" if page == "本地提案与复盘" else "p1_operation"
        app.radio(key=key).set_value(operation).run()
    before = dump(engine)
    # Seed uses ID lookups with WHERE; fail the page's own collection read.
    with database_failure(
        engine, lambda sql: f"FROM {table}" in sql and (
            table in {"DRAFT_FACT", "PROPOSAL_AUDIT"} or "WHERE" not in sql
        ),
    ) as hits:
        app.run()
        assert hits
        assert_fault(app)
        count = len(hits)
        app.run()
        assert_fault(app)
        assert len(hits) == count
    assert "private_raw_input" not in caplog.text
    assert dump(engine) == before
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert app.session_state[f"nav_page_{app.session_state.nav_workspace}"] == page
    if operation:
        assert app.radio(key=key).value == operation
    assert dump(engine) == before


@pytest.mark.parametrize("mode", ["合成演示", "真实数据"])
def test_a44_initialization_failure_keeps_backup_available_and_isolates_spaces(
    engine, monkeypatch, tmp_path, mode
):
    from xhs_seller import db

    real = make_engine(tmp_path / "real.db")
    init_db(real)
    app = open_app(engine, monkeypatch)
    monkeypatch.setattr(db, "make_engine", lambda path=None: engine if path is None else real)
    target = engine if mode == "合成演示" else real
    before = dump(target)
    with database_failure(target, lambda sql: sql.strip() == "BEGIN IMMEDIATE") as hits:
        if mode == "真实数据":
            app.sidebar.selectbox(key="data_space").set_value(mode).run()
        else:
            app.run()
        assert hits
        assert_fault(app)
        navigate(app, "数据备份")
        widget(app, "button", "生成本地备份").click().run()
        assert not app.exception and not app.error
        assert app.get("download_button")
        assert dump(target) == before
        navigate(app, "离线草稿")
        assert_fault(app)
    other = "真实数据" if mode == "合成演示" else "合成演示"
    app.sidebar.selectbox(key="data_space").set_value(other).run()
    assert not app.exception and not app.error
    assert not any(item.label == "重新读取页面" for item in app.button)
    assert dump(target) == before


def prepare_write(app, engine, kind):
    if kind in {"fact", "asset", "generate", "draft"}:
        if kind == "draft":
            create_draft(engine, sku_id="S-01", operator="test")
        navigate(app, "离线草稿")
        app.text_input(key="p3_operator_合成演示").set_value("test").run()
        if kind == "fact":
            widget(app, "text_input", "事实文本（单行）").set_value("故障回归事实")
            return "保存事实", "DRAFT_AUDIT"
        if kind == "asset":
            widget(app, "text_input", "本地路径索引").set_value("materials/test.png")
            return "保存素材", "DRAFT_AUDIT"
        if kind == "generate":
            return "生成离线草稿", "DRAFT_AUDIT"
        widget(app, "text_area", "补充待核实事项（每行一项）").set_value("故障回归备注")
        return "保存草稿", "DRAFT_AUDIT"
    navigate(app, "本地提案与复盘")
    if kind == "create":
        widget(app, "text_input", "提案 ID（字母、数字、下划线或短横线）").set_value("P-NEW")
        for label, value in (
            ("变更前快照", "原状"), ("建议后快照", "建议"),
            ("建议原因", "合成回归"), ("证据索引（每行一个）", "fixture://recovery"),
        ):
            widget(app, "text_area", label).set_value(value)
        widget(app, "text_input", "创建人本地标识").set_value("test")
        return "创建待审提案", "PROPOSAL_AUDIT"
    create(engine, "P-A")
    create(engine, "P-B")
    app.radio(key="p5_operation").set_value("审核与待办").run()
    widget(app, "selectbox", "选择提案").set_value("P-B").run()
    widget(app, "text_area", "决定依据").set_value("人工核对")
    widget(app, "text_input", "决定人本地标识").set_value("test")
    return "记录本地决定", "PROPOSAL_AUDIT"


@pytest.mark.parametrize("kind", ["fact", "asset", "generate", "draft", "create", "decide"])
def test_a44_write_failure_rolls_back_business_and_audit_without_replaying(
    engine, monkeypatch, kind, caplog
):
    app = open_app(engine, monkeypatch)
    label, table = prepare_write(app, engine, kind)
    before = dump(engine)
    with database_failure(engine, lambda sql: sql.startswith(f"INSERT INTO {table}"), after=True):
        widget(app, "button", label).click().run()
    assert_fault(app)
    assert "private_raw_input" not in caplog.text
    assert dump(engine) == before
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert dump(engine) == before
    if kind == "fact":
        assert widget(app, "text_input", "事实文本（单行）").value == ""
    if kind == "decide":
        assert widget(app, "selectbox", "选择提案").value == "P-B"
        assert widget(app, "text_area", "决定依据").value == ""


@pytest.mark.parametrize("kind", ["fact", "decide"])
def test_a44_post_commit_refresh_failure_is_reloaded_without_duplicate_write(
    engine, monkeypatch, kind
):
    app = open_app(engine, monkeypatch)
    label, audit_table = prepare_write(app, engine, kind)
    written = []
    committed = []

    def observe(conn, cursor, statement, parameters, context, executemany):
        if statement.upper().startswith(f"INSERT INTO {audit_table}"):
            written.append(True)

    def on_commit(conn):
        if written:
            committed.append(True)

    model = "DRAFT_FACT" if kind == "fact" else "ACTION_PROPOSAL"
    event.listen(engine, "after_cursor_execute", observe)
    event.listen(engine, "commit", on_commit)
    try:
        with database_failure(
            engine, lambda sql: bool(committed) and f"FROM {model}" in sql,
        ) as hits:
            widget(app, "button", label).click().run()
    finally:
        event.remove(engine, "after_cursor_execute", observe)
        event.remove(engine, "commit", on_commit)
    assert committed and hits
    assert_fault(app)
    assert any("核对已保存记录" in item.value for item in app.error)
    assert not any("未保存" in item.value or "已回滚" in item.value for item in app.error)
    after = dump(engine)
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert dump(engine) == after
    with Session(engine) as session:
        if kind == "fact":
            assert session.exec(select(DraftFact)).one().text == "故障回归事实"
            assert len(session.exec(select(DraftAudit)).all()) == 1
        else:
            assert session.get(ActionProposal, "P-B").status == "approved"
            assert len(session.exec(select(ProposalAudit)).all()) == 3
            assert widget(app, "selectbox", "选择提案").value == "P-B"
            assert not any(item.label == "记录本地决定" for item in app.button)
            assert app.get("download_button")


@pytest.mark.parametrize("kind", ["draft", "todo"])
def test_a44_export_read_failure_removes_prior_details_and_download(engine, monkeypatch, kind):
    if kind == "draft":
        create_draft(engine, sku_id="S-01", operator="test")
        page, table = "离线草稿", "DRAFT_FACT"
    else:
        create(engine, "P-A")
        decide_proposal(
            engine, proposal_id="P-A", expected_revision=1, decision="approved",
            note="人工核对", operator="test",
        )
        page, table = "本地提案与复盘", "PROPOSAL_AUDIT"
    app = open_app(engine, monkeypatch)
    navigate(app, page)
    if kind == "todo":
        app.radio(key="p5_operation").set_value("审核与待办").run()
    assert app.get("download_button")
    before = dump(engine)
    with database_failure(engine, lambda sql: f"FROM {table}" in sql, skip=1) as hits:
        app.run()
    assert hits
    assert_fault(app)
    assert dump(engine) == before
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert app.get("download_button")


@pytest.mark.parametrize("kind", ["product", "quote", "sku", "research", "link", "check"])
def test_a44_editor_recovers_same_record_and_discards_old_preview(engine, monkeypatch, kind):
    directory = ROOT / "examples" / "p2"
    preview = preflight(
        engine, [InputFile(entity, name, (directory / name).read_bytes()) for entity, name in (
            ("research", "research_notes.csv"), ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )], real_data=False,
    )
    commit(engine, preview, operator="test")
    app = open_app(engine, monkeypatch)
    navigate(app, "编辑与导入")
    app.selectbox(key="edit_kind").set_value(kind).run()
    select_key = f"edit_record_合成演示_{kind}"
    selected = app.selectbox(key=select_key).options[2]
    app.selectbox(key=select_key).set_value(selected).run()
    widget(app, "button", "预检编辑").click().run()
    assert app.session_state["p1_preview"]
    before = dump(engine)
    # Existing editor reloads its collection after the initialization transaction.
    from xhs_seller.services.imports import SPECS

    table = SPECS[kind][0].__tablename__.upper()
    with database_failure(engine, lambda sql: f"FROM {table}" in sql and "WHERE" not in sql):
        app.run()
    assert_fault(app)
    assert "p1_preview" not in app.session_state
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert app.selectbox(key=select_key).value == selected
    assert "p1_preview" not in app.session_state
    assert dump(engine) == before


@pytest.mark.parametrize("page,key,table", [
    ("研究与评分", "p2_asof_合成演示", "RESEARCH_NOTE"),
    ("离线协作", "m1_as_of", "SKU"),
    ("订单成本与对账", "p4_report_as_of", "ORDER_LINE"),
    ("异常与复盘", "p5_review_as_of", "EXPENSE"),
])
def test_a44_reload_preserves_explicit_report_time(engine, monkeypatch, page, key, table):
    app = open_app(engine, monkeypatch)
    navigate(app, page)
    stamp = "2026-11-01T12:34:56+08:00"
    app.text_input(key=key).set_value(stamp).run()
    with database_failure(engine, lambda sql: f"FROM {table}" in sql and "WHERE" not in sql):
        app.run()
    assert_fault(app)
    app.run()  # Let Streamlit discard hidden widgets before an explicit retry.
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert app.text_input(key=key).value == stamp


@pytest.mark.parametrize("deleted", [False, True])
def test_a44_navigation_retry_revalidates_original_target(engine, monkeypatch, deleted):
    from xhs_seller import ui_navigation

    create(engine, "P-A")
    create(engine, "P-B")
    app = open_app(engine, monkeypatch)
    navigate(app, "本地提案与复盘")
    app.session_state.nav_target = (
        "本地提案与复盘", "proposal", "P-B", None, app.session_state.nav_space,
    )
    original = ui_navigation.resolve_target

    def fail(*args):
        raise OperationalError("SELECT", {}, Exception("private_raw_input"))

    monkeypatch.setattr(ui_navigation, "resolve_target", fail)
    app.run()
    assert_fault(app)
    monkeypatch.setattr(ui_navigation, "resolve_target", original)
    if deleted:
        with sqlite3.connect(engine.url.database) as connection:
            connection.execute("DELETE FROM action_proposal WHERE proposal_id='P-B'")
    widget(app, "button", "重新读取页面").click().run()
    assert not app.exception
    if deleted:
        assert any("定位对象不存在" in item.value for item in app.error)
        assert not app.json and not any(item.label == "记录本地决定" for item in app.button)
    else:
        assert not app.error
        assert widget(app, "selectbox", "选择提案").value == "P-B"


def test_a44_recovery_cancels_work_without_losing_occupied_worker_slots(engine, monkeypatch):
    from test_m1 import wait_for

    app = open_app(engine, monkeypatch)
    navigate(app, "编辑与导入")
    manager = app.session_state.m1_session
    snap = read_snapshot(engine, "合成演示", ["S-01"], "2026-11-01T12:00:00+08:00")
    started, release = threading.Barrier(4), threading.Event()

    def worker(*args):
        started.wait(timeout=5)
        release.wait(timeout=10)
        raise ValueError("cancelled test work")

    run = manager.start(snap, worker=worker)
    try:
        started.wait(timeout=5)
        with database_failure(
            engine, lambda sql: "FROM PRODUCT_CANDIDATE" in sql and "WHERE" not in sql,
        ):
            app.run()
        assert_fault(app)
        app.run()
        assert run.view()["status"] == "cancelled"
        assert manager.run is None and manager.pool.active == 3
        with pytest.raises(ValueError, match="资源尚未释放"):
            manager.start(snap)
    finally:
        release.set()
        wait_for(lambda: manager.pool.active == 0)
