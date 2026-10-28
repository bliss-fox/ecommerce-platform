import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, select

from xhs_seller.db import make_engine
from xhs_seller.models import (
    ActionProposal,
    ContentDraft,
    DraftAsset,
    DraftAudit,
    DraftFact,
    ProposalAudit,
)
from xhs_seller.seed_demo import seed
from xhs_seller.services.drafts import PLACEHOLDER, create_draft, save_asset, save_draft, save_fact
from xhs_seller.services.proposals import create_proposal, decide_proposal


@pytest.mark.parametrize("offset_microseconds", [0, -1])
def test_local_revisions_reject_a_nonadvancing_clock(monkeypatch, tmp_path, offset_microseconds):
    from xhs_seller.services import drafts, proposals

    engine = make_engine(tmp_path / "local.db")
    seed(engine)
    fact = save_fact(
        engine,
        sku_id="S-01",
        kind="product_fact",
        fact_text="待核实事实",
        status="unverified",
        evidence_ref="",
        checked_at="",
        operator="local",
        real_data=False,
    )
    asset = save_asset(
        engine,
        sku_id="S-01",
        local_path="images/example.jpg",
        rights_basis="unknown",
        rights_evidence_ref="",
        operator="local",
        real_data=False,
    )
    draft = create_draft(engine, sku_id="S-01", operator="local")
    proposal = create_proposal(
        engine,
        proposal_id="P-LOCAL",
        action_type="reprice",
        target_type="sku",
        target_id="S-01",
        before="原价",
        after="建议价",
        reason="本地测试",
        evidence_refs=["fixture://evidence"],
        estimated_cost="",
        operator="local",
        real_data=False,
    )
    cases = [
        (
            fact.updated_at,
            lambda: save_fact(
                engine,
                sku_id="S-01",
                kind="product_fact",
                fact_text="更改事实",
                status="unverified",
                evidence_ref="",
                checked_at="",
                operator="local",
                real_data=False,
                fact_id=fact.fact_id,
                expected_revision=1,
            ),
        ),
        (
            asset.updated_at,
            lambda: save_asset(
                engine,
                sku_id="S-01",
                local_path="images/new.jpg",
                rights_basis="unknown",
                rights_evidence_ref="",
                operator="local",
                real_data=False,
                asset_id=asset.asset_id,
                expected_revision=1,
            ),
        ),
        (
            draft.updated_at,
            lambda: save_draft(
                engine,
                draft_id=draft.draft_id,
                expected_revision=1,
                title=PLACEHOLDER,
                body="",
                shot_list="",
                needs_verification="",
                status="reviewed",
                operator="local",
            ),
        ),
    ]
    for old_time, save in cases:
        timestamp = datetime.fromisoformat(old_time) + timedelta(microseconds=offset_microseconds)
        monkeypatch.setattr(drafts, "now", lambda timestamp=timestamp: timestamp.isoformat())
        with pytest.raises(ValueError, match="时间"):
            save()
    timestamp = datetime.fromisoformat(proposal.updated_at) + timedelta(
        microseconds=offset_microseconds
    )
    monkeypatch.setattr(proposals, "now", lambda: timestamp.isoformat())
    with pytest.raises(ValueError, match="时间"):
        decide_proposal(
            engine,
            proposal_id=proposal.proposal_id,
            expected_revision=1,
            decision="approved",
            note="人工确认",
            operator="local",
        )
    with Session(engine) as session:
        assert session.get(DraftFact, fact.fact_id).revision == 1
        assert session.get(DraftAsset, asset.asset_id).revision == 1
        assert session.get(ContentDraft, draft.draft_id).revision == 1
        assert session.get(ActionProposal, proposal.proposal_id).status == "proposed"
        audits = session.exec(select(DraftAudit)).all()
        assert len(audits) == 3
        assert all(audit.updated_at == json.loads(audit.new_json)["updated_at"] for audit in audits)
        assert len(session.exec(select(ProposalAudit)).all()) == 1
