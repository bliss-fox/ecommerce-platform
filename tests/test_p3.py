import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from xhs_seller.db import make_engine
from xhs_seller.models import ContentDraft, DraftAudit, DraftFact
from xhs_seller.seed_demo import seed
from xhs_seller.services.drafts import (
    PLACEHOLDER,
    StaleDraftError,
    create_draft,
    render_markdown,
    save_asset,
    save_draft,
    save_fact,
)


@pytest.fixture
def engine(tmp_path):
    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    return engine


def fact(
    engine,
    text,
    *,
    status="verified",
    kind="product_fact",
    evidence="fixture://facts/1",
    checked="2026-10-25T11:00:00+08:00",
    **kwargs,
):
    return save_fact(
        engine,
        sku_id="S-01",
        kind=kind,
        fact_text=text,
        status=status,
        evidence_ref=evidence,
        checked_at=checked,
        operator="test",
        real_data=False,
        **kwargs,
    )


def test_a11_offline_template_only_verified_facts(engine):
    fact(engine, "尺寸：10 cm")
    fact(engine, "颜色：蓝色")
    fact(engine, "销量第一", status="unverified", evidence="", checked="")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    assert draft.status == "draft" and draft.title == PLACEHOLDER
    assert set(draft.body.splitlines()) == {"尺寸：10 cm", "颜色：蓝色"}
    assert (
        "我用了半个月" not in draft.body
        and "销量第一" not in draft.body
        and "大家都说好" not in draft.body
    )
    exported = render_markdown(engine, draft.draft_id)
    assert "尺寸：10 cm" in exported and "颜色：蓝色" in exported
    assert "待核实：销量第一" in exported
    assert "尚无素材记录" in exported and "不代表可发布或已发布" in exported
    assert "fixture://facts/1" in exported
    saved = save_draft(
        engine,
        draft_id=draft.draft_id,
        expected_revision=1,
        title="尺寸：10 cm",
        body="颜色：蓝色\n尺寸：10 cm",
        shot_list="拍摄整体",
        needs_verification="核实素材权利",
        status="reviewed",
        operator="test",
    )
    assert saved.revision == 2 and saved.status == "reviewed"
    text = render_markdown(engine, draft.draft_id)
    assert "不代表发布" in text
    title = text.split("## 标题", 1)[1].split("## 已核实事实正文", 1)[0]
    assert "fixture://facts/1" in title and "核验时间" in title
    with Session(engine) as session:
        audits = session.exec(
            select(DraftAudit).where(DraftAudit.record_id == draft.draft_id)
        ).all()
        assert len(audits) == 2 and audits[-1].operator == "test"


def test_evidence_and_authored_claims_are_required(engine):
    with pytest.raises(ValueError, match="证据索引"):
        fact(engine, "尺寸：10 cm", evidence="")
    with pytest.raises(ValueError, match="使用经历"):
        fact(engine, "我用了半个月")
    usage = fact(
        engine,
        "我用了半个月",
        kind="usage_experience",
        status="unverified",
        evidence="",
        checked="",
    )
    draft = create_draft(engine, sku_id="S-01", operator="test")
    assert "我用了半个月" not in draft.body
    assert "待核实：我用了半个月" in render_markdown(engine, draft.draft_id)
    with pytest.raises(ValueError, match="正文"):
        save_draft(
            engine,
            draft_id=draft.draft_id,
            expected_revision=1,
            title=PLACEHOLDER,
            body=usage.text,
            shot_list="",
            needs_verification="",
            status="draft",
            operator="test",
        )
    with pytest.raises(ValueError, match="标题"):
        save_draft(
            engine,
            draft_id=draft.draft_id,
            expected_revision=1,
            title="销量第一",
            body="",
            shot_list="",
            needs_verification="",
            status="draft",
            operator="test",
        )


def test_asset_rights_unknown_and_changed_source_blocks_export(engine):
    item = fact(engine, "尺寸：10 cm")
    asset = save_asset(
        engine,
        sku_id="S-01",
        local_path="images/item.jpg",
        rights_basis="unknown",
        rights_evidence_ref="",
        operator="test",
        real_data=False,
    )
    draft = create_draft(engine, sku_id="S-01", operator="test")
    assert "待核实素材权利" in render_markdown(engine, draft.draft_id)
    with pytest.raises(ValueError, match="依据索引"):
        save_asset(
            engine,
            sku_id="S-01",
            local_path="images/item.jpg",
            rights_basis="authorized",
            rights_evidence_ref="",
            operator="test",
            real_data=False,
        )
    fact(
        engine,
        "尺寸：11 cm",
        fact_id=item.fact_id,
        expected_revision=1,
        checked="2026-10-26T11:00:00+08:00",
    )
    with pytest.raises(StaleDraftError, match="重新生成"):
        render_markdown(engine, draft.draft_id)
    with pytest.raises(StaleDraftError):
        save_draft(
            engine,
            draft_id=draft.draft_id,
            expected_revision=1,
            title=PLACEHOLDER,
            body=draft.body,
            shot_list="",
            needs_verification="",
            status="draft",
            operator="test",
        )
    with pytest.raises(StaleDraftError):
        save_asset(
            engine,
            sku_id="S-01",
            local_path="images/new.jpg",
            rights_basis="unknown",
            rights_evidence_ref="",
            operator="test",
            real_data=False,
            asset_id=asset.asset_id,
            expected_revision=0,
        )


def test_invalid_boundaries_and_markdown_escaping(engine):
    with pytest.raises(ValueError, match="操作者"):
        create_draft(engine, sku_id="S-01", operator="")
    with pytest.raises(ValueError, match="本地路径"):
        save_asset(
            engine,
            sku_id="S-01",
            local_path="../secret.jpg",
            rights_basis="unknown",
            rights_evidence_ref="",
            operator="test",
            real_data=False,
        )
    with pytest.raises(PermissionError):
        save_fact(
            engine,
            sku_id="S-01",
            kind="product_fact",
            fact_text="颜色：蓝色",
            status="verified",
            evidence_ref="fixture://demo",
            checked_at="2026-10-25T11:00:00+08:00",
            operator="test",
            real_data=True,
        )
    with pytest.raises(ValueError, match="时区"):
        fact(engine, "颜色：蓝色", checked="2026-10-25T11:00:00")
    fact(engine, "颜色：[蓝色](https://example.test)")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    text = render_markdown(engine, draft.draft_id)
    assert "\\[蓝色\\]\\(https://example\\.test\\)" in text


def test_storage_failure_rolls_back_fact_and_audit(engine):
    def fail(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO draft_audit"):
            raise RuntimeError("simulated failure")

    event.listen(engine, "before_cursor_execute", fail)
    try:
        with pytest.raises(RuntimeError, match="simulated"):
            fact(engine, "颜色：蓝色")
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    with Session(engine) as session:
        assert not session.exec(select(DraftFact)).all()
        assert not session.exec(select(DraftAudit)).all()
        assert not session.exec(select(ContentDraft)).all()


def test_new_or_silently_changed_sources_make_existing_draft_stale(engine):
    draft = create_draft(engine, sku_id="S-01", operator="test")
    item = fact(engine, "颜色：蓝色")
    with pytest.raises(StaleDraftError):
        render_markdown(engine, draft.draft_id)
    updated = create_draft(engine, sku_id="S-01", operator="test")
    with Session(engine) as session:
        record = session.get(DraftFact, item.fact_id)
        record.text = "未经修订的修改"
        session.commit()
    with pytest.raises(StaleDraftError):
        render_markdown(engine, updated.draft_id)


def test_reviewed_content_change_requires_new_review(engine):
    fact(engine, "尺寸：10 cm")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    reviewed = save_draft(
        engine,
        draft_id=draft.draft_id,
        expected_revision=1,
        title=PLACEHOLDER,
        body=draft.body,
        shot_list=draft.shot_list,
        needs_verification="",
        status="reviewed",
        operator="test",
    )
    with pytest.raises(ValueError, match="重新审核"):
        save_draft(
            engine,
            draft_id=draft.draft_id,
            expected_revision=2,
            title=PLACEHOLDER,
            body=reviewed.body,
            shot_list="拍摄商品细节",
            needs_verification="",
            status="reviewed",
            operator="test",
        )
    new = save_draft(
        engine,
        draft_id=draft.draft_id,
        expected_revision=2,
        title=PLACEHOLDER,
        body=reviewed.body,
        shot_list="拍摄商品细节",
        needs_verification="",
        status="draft",
        operator="test",
    )
    assert new.status == "draft" and new.revision == 3


def test_evidence_reference_must_not_inject_new_markdown_lines(engine):
    with pytest.raises(ValueError, match="单行"):
        fact(engine, "颜色：蓝色", evidence="fixture://evidence\n# 错误章节")


def test_changed_verified_fact_requires_new_verification_time(engine):
    item = fact(engine, "颜色：蓝色")
    with pytest.raises(ValueError, match="重新核验"):
        fact(engine, "颜色：红色", fact_id=item.fact_id, expected_revision=1)
    changed = fact(
        engine,
        "颜色：红色",
        fact_id=item.fact_id,
        expected_revision=1,
        checked="2026-10-26T11:00:00+08:00",
    )
    assert changed.revision == 2 and changed.text == "颜色：红色"


def test_asset_rights_must_be_explicitly_confirmed(engine):
    kwargs = {
        "sku_id": "S-01",
        "local_path": "images/item.jpg",
        "rights_basis": "authorized",
        "rights_evidence_ref": "fixture://rights/1",
        "operator": "test",
        "real_data": False,
    }
    with pytest.raises(ValueError, match="确认权利"):
        save_asset(engine, **kwargs)
    item = save_asset(engine, **kwargs, rights_confirmed=True)
    with pytest.raises(ValueError, match="确认权利"):
        save_asset(
            engine,
            **(kwargs | {"local_path": "images/other.jpg"}),
            asset_id=item.asset_id,
            expected_revision=1,
        )


def test_shot_list_cannot_present_unverified_claim(engine):
    fact(engine, "颜色：蓝色")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    with pytest.raises(ValueError, match="拍摄清单"):
        save_draft(
            engine,
            draft_id=draft.draft_id,
            expected_revision=1,
            title=PLACEHOLDER,
            body=draft.body,
            shot_list="销量第一",
            needs_verification="",
            status="draft",
            operator="test",
        )
    updated = save_draft(
        engine,
        draft_id=draft.draft_id,
        expected_revision=1,
        title=PLACEHOLDER,
        body=draft.body,
        shot_list="拍摄已核实事实：颜色：蓝色",
        needs_verification="销量第一",
        status="draft",
        operator="test",
    )
    text = render_markdown(engine, updated.draft_id)
    assert "拍摄已核实事实：颜色：蓝色" in text
    assert "销量第一" in text.split("## 待核实清单", 1)[1]


def test_evidenced_personal_experience_must_be_explicitly_recorded(engine):
    fact(engine, "我用了半个月", kind="usage_experience")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    assert "我用了半个月" in draft.body
    assert "fixture://facts/1" in render_markdown(engine, draft.draft_id)


def test_real_fact_rejects_mixed_case_fixture_reference(engine):
    with pytest.raises(PermissionError):
        save_fact(
            engine,
            sku_id="S-01",
            kind="product_fact",
            fact_text="颜色：蓝色",
            status="verified",
            evidence_ref="FiXtUrE://demo",
            checked_at="2026-10-25T11:00:00+08:00",
            operator="test",
            real_data=True,
        )
    with Session(engine) as session:
        assert not session.exec(select(DraftFact)).all()


def test_unverified_intermediate_cannot_reuse_old_verification(engine):
    original = fact(engine, "尺寸：10 cm", checked="2026-10-25T11:00:00+08:00")
    interim = fact(
        engine,
        "尺寸：11 cm",
        status="unverified",
        evidence="",
        checked="",
        fact_id=original.fact_id,
        expected_revision=original.revision,
    )
    with pytest.raises(ValueError, match="更晚"):
        fact(
            engine,
            "尺寸：11 cm",
            checked="2026-10-25T11:00:00+08:00",
            fact_id=interim.fact_id,
            expected_revision=interim.revision,
        )
    with Session(engine) as session:
        current = session.get(DraftFact, original.fact_id)
        assert current.status == "unverified" and current.revision == 2
    verified = fact(
        engine,
        "尺寸：11 cm",
        checked="2026-10-25T11:00:01+08:00",
        fact_id=interim.fact_id,
        expected_revision=interim.revision,
    )
    assert verified.status == "verified" and verified.revision == 3


def test_unchanged_fact_can_return_to_previous_verification(engine):
    original = fact(engine, "尺寸：10 cm")
    interim = fact(
        engine,
        "尺寸：10 cm",
        status="unverified",
        evidence="",
        checked="",
        fact_id=original.fact_id,
        expected_revision=original.revision,
    )
    verified = fact(
        engine,
        "尺寸：10 cm",
        fact_id=interim.fact_id,
        expected_revision=interim.revision,
    )
    assert verified.status == "verified" and verified.revision == 3


@pytest.mark.parametrize(
    "changes", [{"evidence": "fixture://facts/new"}, {"kind": "usage_experience"}]
)
def test_reverification_with_changed_evidence_or_kind_needs_new_time(engine, changes):
    original = fact(engine, "尺寸：10 cm")
    interim = fact(
        engine,
        "尺寸：10 cm",
        status="unverified",
        evidence="",
        checked="",
        fact_id=original.fact_id,
        expected_revision=original.revision,
    )
    with pytest.raises(ValueError, match="更晚"):
        fact(
            engine,
            "尺寸：10 cm",
            fact_id=interim.fact_id,
            expected_revision=interim.revision,
            **changes,
        )
    verified = fact(
        engine,
        "尺寸：10 cm",
        checked="2026-10-25T11:00:01+08:00",
        fact_id=interim.fact_id,
        expected_revision=interim.revision,
        **changes,
    )
    assert verified.status == "verified"


@pytest.mark.parametrize("via_unverified", [False, True])
def test_verified_fact_cannot_backdate_same_content(engine, via_unverified):
    original = fact(engine, "尺寸：10 cm", checked="2026-10-25T11:00:00+08:00")
    current = original
    if via_unverified:
        current = fact(
            engine,
            "尺寸：10 cm",
            status="unverified",
            evidence="",
            checked="",
            fact_id=original.fact_id,
            expected_revision=original.revision,
        )
    with pytest.raises(ValueError, match="核验时间"):
        fact(
            engine,
            "尺寸：10 cm",
            checked="2026-10-24T11:00:00+08:00",
            fact_id=current.fact_id,
            expected_revision=current.revision,
        )
    with Session(engine) as session:
        saved = session.get(DraftFact, original.fact_id)
        assert saved.revision == current.revision


def test_earlier_legacy_revision_does_not_lower_verified_time_floor(engine):
    from xhs_seller.services.drafts import audit

    original = fact(engine, "尺寸：10 cm", checked="2026-10-25T11:00:00+08:00")
    with Session(engine) as session:
        current = session.get(DraftFact, original.fact_id)
        old = current.model_dump()
        current.revision = 2
        current.checked_at = "2026-10-24T03:00:00+00:00"
        audit(session, "fact", current, old, "test")
        session.commit()
    with pytest.raises(ValueError, match="核验时间"):
        fact(
            engine,
            "尺寸：11 cm",
            checked="2026-10-24T12:00:00+08:00",
            fact_id=original.fact_id,
            expected_revision=2,
        )
    recovered = fact(
        engine,
        "尺寸：11 cm",
        checked="2026-10-25T11:00:01+08:00",
        fact_id=original.fact_id,
        expected_revision=2,
    )
    assert recovered.revision == 3 and recovered.status == "verified"


def test_reviewed_draft_cannot_change_content_through_rejected_state(engine):
    fact(engine, "尺寸：10 cm")
    draft = create_draft(engine, sku_id="S-01", operator="test")

    def save(current, *, status, shots):
        return save_draft(
            engine,
            draft_id=current.draft_id,
            expected_revision=current.revision,
            title=current.title,
            body=current.body,
            shot_list=shots,
            needs_verification=current.needs_verification,
            status=status,
            operator="test",
        )

    reviewed = save(draft, status="reviewed", shots=draft.shot_list)
    with pytest.raises(ValueError, match="重新审核"):
        save(reviewed, status="rejected", shots="拍摄商品细节")
    rejected = save(reviewed, status="rejected", shots=reviewed.shot_list)
    with pytest.raises(ValueError, match="重新审核"):
        save(rejected, status="rejected", shots="拍摄商品细节")
    with pytest.raises(ValueError, match="重新审核"):
        save(rejected, status="reviewed", shots="拍摄商品细节")
    with Session(engine) as session:
        stored = session.get(ContentDraft, draft.draft_id)
        audits = session.exec(
            select(DraftAudit).where(DraftAudit.record_id == draft.draft_id)
        ).all()
        assert stored.revision == rejected.revision and stored.status == "rejected"
        assert len(audits) == 3
    modified = save(rejected, status="draft", shots="拍摄商品细节")
    rereviewed = save(modified, status="reviewed", shots=modified.shot_list)
    assert rereviewed.status == "reviewed" and rereviewed.shot_list == "拍摄商品细节"


def test_reviewed_draft_cannot_change_during_rereview(engine):
    fact(engine, "尺寸：10 cm")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    reviewed = save_draft(
        engine,
        draft_id=draft.draft_id,
        expected_revision=draft.revision,
        title=draft.title,
        body=draft.body,
        shot_list=draft.shot_list,
        needs_verification="",
        status="reviewed",
        operator="test",
    )
    draft_again = save_draft(
        engine,
        draft_id=reviewed.draft_id,
        expected_revision=reviewed.revision,
        title=reviewed.title,
        body=reviewed.body,
        shot_list=reviewed.shot_list,
        needs_verification="",
        status="draft",
        operator="test",
    )
    with pytest.raises(ValueError, match="重新审核"):
        save_draft(
            engine,
            draft_id=draft_again.draft_id,
            expected_revision=draft_again.revision,
            title=draft_again.title,
            body=draft_again.body,
            shot_list="拍摄商品细节",
            needs_verification="",
            status="reviewed",
            operator="test",
        )
