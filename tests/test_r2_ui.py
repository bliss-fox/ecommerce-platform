from pathlib import Path

import pytest
from navigation_helpers import navigate
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ContentDraft, DraftAudit, ProductCandidate, RevisionAudit, Sku
from xhs_seller.services.drafts import create_draft, save_fact


def open_app(tmp_path, monkeypatch):
    from xhs_seller import db

    engine = make_engine(tmp_path / "r2.db")
    monkeypatch.setattr(db, "make_engine", lambda *args: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    return app, engine


def test_a33_details_only_show_selected_product_and_keep_context(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "候选与报价")
    assert any("Q-01A" in m.value for m in app.markdown)
    assert not any("Q-03B" in m.value for m in app.markdown)
    app.selectbox(key="r2_product").set_value("PC-03").run()
    assert app.selectbox(key="nav_product_sku_合成演示_PC-03").value == "S-03"
    app.button(key="nav_sku_研究与评分").click().run()
    assert app.selectbox(key="p2_sku_合成演示").value == "S-03"
    navigate(app, "候选与报价")
    assert app.selectbox(key="r2_product").value == "PC-03"
    with Session(engine) as session:
        assert not session.exec(select(RevisionAudit)).all()


def test_a33_comparison_unknown_mismatch_and_no_writes(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "SKU 经济性")
    assert any("¥8.40" in m.value for m in app.markdown)
    app.selectbox(key="r2_compare_S-01_v1").set_value("Q-01B").run()
    assert any("¥8.00" in m.value for m in app.markdown)
    assert any("已保存报价：Q-01A" in m.value for m in app.markdown)
    app.selectbox(key="nav_product_sku_合成演示").set_value("S-03").run()
    assert any("¥8.60" in m.value for m in app.markdown)
    app.selectbox(key="r2_compare_S-03_v1").set_value("Q-03B").run()
    assert any("不可完整计算" in m.value for m in app.markdown)
    assert any("发货" in m.value and "获客" in m.value for m in app.markdown)
    app.selectbox(key="nav_product_sku_合成演示").set_value("S-02").run()
    app.selectbox(key="r2_compare_S-02_v1").set_value("Q-02B").run()
    assert any("场景不匹配" in m.value for m in app.warning)
    with Session(engine) as session:
        assert session.get(Sku, "S-01").quote_id == "Q-01A"
        assert not session.exec(select(RevisionAudit)).all()
    assert not app.exception


def test_a33_product_without_sku_and_invalid_selection(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    with Session(engine) as session:
        source = session.get(ProductCandidate, "PC-01").model_dump()
        session.add(ProductCandidate(**dict(source, product_id="PC-empty")))
        session.commit()
    navigate(app, "候选与报价")
    app.selectbox(key="r2_product").set_value("PC-empty").run()
    assert any("尚无 SKU" in i.value for i in app.info)
    assert not any(s.label == "选择 SKU" for s in app.selectbox)
    assert "nav_sku" not in app.session_state
    with Session(engine) as session:
        session.delete(session.get(ProductCandidate, "PC-empty"))
        session.commit()
    app.run()
    assert any("不存在" in e.value for e in app.error)
    assert not app.exception


def test_a34_handoff_resets_preview_and_requires_confirmation(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "编辑与导入")
    app.radio[0].set_value("CSV 导入").run()
    app.session_state["p1_preview"] = "old preview"
    navigate(app, "SKU 经济性")
    app.button(key="r2_edit_sku_S-01").click().run()
    assert not app.exception
    assert app.radio[0].value == "手工编辑"
    assert app.selectbox(key="edit_record_合成演示_sku").value == "S-01"
    assert "p1_preview" not in app.session_state
    assert any(e.label == "版本详情" and not e.proto.expanded for e in app.expander)
    app.text_input(key="field_合成演示_sku_S-01_v1_quote_id").set_value("Q-01B")
    app.button(key="FormSubmitter:edit_form_合成演示_sku_S-01_v1-预检编辑").click().run()
    with Session(engine) as session:
        assert session.get(Sku, "S-01").quote_id == "Q-01A"
    next(t for t in app.text_input if t.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    with Session(engine) as session:
        sku = session.get(Sku, "S-01")
        assert sku.quote_id == "Q-01B" and sku.revision == 2
        assert len(session.exec(select(RevisionAudit)).all()) == 1


def fact(engine, text, status="verified"):
    return save_fact(
        engine,
        sku_id="S-01",
        kind="product_fact",
        fact_text=text,
        status=status,
        evidence_ref="fixture://r2/fact" if status == "verified" else "",
        checked_at="2026-10-25T10:00:00+08:00" if status == "verified" else "",
        operator="test",
        real_data=False,
    )


def test_a35_fact_picker_only_changes_form_and_stale_sources_block(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    fact(engine, "颜色：蓝色")
    fact(engine, "尺寸：10厘米")
    fact(engine, "重量待核实", "unverified")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    with Session(engine) as session:
        audit_count = len(session.exec(select(DraftAudit)).all())
    navigate(app, "离线草稿")
    assert any("重量待核实" in m.value for m in app.warning)
    assert any("尚无素材" in m.value for m in app.warning)
    picker = app.multiselect[0]
    assert "重量待核实" not in picker.options
    picker.set_value(["尺寸：10厘米"]).run()
    next(s for s in app.selectbox if s.label == "选用标题事实").set_value("颜色：蓝色").run()
    next(b for b in app.button if b.label == "填入编辑表单").click().run()
    assert (
        next(t for t in app.text_area if t.label == "事实正文（每行一条）").value == "尺寸：10厘米"
    )
    assert next(t for t in app.text_input if t.label == "标题").value == "颜色：蓝色"
    with Session(engine) as session:
        assert session.get(ContentDraft, draft.draft_id).revision == 1
        assert len(session.exec(select(DraftAudit)).all()) == audit_count
    app.text_input(key="p3_operator_合成演示").set_value("test")
    next(b for b in app.button if b.label == "保存草稿").click().run()
    with Session(engine) as session:
        assert session.get(ContentDraft, draft.draft_id).body == "尺寸：10厘米"
    fact(engine, "新事实")
    app.run()
    assert any("重新生成" in e.value for e in app.error)
    assert not any(b.label in {"保存草稿", "填入编辑表单"} for b in app.button)
    assert not app.get("download_button")
    assert not app.exception


@pytest.mark.parametrize(
    "kind,record_id,parent_id",
    [
        ("quote", "Q-02B", "PC-02"),
        ("product", "PC-02", None),
    ],
)
def test_a34_editor_reloads_exact_target(tmp_path, monkeypatch, kind, record_id, parent_id):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "候选与报价")
    app.selectbox(key="r2_product").set_value("PC-02").run()
    app.button(key=f"r2_edit_{kind}_{record_id}").click().run()
    assert not app.exception
    assert app.selectbox(key=f"edit_record_合成演示_{kind}").value == record_id
    with Session(engine) as session:
        assert not session.exec(select(RevisionAudit)).all()


@pytest.mark.parametrize(
    "kind,record_id,parent_id",
    [
        ("quote", "Q-02B", "PC-01"),
        ("sku", "S-missing", None),
        ("product", "PC-missing", None),
    ],
)
def test_a34_invalid_edit_target_never_opens_another_form(
    tmp_path,
    monkeypatch,
    kind,
    record_id,
    parent_id,
):
    app, _ = open_app(tmp_path, monkeypatch)
    navigate(app, "编辑与导入")
    app.session_state["nav_target"] = (
        "编辑与导入",
        kind,
        record_id,
        parent_id,
        app.session_state.nav_space,
    )
    app.run()
    assert app.error and not app.exception
    assert not any(b.label == "预检编辑" for b in app.button)


def test_a35_picker_does_not_cross_sku_or_data_space(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    fact(engine, "S-01 专属事实")
    draft = create_draft(engine, sku_id="S-01", operator="test")
    create_draft(engine, sku_id="S-02", operator="test")
    navigate(app, "离线草稿")
    app.multiselect[0].set_value([]).run()
    next(b for b in app.button if b.label == "填入编辑表单").click().run()
    app.selectbox(key="p3_sku_合成演示").set_value("S-02").run()
    assert app.multiselect[0].options == []
    assert next(t for t in app.text_area if t.label == "事实正文（每行一条）").value == ""
    app.sidebar.selectbox(key="data_space").set_value("真实数据").run()
    assert not app.exception
    for field in ("title", "body", "pick_title", "pick_body"):
        assert f"p3_editor_合成演示_S-01_{draft.draft_id}_v1_{field}" not in app.session_state


def test_a33_real_empty_catalog_has_entry(tmp_path, monkeypatch):
    from xhs_seller import db

    app, demo = open_app(tmp_path, monkeypatch)
    real = make_engine(tmp_path / "real.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    app.sidebar.selectbox(key="data_space").set_value("真实数据").run()
    navigate(app, "候选与报价")
    assert any("尚未录入候选" in i.value for i in app.info)
    assert any(b.label == "录入候选" for b in app.button)
    assert not any("Q-01A" in m.value for m in app.markdown)
    assert not app.exception
