from pathlib import Path

from navigation_helpers import navigate
from sqlmodel import Session
from streamlit.testing.v1 import AppTest
from test_r1 import create
from test_r1 import engine as engine_fixture

from xhs_seller.models import Sku

engine = engine_fixture


def open_app(engine, monkeypatch):
    from xhs_seller import db

    monkeypatch.setattr(db, "make_engine", lambda *args: engine)
    return AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()


def test_a23_six_groups_sku_handoff_and_saved_quote_unchanged(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    assert not app.exception
    assert app.sidebar.radio[0].options == [
        "协作工作台",
        "选品工作区",
        "内容工作区",
        "经营核对",
        "决策记录",
        "数据管理",
    ]
    navigate(app, "SKU 经济性")
    app.selectbox(key="nav_product_sku_合成演示").set_value("S-03").run()
    app.button(key="nav_sku_研究与评分").click().run()
    assert app.selectbox(key="p2_sku_合成演示").value == "S-03"
    app.selectbox(key="p2_quote_合成演示_S-03").set_value("Q-03B").run()
    app.button(key="nav_sku_离线草稿").click().run()
    assert not app.exception
    assert app.selectbox(key="p3_sku_合成演示").value == "S-03"
    assert any("S-03" in m.value and "合成演示规格 03" in m.value for m in app.markdown)
    with Session(engine) as session:
        assert session.get(Sku, "S-03").quote_id == "Q-03A"


def test_a24_workbench_opens_third_page_proposal_and_order(engine, monkeypatch):
    for i in range(101):
        create(engine, f"P-{i:03}")
    app = open_app(engine, monkeypatch)
    app.number_input(key="wb_proposal_page").set_value(3).run()
    app.button(key="wb_proposal_P-100").click().run()
    assert not app.exception
    assert next(s.value for s in app.selectbox if s.label == "选择提案") == "P-100"
    assert app.number_input(key="p5_list_page_合成演示").value == 3
    navigate(app, "经营提醒")
    app.radio(key="wb_group").set_value("settlement").run()
    app.button(key="wb_settlement_OL-05").click().run()
    assert any(h.value == "定位订单：OL-05" for h in app.subheader)
    assert any("-0.50" in j.value and "OL-05" in j.value for j in app.json)


def test_a25_bad_report_does_not_hide_proposals_or_show_stale_counts(engine, monkeypatch):
    create(engine, "P-valid")
    app = open_app(engine, monkeypatch)
    assert any(m.label == "结算待核对订单数" and m.value == "3" for m in app.metric)
    app.text_input(key="wb_as_of").set_value("2020-01-01T00:00:00+08:00").run()
    assert not app.exception
    assert any("不可计算" in e.value for e in app.error)
    assert not any("订单数" in m.label for m in app.metric)
    assert app.button(key="wb_proposal_P-valid")


def test_a23_space_switch_clears_preview_decision_and_content_context(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    app.session_state["p1_preview"] = "old preview"
    app.session_state["p4_preview"] = "old preview"
    app.session_state["p5_decision_context"] = "old decision"
    app.session_state["m1_report"] = "old report"
    app.session_state["nav_sku"] = "S-03"
    app.sidebar.selectbox(key="data_space").set_value("真实数据").run()
    assert not app.exception
    for key in ("p1_preview", "p4_preview", "p5_decision_context", "m1_report", "nav_sku"):
        assert key not in app.session_state


def test_a24_invalid_target_does_not_fall_back(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    app.session_state["nav_target"] = (
        "经营提醒",
        "sku",
        "missing",
        None,
        app.session_state.nav_space,
    )
    app.run()
    assert any("类型与页面不匹配" in e.value for e in app.error)
    navigate(app, "离线草稿")
    app.session_state["nav_sku"] = "missing"
    app.run()
    assert any("未自动替换" in e.value for e in app.error)
    assert not any(s.label == "选择 SKU" for s in app.selectbox)


def test_a24_changed_proposal_status_is_reloaded(engine, monkeypatch):
    from xhs_seller.services.proposals import decide_proposal

    create(engine, "P-race")
    app = open_app(engine, monkeypatch)
    decide_proposal(
        engine,
        proposal_id="P-race",
        expected_revision=1,
        decision="approved",
        note="另一会话人工核对",
        operator="test",
    )
    app.button(key="wb_proposal_P-race").click().run()
    assert not app.exception
    assert not any(b.label == "记录本地决定" for b in app.button)
    assert app.get("download_button")
    assert any('"approved"' in j.value for j in app.json)


def test_a25_bad_proposal_audit_still_blocks_after_handoff(engine, monkeypatch):
    from sqlmodel import select

    from xhs_seller.models import ProposalAudit

    create(engine, "P-bad")
    with Session(engine) as session:
        audit = session.exec(select(ProposalAudit)).one()
        audit.new_json = "invalid"
        session.commit()
    app = open_app(engine, monkeypatch)
    app.button(key="wb_proposal_P-bad").click().run()
    assert not app.exception
    assert any("审计" in e.value for e in app.error)
    assert not app.get("download_button")
    assert not any(b.label == "记录本地决定" for b in app.button)


def test_a24_parent_mismatch_is_rejected(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    navigate(app, "离线草稿")
    app.session_state["nav_target"] = (
        "离线草稿",
        "sku",
        "S-01",
        "PC-03",
        app.session_state.nav_space,
    )
    app.run()
    assert any("归属不匹配" in e.value for e in app.error)
    assert not any(s.label == "选择 SKU" for s in app.selectbox)
    app.run()
    assert any("归属不匹配" in e.value for e in app.error)


def test_a25_unexpected_report_failure_is_isolated(engine, monkeypatch):
    from xhs_seller.services import workbench_queries

    create(engine, "P-available")

    def fail(*args):
        raise RuntimeError("raw sensitive error must not appear")

    monkeypatch.setattr(workbench_queries, "build_report", fail)
    app = open_app(engine, monkeypatch)
    assert not app.exception
    assert app.button(key="wb_proposal_P-available")
    assert any("订单提醒读取失败" in e.value for e in app.error)
    assert not any("raw sensitive" in e.value for e in app.error)
    assert not any("订单数" in m.label for m in app.metric)


def test_a26_all_legacy_pages_are_reachable(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    for page in (
        "离线协作",
        "候选与报价",
        "SKU 经济性",
        "研究与评分",
        "离线草稿",
        "订单成本与对账",
        "异常与复盘",
        "本地提案与复盘",
        "编辑与导入",
        "订单费用导入",
        "数据备份",
        "经营提醒",
    ):
        navigate(app, page)
        assert not app.exception, page
