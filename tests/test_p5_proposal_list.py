"""A21: bounded, literal proposal search and isolated decision forms."""

import json
import sqlite3
from pathlib import Path

import pytest
from navigation_helpers import navigate
from sqlalchemy import event, text
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ActionProposal, ProposalAudit
from xhs_seller.seed_demo import seed
from xhs_seller.services import proposals
from xhs_seller.services.proposals import create_proposal, decide_proposal


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "proposal-list.db")
    seed(engine)
    return engine


def create(engine, proposal_id, *, target_type="sku", target_id="S-01"):
    return create_proposal(
        engine,
        proposal_id=proposal_id,
        action_type="reprice",
        target_type=target_type,
        target_id=target_id,
        before="原状",
        after="人工建议",
        reason="合成测试",
        evidence_refs=["fixture://research/01"],
        estimated_cost="",
        operator="local",
        real_data=False,
    )


def decide(engine, proposal_id):
    return decide_proposal(
        engine,
        proposal_id=proposal_id,
        expected_revision=1,
        decision="approved",
        note="人工核对",
        operator="reviewer",
    )


def query(engine, **filters):
    from xhs_seller.services.proposal_queries import list_proposals

    with Session(engine) as session:
        session.exec(text("BEGIN"))
        return list_proposals(session, **filters)


def open_list(engine, monkeypatch):
    from xhs_seller import db

    monkeypatch.setattr(db, "make_engine", lambda path=None: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    navigate(app, "本地提案与复盘")
    app.radio[0].set_value("审核与待办").run()
    return app


def widget(app, kind, label):
    return next(item for item in getattr(app, kind) if item.label == label)


def test_search_combines_filters_and_matches_case_sensitive_literal_ids(engine):
    create(engine, "P_A")
    create(engine, "PXA")
    create(engine, "p_a", target_id="S-02")
    create(engine, "P-PC", target_type="product", target_id="PC-01")
    decide(engine, "P_A")
    assert [p.proposal_id for p in query(engine, query=" _ ").items] == ["P_A", "p_a"]
    assert [p.proposal_id for p in query(engine, query="P_A").items] == ["P_A"]
    assert [p.proposal_id for p in query(engine, query="S-02").items] == ["p_a"]
    result = query(engine, status="proposed", target_type="sku", query="P")
    assert result.total == 1 and result.items[0].proposal_id == "PXA"
    assert query(engine, status="approved", target_type="product").total == 0


def test_three_pages_are_bounded_and_stable_at_equal_creation_times(engine, monkeypatch):
    timestamp = "2026-10-27T04:00:00+00:00"
    monkeypatch.setattr(proposals, "now", lambda: timestamp)
    for i in reversed(range(101)):
        create(engine, f"P-{i:03}")
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        pages = [query(engine, page=i) for i in (1, 2, 3)]
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert [len(p.items) for p in pages] == [50, 50, 1]
    assert all(p.total == 101 and p.pages == 3 for p in pages)
    assert [p.proposal_id for page in pages for p in page.items] == [
        f"P-{i:03}" for i in range(101)
    ]
    record_queries = [s for s in statements if s.startswith("SELECT action_proposal.")]
    assert len(record_queries) == 3 and all("LIMIT" in s and "OFFSET" in s for s in record_queries)
    assert not any(s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in statements)


@pytest.mark.parametrize(
    "filters",
    [
        {"status": "executed"}, {"target_type": "consumer"}, {"page": 0}, {"page": True},
        {"query": "%"}, {"query": "' OR 1=1"}, {"query": "x" * 65},
    ],
)
def test_invalid_query_is_rejected_without_echoing_input(engine, filters):
    with pytest.raises(ValueError):
        query(engine, **filters)
    with Session(engine) as session:
        assert not session.exec(select(ActionProposal)).all()
        assert not session.exec(select(ProposalAudit)).all()


def test_count_and_page_share_a_read_snapshot(engine):
    from xhs_seller.services.proposal_queries import list_proposals

    create(engine, "P-A")
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    writes = []

    def insert_after_count(conn, cursor, statement, parameters, context, executemany):
        if not writes and statement.startswith("SELECT action_proposal."):
            writes.append(True)
            create(engine, "P-B")

    event.listen(engine, "before_cursor_execute", insert_after_count)
    try:
        with Session(engine) as session:
            session.exec(text("BEGIN"))
            page = list_proposals(session)
    finally:
        event.remove(engine, "before_cursor_execute", insert_after_count)
    assert writes and page.total == 1
    assert [p.proposal_id for p in page.items] == ["P-A"]
    assert query(engine).total == 2


def test_last_page_clamps_after_matching_rows_change(engine):
    for i in range(51):
        create(engine, f"P-{i:03}")
    assert query(engine, status="proposed", page=2).page == 2
    decide(engine, "P-050")
    result = query(engine, status="proposed", page=2)
    assert (result.total, result.page, result.pages) == (50, 1, 1)
    assert len(result.items) == 50
    empty = query(engine, query="MISSING", page=99)
    assert (empty.total, empty.page, empty.pages, empty.items) == (0, 1, 1, ())


def test_ui_pages_filters_and_clears_unsubmitted_decisions(engine, monkeypatch):
    for i in range(51):
        create(engine, f"P-{i:03}")
    app = open_list(engine, monkeypatch)
    assert not app.exception and len(app.dataframe[0].value) == 50
    assert len(widget(app, "selectbox", "选择提案").options) == 50
    widget(app, "text_area", "决定依据").set_value("仅用于第一页")
    widget(app, "text_input", "决定人本地标识").set_value("local")
    widget(app, "number_input", "提案列表页").set_value(2).run()
    assert not app.exception and len(app.dataframe[0].value) == 1
    assert widget(app, "selectbox", "选择提案").value == "P-050"
    assert widget(app, "text_area", "决定依据").value == ""
    assert widget(app, "text_input", "决定人本地标识").value == ""
    widget(app, "text_area", "决定依据").set_value("翻页前未提交")
    widget(app, "text_input", "决定人本地标识").set_value("local")
    widget(app, "selectbox", "提案状态筛选").set_value("待审核").run()
    assert widget(app, "number_input", "提案列表页").value == 1
    assert widget(app, "text_area", "决定依据").value == ""
    widget(app, "text_input", "提案或目标 ID 筛选").set_value("P-050").run()
    assert not app.exception and len(app.dataframe[0].value) == 1
    widget(app, "text_area", "决定依据").set_value("仅属于 P-050")
    widget(app, "text_input", "决定人本地标识").set_value("local")
    widget(app, "button", "记录本地决定").click().run()
    assert not app.exception and not app.error
    assert any("P-050" in item.value for item in app.success)
    assert any("未找到" in item.value for item in app.info)
    assert not any(item.label == "记录本地决定" for item in app.button)
    widget(app, "selectbox", "提案状态筛选").set_value("已批准").run()
    assert len(app.dataframe[0].value) == 1
    assert any(item.label == "下载人工待办" for item in app.get("download_button"))
    widget(app, "selectbox", "提案目标筛选").set_value("候选").run()
    assert not app.exception and not app.dataframe
    assert not app.get("download_button")
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-050").status == "approved"
        assert len(session.exec(select(ProposalAudit)).all()) == 52


def test_same_proposal_under_new_filters_gets_a_fresh_decision_form(engine, monkeypatch):
    create(engine, "P-A")
    app = open_list(engine, monkeypatch)
    widget(app, "text_area", "决定依据").set_value("旧筛选下未提交")
    widget(app, "text_input", "决定人本地标识").set_value("local")
    widget(app, "selectbox", "决定").set_value("rejected")
    widget(app, "selectbox", "提案目标筛选").set_value("SKU").run()
    assert not app.exception
    assert widget(app, "selectbox", "选择提案").value == "P-A"
    assert widget(app, "text_area", "决定依据").value == ""
    assert widget(app, "text_input", "决定人本地标识").value == ""
    assert widget(app, "selectbox", "决定").value == "approved"
    widget(app, "text_input", "提案或目标 ID 筛选").set_value("NOT-FOUND").run()
    assert not app.exception and any("未找到" in item.value for item in app.info)
    assert not app.json and not app.get("download_button")


def test_status_filtered_list_refreshes_without_reusing_a_competing_decision(engine, monkeypatch):
    create(engine, "P-A")
    create(engine, "P-B")
    app = open_list(engine, monkeypatch)
    widget(app, "selectbox", "提案状态筛选").set_value("待审核").run()
    widget(app, "text_area", "决定依据").set_value("仅适用于 A")
    widget(app, "text_input", "决定人本地标识").set_value("local")
    decide(engine, "P-A")
    widget(app, "button", "记录本地决定").click().run()
    assert not app.exception
    assert widget(app, "selectbox", "选择提案").value == "P-B"
    assert widget(app, "text_area", "决定依据").value == ""
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-A").decided_by == "reviewer"
        assert session.get(ActionProposal, "P-B").status == "proposed"
        assert len(session.exec(select(ProposalAudit)).all()) == 3


def test_ui_shrinks_a_stale_last_page_without_applying_its_decision(engine, monkeypatch):
    for i in range(51):
        create(engine, f"P-{i:03}")
    app = open_list(engine, monkeypatch)
    widget(app, "selectbox", "提案状态筛选").set_value("待审核").run()
    widget(app, "number_input", "提案列表页").set_value(2).run()
    widget(app, "text_area", "决定依据").set_value("仅适用于最后一页")
    widget(app, "text_input", "决定人本地标识").set_value("local")
    decide(engine, "P-050")
    widget(app, "button", "记录本地决定").click().run()
    assert not app.exception and not app.error
    assert len(app.dataframe[0].value) == 50
    assert not app.number_input
    assert any("页码已调整" in item.value for item in app.info)
    assert widget(app, "selectbox", "选择提案").value == "P-000"
    assert json.loads(app.json[0].value)["proposal_id"] == "P-000"
    assert widget(app, "text_area", "决定依据").value == ""
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-000").status == "proposed"
        assert session.get(ActionProposal, "P-050").decided_by == "reviewer"
        assert len(session.exec(select(ProposalAudit)).all()) == 52


def test_filters_do_not_leak_between_data_spaces(engine, monkeypatch, tmp_path):
    from xhs_seller import db

    create(engine, "P-DEMO")
    real = make_engine(tmp_path / "real.db")
    seed(real)
    create(real, "P-REAL")
    app = open_list(engine, monkeypatch)
    monkeypatch.setattr(db, "make_engine", lambda path=None: engine if path is None else real)
    widget(app, "text_input", "提案或目标 ID 筛选").set_value("P-DEMO").run()
    widget(app, "text_area", "决定依据").set_value("演示内容")
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert not app.exception and not app.error
    assert widget(app, "text_input", "提案或目标 ID 筛选").value == ""
    assert widget(app, "selectbox", "选择提案").value == "P-REAL"
    assert widget(app, "text_area", "决定依据").value == ""
    assert json.loads(app.json[0].value)["proposal_id"] == "P-REAL"
