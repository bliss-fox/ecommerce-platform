
import threading
import time
from pathlib import Path

from navigation_helpers import navigate
from streamlit.testing.v1 import AppTest

from xhs_seller.agents.runtime import RunSession
from xhs_seller.agents.snapshot import read_snapshot
from xhs_seller.db import make_engine
from xhs_seller.seed_demo import seed
from xhs_seller.services.drafts import now


def ready(app):
    deadline = time.monotonic() + 10
    while app.session_state.m1_session.run.view()["status"] == "running":
        assert time.monotonic() < deadline
        threading.Event().wait(0.01)
    app.button(key="m1_refresh").click().run()
    assert not app.exception
    return app


def test_a32_ui_run_export_navigation_and_space_isolation(monkeypatch, tmp_path):
    from xhs_seller import db

    demo = make_engine(tmp_path / "demo.db")
    real = make_engine(tmp_path / "real.db")
    monkeypatch.setattr(db, "make_engine", lambda *args: real if args else demo)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    navigate(app, "离线协作")
    assert not app.exception
    app.button(key="m1_start").click().run()
    assert any("1–3" in e.value for e in app.error)
    app.multiselect(key="m1_targets").set_value(["S-01", "S-03"])
    app.button(key="m1_start").click().run()
    ready(app)
    manager = app.session_state.m1_session
    run = manager.run
    assert run.view()["status"] == "completed"
    app.button(key="m1_export").click().run()
    assert app.get("download_button")
    app.button(key="m1_refresh").click().run()
    assert manager.run.run_id == run.run_id
    app.button(key="m1_content_S-03").click().run()
    assert not app.exception
    assert app.sidebar.radio[0].value == "内容工作区"
    assert app.selectbox(key="p3_sku_合成演示").value == "S-03"
    navigate(app, "离线协作")
    app.button(key="m1_start").click().run()
    ready(app)
    assert manager.run.run_id != run.run_id
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert not app.exception
    assert manager.run is None
    assert not app.get("download_button")
    assert any("尚未录入" in e.value for e in app.info)


def test_a31_ui_stale_result_blocks_download_and_navigation(monkeypatch, tmp_path):
    from sqlmodel import Session

    from xhs_seller import db
    from xhs_seller.models import Sku

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    navigate(app, "离线协作")
    app.multiselect(key="m1_targets").set_value(["S-01"])
    app.button(key="m1_start").click().run()
    ready(app)
    app.button(key="m1_export").click().run()
    assert app.get("download_button")
    with Session(engine) as session:
        session.get(Sku, "S-01").marketing_per_order_cents += 1
        session.commit()
    app.button(key="m1_refresh").click().run()
    assert not app.exception
    assert any("来源已更新" in e.value for e in app.error)
    assert not app.get("download_button")
    assert not any(b.label.startswith("核对 S-") for b in app.button)


def test_a29_session_dedup_and_switch_cancel(tmp_path):
    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    snap = read_snapshot(engine, "合成演示", ["S-01"], now())
    manager = RunSession()
    barrier, release = threading.Barrier(4), threading.Event()
    from xhs_seller.agents.tools import execute_role

    def worker(role, tools, inputs):
        barrier.wait(timeout=5)
        assert release.wait(5)
        return execute_role(role, tools, inputs)

    first = manager.start(snap, worker=worker)
    barrier.wait(timeout=5)
    assert manager.start(snap, rerun=True) is first
    manager.switch("another-space")
    assert first.view()["status"] == "cancelled" and manager.run is None
    release.set()


def test_a30_ui_cancel_enables_rerun_without_accepting_late_results(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.agents.tools import execute_role

    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    barrier, release = threading.Barrier(4), threading.Event()

    def worker(role, tools, inputs):
        barrier.wait(timeout=5)
        assert release.wait(5)
        return execute_role(role, tools, inputs)

    manager = app.session_state.m1_session
    run = manager.start(read_snapshot(engine, "合成演示", ["S-01"], now()), worker=worker)
    barrier.wait(timeout=5)
    try:
        navigate(app, "离线协作")
        assert app.button(key="m1_start").disabled
        app.button(key="m1_cancel").click().run()
        assert not app.exception
        assert run.view()["status"] == "cancelled"
        assert not app.button(key="m1_start").disabled
        assert not app.get("download_button")
    finally:
        release.set()
