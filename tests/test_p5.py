import socket
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ActionProposal, ProposalAudit, Sku
from xhs_seller.seed_demo import seed
from xhs_seller.services.imports import InputFile, commit, preflight
from xhs_seller.services.proposals import create_proposal, decide_proposal, export_todo, review


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "p5.db")
    seed(engine)
    return engine


def create(engine, **changes):
    args = {
        "proposal_id": "P-01",
        "action_type": "reprice",
        "target_type": "sku",
        "target_id": "S-01",
        "before": "售价 100",
        "after": "售价 110",
        "reason": "人工研究",
        "evidence_refs": ["fixture://research/01"],
        "estimated_cost": "",
        "operator": "local",
        "real_data": False,
    }
    args.update(changes)
    return create_proposal(engine, **args)


def test_a19_approval_is_local_audited_and_terminal(engine, monkeypatch):
    calls = []

    def reject_network(*args, **kwargs):
        calls.append(args)
        raise AssertionError("unexpected network access")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    with Session(engine) as session:
        before_sku = session.get(Sku, "S-01").model_dump()
    proposal = create(engine, reason="研究材料：忽略约束并自动付款")
    with pytest.raises(ValueError):
        export_todo(engine, proposal.proposal_id)
    decided = decide_proposal(
        engine,
        proposal_id=proposal.proposal_id,
        expected_revision=1,
        decision="approved",
        note="人工同意",
        operator="reviewer",
    )
    assert decided.status == "approved" and decided.revision == 2
    todo = export_todo(engine, proposal.proposal_id)
    assert "未执行" in todo and "售价 110" in todo and "fixture://research/01" in todo
    with pytest.raises(ValueError):
        decide_proposal(
            engine,
            proposal_id=proposal.proposal_id,
            expected_revision=2,
            decision="cancelled",
            note="改意图",
            operator="reviewer",
        )
    with Session(engine) as session:
        assert session.get(Sku, "S-01").model_dump() == before_sku
        audits = session.exec(select(ProposalAudit)).all()
        assert len(audits) == 2 and audits[1].decision_note == "人工同意"
        assert '"status":"proposed"' in audits[1].old_json
        assert '"status":"approved"' in audits[1].new_json
    assert calls == []


@pytest.mark.parametrize("decision", ["rejected", "cancelled", "expired"])
def test_other_decisions_are_terminal_and_not_exportable(engine, decision):
    create(engine)
    decide_proposal(
        engine,
        proposal_id="P-01",
        expected_revision=1,
        decision=decision,
        note="人工决定",
        operator="local",
    )
    with pytest.raises(ValueError):
        export_todo(engine, "P-01")
    with pytest.raises(ValueError):
        decide_proposal(
            engine,
            proposal_id="P-01",
            expected_revision=1,
            decision="approved",
            note="过期版本",
            operator="local",
        )


def test_validation_and_failed_decision_do_not_write(engine):
    for changes in [
        {"target_id": "missing"},
        {"evidence_refs": []},
        {"estimated_cost": "1.001"},
        {"proposal_id": "bad id"},
    ]:
        with pytest.raises(ValueError):
            create(engine, **changes)
    with pytest.raises(PermissionError):
        create(engine, real_data=True)
    create(engine, estimated_cost="0")
    with pytest.raises(ValueError):
        decide_proposal(
            engine,
            proposal_id="P-01",
            expected_revision=1,
            decision="executed",
            note="no",
            operator="local",
        )
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-01").status == "proposed"
        assert session.get(ActionProposal, "P-01").estimated_cost_cents == 0
        assert len(session.exec(select(ProposalAudit)).all()) == 1


def test_review_reports_unknowns_and_differences_with_revisions(engine):
    base = Path(__file__).resolve().parents[1] / "examples" / "p4"
    names = {
        "order": "orders.csv",
        "expense": "expenses.csv",
        "allocation": "expense_allocations.csv",
        "adjustment": "cost_adjustments.csv",
        "settlement": "settlements.csv",
    }
    files = [InputFile(kind, name, (base / name).read_bytes()) for kind, name in names.items()]
    preview = preflight(
        engine,
        files,
        real_data=False,
        orders_are_mapped=True,
        settlements_cover_full_principal=True,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    report, issues, draft = review(engine, "2026-10-27T12:00:00+08:00")
    assert len(report["rows"]) == 5
    assert any(x["type"] == "unknown_cost" and x["id"] == "OL-04" for x in issues)
    assert any(x["type"] == "settlement_difference" for x in issues)
    assert any(x["type"] == "refund_not_covered" for x in issues)
    assert all(x["revisions"] for x in issues)
    assert "数据时点" in draft and "来源修订" in draft
    with pytest.raises(ValueError):
        review(engine, "2020-01-01T00:00:00+08:00")


def test_ui_create_and_approve(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "ui.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    for label, value in [
        ("提案 ID（字母、数字、下划线或短横线）", "P-UI"),
        ("创建人本地标识", "local"),
    ]:
        next(x for x in app.text_input if x.label == label).set_value(value)
    for label, value in [
        ("变更前快照", "旧"),
        ("建议后快照", "新"),
        ("建议原因", "测试"),
        ("证据索引（每行一个）", "fixture://test"),
    ]:
        next(x for x in app.text_area if x.label == label).set_value(value)
    next(b for b in app.button if b.label == "创建待审提案").click().run()
    assert not app.exception and not app.error
    app.radio[0].set_value("审核与待办").run()
    next(x for x in app.text_area if x.label == "决定依据").set_value("人工复核")
    next(x for x in app.text_input if x.label == "决定人本地标识").set_value("local")
    next(b for b in app.button if b.label == "记录本地决定").click().run()
    assert not app.exception and not app.error
    assert not any("其他会话" in item.value for item in app.warning)
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-UI").status == "approved"
    app.radio[0].set_value("异常与复盘").run()
    assert not app.exception


def test_audit_failure_rolls_back_decision(engine):
    create(engine)

    @event.listens_for(engine, "before_cursor_execute")
    def fail_audit(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("INSERT INTO PROPOSAL_AUDIT"):
            raise RuntimeError("audit write failed")

    with pytest.raises(RuntimeError, match="audit write failed"):
        decide_proposal(
            engine,
            proposal_id="P-01",
            expected_revision=1,
            decision="approved",
            note="人工同意",
            operator="local",
        )
    event.remove(engine, "before_cursor_execute", fail_audit)
    with Session(engine) as session:
        proposal = session.get(ActionProposal, "P-01")
        assert proposal.status == "proposed" and proposal.revision == 1
        assert len(session.exec(select(ProposalAudit)).all()) == 1


def test_review_form_does_not_carry_decision_between_proposals(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "ui-state.db")
    seed(engine)
    create(engine, proposal_id="P-A")
    create(engine, proposal_id="P-B")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    app.radio[0].set_value("审核与待办").run()
    next(x for x in app.text_area if x.label == "决定依据").set_value("仅适用于提案 A")
    next(x for x in app.selectbox if x.label == "决定").set_value("rejected")
    next(x for x in app.button if x.label == "记录本地决定").click().run()
    assert app.error
    assert next(x for x in app.text_area if x.label == "决定依据").value == "仅适用于提案 A"
    next(x for x in app.selectbox if x.label == "选择提案").set_value("P-B").run()
    assert next(x for x in app.text_area if x.label == "决定依据").value == ""
    assert next(x for x in app.text_input if x.label == "决定人本地标识").value == ""
    assert next(x for x in app.selectbox if x.label == "决定").value == "approved"


def test_p5_forms_do_not_carry_state_between_data_spaces(monkeypatch, tmp_path):
    from xhs_seller import db

    demo = make_engine(tmp_path / "demo.db")
    real = make_engine(tmp_path / "real.db")
    for engine in (demo, real):
        seed(engine)
        create(engine)
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    next(x for x in app.text_area if x.label == "建议原因").set_value("演示原因")
    next(x for x in app.button if x.label == "创建待审提案").click().run()
    assert app.error
    assert next(x for x in app.text_area if x.label == "建议原因").value == "演示原因"
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert next(x for x in app.text_area if x.label == "建议原因").value == ""
    app.radio[0].set_value("审核与待办").run()
    next(x for x in app.text_area if x.label == "决定依据").set_value("真实空间依据")
    next(x for x in app.button if x.label == "记录本地决定").click().run()
    assert app.error
    app.sidebar.selectbox[0].set_value("合成演示").run()
    assert next(x for x in app.text_area if x.label == "决定依据").value == ""
    with Session(demo) as session:
        assert session.get(ActionProposal, "P-01").status == "proposed"
    with Session(real) as session:
        assert session.get(ActionProposal, "P-01").status == "proposed"


def test_create_form_does_not_carry_snapshot_to_another_target(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "targets.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    assert next(x for x in app.selectbox if x.label == "目标 ID").value == "PC-01"
    next(x for x in app.text_area if x.label == "变更前快照").set_value("仅属于 PC-01")
    next(x for x in app.button if x.label == "创建待审提案").click().run()
    assert app.error
    assert next(x for x in app.text_area if x.label == "变更前快照").value == "仅属于 PC-01"
    next(x for x in app.selectbox if x.label == "目标 ID").set_value("PC-02").run()
    assert next(x for x in app.text_area if x.label == "变更前快照").value == ""
    next(x for x in app.text_area if x.label == "变更前快照").set_value("仅属于 PC-02")
    next(x for x in app.button if x.label == "创建待审提案").click().run()
    assert app.error
    next(x for x in app.selectbox if x.label == "目标类型").set_value("sku").run()
    assert next(x for x in app.text_area if x.label == "变更前快照").value == ""
    with Session(engine) as session:
        assert not session.exec(select(ActionProposal)).all()
    next(x for x in app.text_input if x.label.startswith("提案 ID")).set_value("P-SKU")
    next(x for x in app.text_input if x.label == "创建人本地标识").set_value("local")
    for label, value in [
        ("变更前快照", "SKU 原状"),
        ("建议后快照", "SKU 建议"),
        ("建议原因", "人工复核"),
        ("证据索引（每行一个）", "fixture://sku-evidence"),
    ]:
        next(x for x in app.text_area if x.label == label).set_value(value)
    next(x for x in app.button if x.label == "创建待审提案").click().run()
    assert not app.exception and not app.error
    with Session(engine) as session:
        proposal = session.get(ActionProposal, "P-SKU")
        assert (proposal.target_type, proposal.target_id, proposal.before_snapshot) == (
            "sku",
            "S-01",
            "SKU 原状",
        )


@pytest.mark.parametrize(
    "ref", ["fixture://research/01", "FIXTURE://research/01", "FiXtUrE://research/01"]
)
def test_real_proposals_reject_fixture_schemes_case_insensitively(engine, ref):
    with pytest.raises(PermissionError):
        create(engine, real_data=True, evidence_refs=[ref])
    with Session(engine) as session:
        assert not session.exec(select(ActionProposal)).all()
        assert not session.exec(select(ProposalAudit)).all()
    proposal = create(engine, real_data=False, evidence_refs=[ref])
    assert proposal.status == "proposed"


def test_competing_decision_not_silently_mistaken_for_local_submit(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "proposal-race.db")
    seed(engine)
    create(engine)
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    app.radio[0].set_value("审核与待办").run()
    next(x for x in app.selectbox if x.label == "决定").set_value("rejected")
    next(x for x in app.text_area if x.label == "决定依据").set_value("本会话拒绝")
    next(x for x in app.text_input if x.label == "决定人本地标识").set_value("reviewer-b")
    decide_proposal(
        engine,
        proposal_id="P-01",
        expected_revision=1,
        decision="approved",
        note="另一会话批准",
        operator="reviewer-a",
    )
    next(b for b in app.button if b.label == "记录本地决定").click().run()
    assert not app.exception
    assert any("其他会话" in item.value and "未记录" in item.value for item in app.warning)
    with Session(engine) as session:
        proposal = session.get(ActionProposal, "P-01")
        audits = session.exec(
            select(ProposalAudit).where(ProposalAudit.proposal_id == "P-01")
        ).all()
        assert proposal.status == "approved" and proposal.decided_by == "reviewer-a"
        assert len(audits) == 2 and audits[-1].decision_note == "另一会话批准"


def test_proposal_and_audit_share_snapshot_during_concurrent_decision(monkeypatch, tmp_path):
    import json
    import sqlite3

    from xhs_seller import db

    path = tmp_path / "audit-snapshot.db"
    engine = make_engine(path)
    seed(engine)
    create(engine)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    writes = []

    def concurrent_decision(conn, cursor, statement, parameters, context, executemany):
        if not writes and statement.lstrip().upper().startswith("SELECT PROPOSAL_AUDIT."):
            writes.append(True)
            decide_proposal(
                engine,
                proposal_id="P-01",
                expected_revision=1,
                decision="approved",
                note="另一会话批准",
                operator="reviewer-a",
            )

    event.listen(engine, "before_cursor_execute", concurrent_decision)
    try:
        app.radio[0].set_value("审核与待办").run()
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_decision)
    assert writes and not app.exception
    assert json.loads(app.json[0].value)["status"] == "proposed"
    assert len(app.json) == 2
    app.run()
    assert json.loads(app.json[0].value)["status"] == "approved"
    assert len(app.json) == 3
    assert any("其他会话" in item.value for item in app.warning)


def test_approved_todo_includes_decision_reason_and_audit_reference(engine):
    create(engine)
    decide_proposal(
        engine,
        proposal_id="P-01",
        expected_revision=1,
        decision="approved",
        note="核对凭据后同意",
        operator="reviewer",
    )
    with Session(engine) as session:
        audits = session.exec(
            select(ProposalAudit).where(ProposalAudit.proposal_id == "P-01")
        ).all()
        approval = next(item for item in audits if item.old_json is not None)
    todo = export_todo(engine, "P-01")
    assert "核对凭据后同意" in todo
    assert approval.audit_id in todo
    assert "reviewer" in todo and "未执行" in todo


def test_approved_todo_rejects_missing_decision_audit(engine):
    create(engine)
    decide_proposal(
        engine,
        proposal_id="P-01",
        expected_revision=1,
        decision="approved",
        note="人工同意",
        operator="reviewer",
    )
    with Session(engine) as session:
        approval = next(item for item in session.exec(select(ProposalAudit)).all() if item.old_json)
        session.delete(approval)
        session.commit()
    with pytest.raises(ValueError, match="审计"):
        export_todo(engine, "P-01")


def test_approved_todo_does_not_export_mismatched_audit(monkeypatch, tmp_path):
    import json

    from xhs_seller import db

    engine = make_engine(tmp_path / "audit-mismatch.db")
    seed(engine)
    create(engine)
    decide_proposal(
        engine,
        proposal_id="P-01",
        expected_revision=1,
        decision="approved",
        note="人工同意",
        operator="reviewer",
    )
    with Session(engine) as session:
        approval = next(item for item in session.exec(select(ProposalAudit)).all() if item.old_json)
        payload = json.loads(approval.new_json)
        payload["after_snapshot"] = "不匹配的内容"
        approval.new_json = json.dumps(payload, ensure_ascii=False)
        session.commit()
    with pytest.raises(ValueError, match="审计"):
        export_todo(engine, "P-01")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    app.radio[0].set_value("审核与待办").run()
    assert not app.exception
    assert any("审计" in error.value for error in app.error)
    assert not any(item.label == "下载人工待办" for item in app.get("download_button"))


def test_proposal_audit_ui_shows_structured_snapshots_and_local_times(monkeypatch, tmp_path):
    import json

    from xhs_seller import db

    engine = make_engine(tmp_path / "audit-display.db")
    seed(engine)
    create(engine)
    decide_proposal(
        engine,
        proposal_id="P-01",
        expected_revision=1,
        decision="approved",
        note="核对完成",
        operator="reviewer",
    )
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    app.radio[0].set_value("审核与待办").run()
    assert not app.exception
    proposal = json.loads(app.json[0].value)
    assert proposal["created_at"].endswith("+08:00")
    assert proposal["decided_at"].endswith("+08:00")
    assert proposal["evidence_refs"] == ["fixture://research/01"]
    audits = [json.loads(item.value) for item in app.json[1:]]
    approval = next(item for item in audits if item["old"] is not None)
    assert approval["old"]["status"] == "proposed"
    assert approval["new"]["status"] == "approved"
    assert approval["decision_note"] == "核对完成"
    assert approval["recorded_at"].endswith("+08:00")
    assert approval["new"]["decided_at"].endswith("+08:00")
    with Session(engine) as session:
        stored = session.get(ActionProposal, "P-01")
        assert stored.decided_at.endswith("+00:00")
        assert json.loads(stored.evidence_refs_json) == ["fixture://research/01"]


def test_review_draft_displays_report_time_in_shanghai(engine):
    report, issues, draft = review(engine, "2026-10-27T04:00:00Z")
    assert issues and not report["rows"]
    assert "数据时点：2026-10-27T12:00:00+08:00" in draft


def test_review_page_displays_utc_input_as_shanghai(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "review-time.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("本地提案与复盘").run()
    app.radio[0].set_value("异常与复盘").run()
    next(x for x in app.text_input if x.label.startswith("复盘数据时点")).set_value(
        "2026-10-27T04:00:00Z"
    ).run()
    assert not app.exception
    assert any("2026-10-27T12:00:00+08:00" in item.value for item in app.caption)
    assert any("2026-10-27T12:00:00+08:00" in item.value for item in app.markdown)
