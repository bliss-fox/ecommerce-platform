"""A46: expose navigation and focus reminders without changing business rules."""

from pathlib import Path

import pytest
from navigation_helpers import navigate
from streamlit.testing.v1 import AppTest
from test_m1 import dump_database
from test_m1_ui import ready
from test_r1 import create
from test_r1 import engine as engine_fixture

from xhs_seller.ui_navigation import PAGE_LABELS, WORKSPACES

engine = engine_fixture
ROOT = Path(__file__).resolve().parents[1]


def open_app(engine, monkeypatch):
    from xhs_seller import db

    monkeypatch.setattr(db, "make_engine", lambda *args: engine)
    return AppTest.from_file(ROOT / "app.py", default_timeout=30).run()


def test_a46_visible_subpages_and_single_page_without_selector(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    assert not app.exception
    assert app.sidebar.title[0].value == "小红书卖家助手"
    assert not app.main.title
    for workspace, pages in WORKSPACES.items():
        app.sidebar.radio(key="nav_workspace").set_value(workspace).run()
        assert not app.exception
        key = f"nav_page_{workspace}"
        if len(pages) > 1:
            assert app.sidebar.radio(key=key).options == [PAGE_LABELS[page] for page in pages]
        else:
            assert app.session_state[key] == pages[0]
            assert not any(widget.key == key for widget in app.sidebar.radio)
        assert not any(widget.key == key for widget in app.sidebar.selectbox)


def test_a46_reminder_category_shows_only_its_own_objects(engine, monkeypatch):
    create(engine, "P-UI-01")
    app = open_app(engine, monkeypatch)
    before = dump_database(engine)
    assert app.radio(key="wb_group").value == "proposals"
    assert app.button(key="wb_proposal_P-UI-01")
    assert not any(button.key.startswith("wb_settlement_") for button in app.button)
    assert {metric.label: metric.value for metric in app.metric} == {
        "待审提案数": "1", "成本待补订单数": "1", "含估计成本订单数": "0",
        "结算待核对订单数": "3", "未分摊费用数": "0",
    }
    app.radio(key="wb_group").set_value("settlement").run()
    assert app.button(key="wb_settlement_OL-05")
    assert not any(button.key.startswith("wb_proposal_") for button in app.button)
    app.radio(key="wb_group").set_value("estimated").run()
    assert not app.exception
    assert not any(button.key.startswith("wb_settlement_") for button in app.button)
    assert any("当前没有含估计成本订单" in info.value for info in app.info)
    assert dump_database(engine) == before


def test_a46_first_category_and_manual_selection_survive_count_changes(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    assert app.radio(key="wb_group").value == "unknown"
    app.radio(key="wb_group").set_value("estimated").run()
    create(engine, "P-new")
    app.run()
    assert app.radio(key="wb_group").value == "estimated"
    assert not any(button.key == "wb_proposal_P-new" for button in app.button)
    navigate(app, "SKU 经济性")
    navigate(app, "经营提醒")
    assert app.radio(key="wb_group").value == "estimated"


def test_a46_category_keeps_its_page_when_hidden(engine, monkeypatch):
    for index in range(101):
        create(engine, f"P-UI-{index:03}")
    app = open_app(engine, monkeypatch)
    app.number_input(key="wb_proposal_page").set_value(3).run()
    assert app.button(key="wb_proposal_P-UI-100")
    app.radio(key="wb_group").set_value("unknown").run()
    app.radio(key="wb_group").set_value("proposals").run()
    assert app.number_input(key="wb_proposal_page").value == 3
    app.button(key="wb_proposal_P-UI-100").click().run()
    assert not app.exception
    assert next(box.value for box in app.selectbox if box.label == "选择提案") == "P-UI-100"
    navigate(app, "经营提醒")
    assert app.radio(key="wb_group").value == "proposals"
    assert app.number_input(key="wb_proposal_page").value == 3
    from xhs_seller.services.proposals import decide_proposal

    decide_proposal(engine, proposal_id="P-UI-000", expected_revision=1, decision="approved",
        note="另一会话合成人工核对", operator="test")
    app.run()
    assert not app.exception
    assert app.number_input(key="wb_proposal_page").value == 2


def test_a46_groups_share_one_report_and_preserve_error_selection(engine, monkeypatch):
    from xhs_seller.services import workbench_queries

    create(engine, "P-available")
    original = workbench_queries.build_report
    calls = []

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(workbench_queries, "build_report", counted)
    app = open_app(engine, monkeypatch)
    assert len(calls) == 1
    app.radio(key="wb_group").set_value("settlement").run()
    assert len(calls) == 2
    app.text_input(key="wb_as_of").set_value("2020-01-01T00:00:00+08:00").run()
    assert not app.exception and any("不可计算" in error.value for error in app.error)
    assert app.radio(key="wb_group").value == "settlement"
    assert not any("订单数" in metric.label for metric in app.metric)
    assert not any(button.key.startswith("wb_settlement_") for button in app.button)
    app.radio(key="wb_group").set_value("proposals").run()
    assert app.button(key="wb_proposal_P-available")


def test_a46_space_switch_resets_category_and_pagination(engine, monkeypatch):
    create(engine, "P-demo")
    app = open_app(engine, monkeypatch)
    app.radio(key="wb_group").set_value("estimated").run()
    app.session_state["wb_settlement_page"] = 99
    app.sidebar.selectbox(key="data_space").set_value("真实数据").run()
    assert not app.exception
    assert app.radio(key="wb_group").value == "proposals"
    assert app.session_state["wb_settlement_page"] == 1


def test_a46_proposal_failure_keeps_order_groups_available(engine, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from xhs_seller import ui_workbench

    create(engine, "P-unavailable")
    app = open_app(engine, monkeypatch)

    def fail(*args):
        raise OperationalError("private_sql", {}, Exception("private_database_error"))

    monkeypatch.setattr(ui_workbench, "pending_proposals", fail)
    app.run()
    assert not app.exception
    assert any("待审提案读取失败" in error.value for error in app.error)
    assert not any(metric.label == "待审提案数" for metric in app.metric)
    assert not any(button.key == "wb_proposal_P-unavailable" for button in app.button)
    assert not any("private_" in error.value for error in app.error)
    app.radio(key="wb_group").set_value("unknown").run()
    assert app.button(key="wb_unknown_OL-04")


def test_a46_collaboration_summary_precedes_folded_process(engine, monkeypatch):
    app = open_app(engine, monkeypatch)
    navigate(app, "离线协作")
    assert not any(box.label == "任务模板" for box in app.selectbox)
    app.multiselect(key="m1_targets").set_value(["S-01", "S-03"])
    app.button(key="m1_start").click().run()
    ready(app)
    assert app.session_state.m1_session.run.view()["status"] == "completed"
    details = next(expander for expander in app.expander if expander.label == "任务过程、角色结果与来源")
    assert not details.proto.expanded
    assert not any(header.value == "主 Agent 汇总" for header in details.subheader)
    assert any(header.value == "主 Agent 汇总" for header in app.subheader)
    assert app.button(key="m1_export")
    assert any("S-03" in text.value and "预计贡献 未知" in text.value for text in app.markdown)


@pytest.mark.parametrize("failed_role", ["cost", "review", "summary"])
def test_a46_collaboration_failures_stay_visible_outside_details(engine, monkeypatch, failed_role):
    from xhs_seller.agents.snapshot import read_snapshot
    from xhs_seller.agents.tools import execute_role
    from xhs_seller.services.drafts import now

    app = open_app(engine, monkeypatch)
    manager = app.session_state.m1_session

    def worker(role, tools, inputs):
        if role == failed_role:
            raise RuntimeError("private_worker_error")
        return execute_role(role, tools, inputs)

    manager.start(read_snapshot(engine, "合成演示", ["S-01"], now()), worker=worker)
    navigate(app, "离线协作")
    ready(app)
    assert manager.run.view()["status"] == "partial"
    assert any("失败" in error.value for error in app.error)
    assert not any("private_worker_error" in error.value for error in app.error)
    details = next(expander for expander in app.expander if expander.label == "任务过程、角色结果与来源")
    assert not details.error
    assert any("不是完整评估" in warning.value for warning in app.warning)
