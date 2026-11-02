"""A20: incomplete proposal history cannot authorize a local decision or todo."""

import json
from pathlib import Path

import pytest
from navigation_helpers import navigate
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ActionProposal, ProposalAudit
from xhs_seller.seed_demo import seed
from xhs_seller.services.proposals import create_proposal, decide_proposal, export_todo


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "audit-integrity.db")
    seed(engine)
    create(engine, "P-01")
    return engine


def create(engine, proposal_id):
    return create_proposal(
        engine,
        proposal_id=proposal_id,
        action_type="reprice",
        target_type="sku",
        target_id="S-01",
        before="售价 100",
        after="售价 110",
        reason="人工研究",
        evidence_refs=["fixture://research/01"],
        estimated_cost="",
        operator="local",
        real_data=False,
    )


def decide(engine, proposal_id="P-01", decision="approved"):
    return decide_proposal(
        engine,
        proposal_id=proposal_id,
        expected_revision=1,
        decision=decision,
        note="人工核对同意",
        operator="reviewer",
    )


def damage_history(engine, damage):
    with Session(engine) as session:
        audits = session.exec(
            select(ProposalAudit).where(ProposalAudit.proposal_id == "P-01")
        ).all()
        creation = next(item for item in audits if item.old_json is None)
        decision = next((item for item in audits if item.old_json is not None), None)
        if damage == "missing_creation":
            session.delete(creation)
        elif damage == "duplicate_creation":
            session.add(ProposalAudit(**{**creation.model_dump(), "audit_id": "duplicate"}))
        elif damage == "changed_creation":
            snapshot = json.loads(creation.new_json)
            snapshot["after_snapshot"] = "另一建议"
            creation.new_json = json.dumps(snapshot, ensure_ascii=False)
        elif damage == "broken_json":
            creation.new_json = '{"private_marker":'
        elif damage == "wrong_shape":
            creation.new_json = '["private_marker"]'
        elif damage == "changed_old_snapshot":
            snapshot = json.loads(decision.old_json)
            snapshot["reason"] = "另一原因"
            decision.old_json = json.dumps(snapshot, ensure_ascii=False)
        elif damage == "missing_old_fields":
            decision.old_json = '{"status":"proposed"}'
        elif damage == "duplicate_decision":
            session.add(ProposalAudit(**{**decision.model_dump(), "audit_id": "duplicate"}))
        elif damage == "empty_decision_note":
            decision.decision_note = " "
        elif damage == "wrong_operator":
            decision.operator = "another-reviewer"
        elif damage == "wrong_time":
            decision.recorded_at = creation.recorded_at
        else:
            raise AssertionError(damage)
        session.commit()


@pytest.mark.parametrize(
    "damage",
    ["missing_creation", "duplicate_creation", "changed_creation", "broken_json", "wrong_shape"],
)
@pytest.mark.parametrize("decision", ["approved", "rejected", "cancelled", "expired"])
def test_incomplete_creation_history_blocks_every_decision(engine, damage, decision):
    damage_history(engine, damage)
    with Session(engine) as session:
        before = session.get(ActionProposal, "P-01").model_dump()
        audits_before = [item.model_dump() for item in session.exec(select(ProposalAudit)).all()]
    with pytest.raises(ValueError, match="审计") as error:
        decide(engine, decision=decision)
    assert "private_marker" not in str(error.value)
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-01").model_dump() == before
        assert [item.model_dump() for item in session.exec(select(ProposalAudit)).all()] == audits_before


@pytest.mark.parametrize(
    "damage",
    [
        "missing_creation", "duplicate_creation", "changed_creation", "broken_json", "wrong_shape",
        "changed_old_snapshot", "missing_old_fields", "duplicate_decision", "empty_decision_note",
        "wrong_operator", "wrong_time",
    ],
)
def test_incomplete_approved_history_blocks_export(engine, damage):
    decide(engine)
    damage_history(engine, damage)
    with pytest.raises(ValueError, match="审计") as error:
        export_todo(engine, "P-01")
    assert "private_marker" not in str(error.value)


@pytest.mark.parametrize("approved", [False, True])
@pytest.mark.parametrize("damage", ["missing_creation", "broken_json", "wrong_shape"])
def test_invalid_history_page_is_contained_and_other_proposals_work(
    engine, monkeypatch, approved, damage
):
    from xhs_seller import db

    if approved:
        decide(engine)
    damage_history(engine, damage)
    create(engine, "P-02")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    navigate(app, "本地提案与复盘")
    app.radio[0].set_value("审核与待办").run()
    assert not app.exception
    assert any("审计" in item.value for item in app.error)
    assert all("private_marker" not in item.value for item in app.error)
    assert not any(item.label == "记录本地决定" for item in app.button)
    assert not any(item.label == "下载人工待办" for item in app.get("download_button"))
    next(item for item in app.selectbox if item.label == "选择提案").set_value("P-02").run()
    assert not app.exception and not app.error
    next(item for item in app.text_area if item.label == "决定依据").set_value("核对完整")
    next(item for item in app.text_input if item.label == "决定人本地标识").set_value("local")
    next(item for item in app.button if item.label == "记录本地决定").click().run()
    assert not app.exception and not app.error
    assert any(item.label == "下载人工待办" for item in app.get("download_button"))
