from pathlib import Path

from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ContentDraft, DraftFact


def element_by_label(elements, label):
    return next(item for item in elements if item.label == label)


def test_offline_fact_to_reviewed_draft_and_export(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("离线草稿").run()
    assert not app.exception
    app.text_input(key="p3_operator_合成演示").set_value("test")
    element_by_label(app.text_input, "事实文本（单行）").set_value("颜色：蓝色")
    element_by_label(app.selectbox, "核验状态").set_value("verified")
    element_by_label(app.text_input, "证据索引").set_value("fixture://facts/blue")
    element_by_label(app.text_input, "核验时间（带时区）").set_value("2026-10-25T10:00:00+08:00")
    app.button(key="FormSubmitter:p3_fact_form_合成演示_S-01_新增-保存事实").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert session.exec(select(DraftFact)).one().text == "颜色：蓝色"
    app.button(key="p3_generate_合成演示_S-01").click().run()
    assert not app.exception
    with Session(engine) as session:
        draft = session.exec(select(ContentDraft)).one()
        draft_id = draft.draft_id
        assert draft.body == "颜色：蓝色"
    assert any("颜色：蓝色" in code.value and "不代表可发布" in code.value for code in app.code)
    element_by_label(app.selectbox, "内部状态").set_value("reviewed")
    app.button(
        key=f"FormSubmitter:p3_draft_form_合成演示_S-01_{draft_id}_v1-保存草稿"
    ).click().run()
    assert not app.exception
    with Session(engine) as session:
        draft = session.get(ContentDraft, draft_id)
        assert draft.status == "reviewed" and draft.revision == 2
    assert any("reviewed" in code.value and "不代表发布" in code.value for code in app.code)
    assert not any("发布" == button.label for button in app.button)


def test_stale_fact_form_does_not_overwrite_concurrent_edit(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.services.drafts import save_fact

    engine = make_engine(tmp_path / "fact-race.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    original = save_fact(
        engine,
        sku_id="S-01",
        kind="product_fact",
        fact_text="原始事实",
        status="unverified",
        evidence_ref="",
        checked_at="",
        operator="test",
        real_data=False,
    )
    app.sidebar.radio[0].set_value("离线草稿").run()
    app.text_input(key="p3_operator_合成演示").set_value("test").run()
    app.selectbox(key="p3_fact_合成演示_S-01").set_value(original.fact_id).run()
    element_by_label(app.text_input, "事实文本（单行）").set_value("旧表单修改")
    save_fact(
        engine,
        sku_id="S-01",
        kind="usage_experience",
        fact_text="原始事实",
        status="unverified",
        evidence_ref="",
        checked_at="",
        operator="test",
        real_data=False,
        fact_id=original.fact_id,
        expected_revision=1,
    )
    next(b for b in app.button if b.label == "保存事实").click().run()
    with Session(engine) as session:
        stored = session.get(DraftFact, original.fact_id)
        assert (
            stored.revision == 2 and stored.text == "原始事实" and stored.kind == "usage_experience"
        )
    assert any("已变化" in item.value for item in app.warning)
    element_by_label(app.text_input, "事实文本（单行）").set_value("重新核对后的修改")
    next(b for b in app.button if b.label == "保存事实").click().run()
    with Session(engine) as session:
        stored = session.get(DraftFact, original.fact_id)
        assert stored.revision == 3 and stored.text == "重新核对后的修改"
        assert stored.kind == "usage_experience"
    assert not any("记录已变化" in item.value for item in app.warning)


def test_stale_asset_form_does_not_overwrite_concurrent_edit(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.models import DraftAsset
    from xhs_seller.services.drafts import save_asset

    engine = make_engine(tmp_path / "asset-race.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    original = save_asset(
        engine,
        sku_id="S-01",
        local_path="images/a.png",
        rights_basis="unknown",
        rights_evidence_ref="",
        operator="test",
        real_data=False,
    )
    app.sidebar.radio[0].set_value("离线草稿").run()
    app.text_input(key="p3_operator_合成演示").set_value("test").run()
    app.selectbox(key="p3_asset_合成演示_S-01").set_value(original.asset_id).run()
    element_by_label(app.text_input, "本地路径索引").set_value("images/stale.png")
    save_asset(
        engine,
        sku_id="S-01",
        local_path="images/a.png",
        rights_basis="manual_owned",
        rights_evidence_ref="fixture://rights",
        operator="test",
        real_data=False,
        rights_confirmed=True,
        asset_id=original.asset_id,
        expected_revision=1,
    )
    next(b for b in app.button if b.label == "保存素材").click().run()
    with Session(engine) as session:
        stored = session.get(DraftAsset, original.asset_id)
        assert stored.revision == 2 and stored.local_path == "images/a.png"
        assert stored.rights_basis == "manual_owned"
    assert any("已变化" in item.value for item in app.warning)


def test_stale_draft_form_does_not_overwrite_concurrent_edit(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.services.drafts import create_draft, save_draft

    engine = make_engine(tmp_path / "draft-race.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    draft = create_draft(engine, sku_id="S-01", operator="test")
    app.sidebar.radio[0].set_value("离线草稿").run()
    app.text_input(key="p3_operator_合成演示").set_value("test").run()
    element_by_label(app.text_area, "补充待核实事项（每行一项）").set_value("旧表单内容")
    save_draft(
        engine,
        draft_id=draft.draft_id,
        expected_revision=1,
        title=draft.title,
        body=draft.body,
        shot_list="拍摄商品细节",
        needs_verification="",
        status="draft",
        operator="test",
    )
    next(b for b in app.button if b.label == "保存草稿").click().run()
    with Session(engine) as session:
        stored = session.get(ContentDraft, draft.draft_id)
        assert stored.revision == 2 and stored.shot_list == "拍摄商品细节"
        assert stored.needs_verification == ""
    assert any("已变化" in item.value for item in app.warning)


def test_draft_history_displays_localized_structured_snapshots(monkeypatch, tmp_path):
    import json

    from xhs_seller import db
    from xhs_seller.services.drafts import create_draft, save_asset, save_fact

    engine = make_engine(tmp_path / "draft-history.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    fact = save_fact(
        engine,
        sku_id="S-01",
        kind="product_fact",
        fact_text="颜色：蓝色",
        status="verified",
        evidence_ref="fixture://facts/blue",
        checked_at="2026-10-25T10:00:00+08:00",
        operator="test",
        real_data=False,
    )
    save_asset(
        engine,
        sku_id="S-01",
        local_path="images/item.png",
        rights_basis="unknown",
        rights_evidence_ref="",
        operator="test",
        real_data=False,
    )
    draft = create_draft(engine, sku_id="S-01", operator="test")
    app.sidebar.radio[0].set_value("离线草稿").run()
    assert not app.exception
    entries = [json.loads(item.value)["新值"] for item in app.json]
    fact_entry = next(item for item in entries if item.get("fact_id") == fact.fact_id)
    draft_entry = next(item for item in entries if item.get("draft_id") == draft.draft_id)
    assert fact_entry["checked_at"] == "2026-10-25T10:00:00+08:00"
    assert fact_entry["updated_at"].endswith("+08:00")
    assert isinstance(draft_entry["facts_json"], list)
    assert draft_entry["facts_json"][0]["evidence_ref"] == "fixture://facts/blue"
    assert draft_entry["facts_json"][0]["checked_at"].endswith("+08:00")
    assert draft_entry["assets_json"][0]["updated_at"].endswith("+08:00")
    assert draft_entry["created_at"].endswith("+08:00")
    with Session(engine) as session:
        stored = session.get(DraftFact, fact.fact_id)
        assert stored.checked_at.endswith("+00:00")


def test_draft_tab_selection_survives_generation(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "draft-tabs.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("离线草稿").run()
    key = "p3_tabs_合成演示_S-01"
    assert app.session_state[key] == "事实记录"
    app.session_state[key] = "草稿与导出"
    app.text_input(key="p3_operator_合成演示").set_value("test")
    app.button(key="p3_generate_合成演示_S-01").click().run()
    assert not app.exception
    assert app.session_state[key] == "草稿与导出"
    with Session(engine) as session:
        assert len(session.exec(select(ContentDraft)).all()) == 1
