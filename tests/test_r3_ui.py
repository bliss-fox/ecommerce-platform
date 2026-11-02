from pathlib import Path

import pytest
from navigation_helpers import navigate
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ImportBatch, OrderLine, RevisionAudit


def open_app(tmp_path, monkeypatch):
    from xhs_seller import db

    engine = make_engine(tmp_path / "r3.db")
    monkeypatch.setattr(db, "make_engine", lambda *args: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    return app, engine


def button(app, label):
    return next(b for b in app.button if b.label == label)


def test_a37_center_switch_clears_old_preview_and_receipt(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    button(app, "预检合成研究示例").click().run()
    assert button(app, "确认并原子提交")
    app.session_state["p1_receipt"] = "old-result"
    app.radio(key="import_group").set_value("订单、费用与结算").run()
    assert not app.exception
    assert "p1_preview" not in app.session_state
    assert "p1_receipt" not in app.session_state
    assert not any(b.label == "确认并原子提交" for b in app.button)
    button(app, "预检 P4 合成示例").click().run()
    assert button(app, "确认并原子提交 P4 数据")
    assert any("文件：" in m.value and "orders.csv" in m.value for m in app.markdown)
    app.radio(key="import_group").set_value("候选、报价与研究").run()
    assert "p4_preview" not in app.session_state
    assert not any(b.label.startswith("确认并原子提交") for b in app.button)
    with Session(engine) as session:
        assert not session.exec(select(ImportBatch)).all()
        assert not session.exec(select(OrderLine)).all()


def test_a38_a40_commit_receipt_history_and_explicit_recheck(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    app.radio(key="import_group").set_value("订单、费用与结算").run()
    app.session_state["p4_report_as_of"] = "2020-01-01T00:00:00+08:00"
    button(app, "预检 P4 合成示例").click().run()
    next(t for t in app.text_input if t.label == "P4 本地操作者标识").set_value("local")
    button(app, "确认并原子提交 P4 数据").click().run()
    assert not app.exception
    assert not any(b.label == "确认并原子提交 P4 数据" for b in app.button)
    with Session(engine) as session:
        batch = session.exec(select(ImportBatch)).one()
        assert len(session.exec(select(OrderLine)).all()) == 5
    assert app.session_state["p4_report_as_of"].startswith("2020")
    app.run()
    assert any(batch.batch_id in m.value for m in app.markdown)
    button(app, "查看本批修订").click().run()
    assert app.radio(key="p1_operation").value == "修订历史"
    assert app.selectbox(key="p1_history_batch_合成演示_成功批次").value == batch.batch_id
    navigate(app, "数据导入")
    button(app, "按当前时点重新核对订单").click().run()
    assert not app.exception and not app.error
    assert not app.text_input(key="p4_report_as_of").value.startswith("2020")
    assert any("350.00" in m.value for m in app.markdown)
    with Session(engine) as session:
        audit_count = len(session.exec(select(RevisionAudit)).all())
    navigate(app, "数据导入")
    button(app, "预检 P4 合成示例").click().run()
    assert not any(b.label == "查看本批修订" for b in app.button)
    next(t for t in app.text_input if t.label == "P4 本地操作者标识").set_value("local")
    button(app, "确认并原子提交 P4 数据").click().run()
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == 2
        assert len(session.exec(select(RevisionAudit)).all()) == audit_count


def test_a39_sources_and_handoff_keep_order(tmp_path, monkeypatch):
    from xhs_seller.services.imports import InputFile, commit, preflight
    from xhs_seller.ui_p4 import FILES

    app, engine = open_app(tmp_path, monkeypatch)
    directory = Path(__file__).resolve().parents[1] / "examples" / "p4"
    preview = preflight(
        engine,
        [
            InputFile(kind, name, (directory / name).read_bytes())
            for kind, (_, name) in FILES.items()
        ],
        real_data=False,
        orders_are_mapped=True,
        settlements_cover_full_principal=True,
        costs_exclude_refunds=True,
    )
    commit(engine, preview, operator="test")
    navigate(app, "订单成本与对账")
    app.selectbox(key="r3_order_source").set_value("OL-03").run()
    assert any("fixture://" in j.value and "-2400" in j.value for j in app.json)
    assert any("-18.00" in m.value for m in app.markdown)
    button(app, "导入有依据的修订").click().run()
    assert app.radio(key="import_group").value == "订单、费用与结算"
    button(app, "返回订单核对").click().run()
    assert any(h.value == "定位订单：OL-03" for h in app.subheader)
    assert app.selectbox(key="r3_order_source").value == "OL-03"
    assert not app.exception


@pytest.mark.parametrize(
    "group,sample,confirm,operator_label,module,table",
    [
        (
            "候选、报价与研究",
            "预检合成研究示例",
            "确认并原子提交",
            "本地操作者标识",
            "ui_p1",
            "research_note",
        ),
        (
            "订单、费用与结算",
            "预检 P4 合成示例",
            "确认并原子提交 P4 数据",
            "P4 本地操作者标识",
            "ui_p4",
            "order_line",
        ),
    ],
)
def test_a38_failed_write_rolls_back_without_success_receipt(
    tmp_path,
    monkeypatch,
    group,
    sample,
    confirm,
    operator_label,
    module,
    table,
):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    app.radio(key="import_group").set_value(group).run()
    button(app, sample).click().run()
    next(t for t in app.text_input if t.label == operator_label).set_value("local")
    hits = []

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(f"INSERT INTO {table.upper()}"):
            hits.append(True)
            raise OperationalError(statement, parameters, Exception("sensitive raw failure"))

    event.listen(engine, "before_cursor_execute", fail)
    try:
        button(app, confirm).click().run()
    finally:
        event.remove(engine, "before_cursor_execute", fail)
    assert hits and not app.exception
    assert any("写入失败" in e.value for e in app.error)
    assert not any("sensitive raw" in e.value for e in app.error)
    assert not any(b.label == "查看本批修订" for b in app.button)
    with Session(engine) as session:
        from sqlalchemy import text

        assert not session.exec(select(ImportBatch)).all()
        assert not session.exec(select(RevisionAudit)).all()
        assert session.exec(text(f"SELECT COUNT(*) FROM {table}")).one()[0] == 0


def test_a37_center_real_space_clears_confirmation_and_preview(tmp_path, monkeypatch):
    from xhs_seller import db

    app, demo = open_app(tmp_path, monkeypatch)
    real = make_engine(tmp_path / "real.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    navigate(app, "数据导入")
    app.radio(key="import_group").set_value("订单、费用与结算").run()
    button(app, "预检 P4 合成示例").click().run()
    next(t for t in app.text_input if t.label == "P4 本地操作者标识").set_value("old-operator")
    app.sidebar.selectbox(key="data_space").set_value("真实数据").run()
    assert not app.exception
    assert not any(
        "合成示例" in b.label or b.label.startswith("确认并原子提交") for b in app.button
    )
    assert "p4_preview" not in app.session_state
    assert not any(t.value == "old-operator" for t in app.text_input)


def test_a38_baseline_change_refuses_center_commit(tmp_path, monkeypatch):
    from xhs_seller.models import ProductCandidate

    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    button(app, "预检合成研究示例").click().run()
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        product.revision = 2
        session.commit()
    next(t for t in app.text_input if t.label == "本地操作者标识").set_value("local")
    button(app, "确认并原子提交").click().run()
    assert any("BASELINE_CHANGED" in e.value for e in app.error)
    assert not any(b.label == "查看本批修订" for b in app.button)
    with Session(engine) as session:
        assert not session.exec(select(ImportBatch)).all()


def test_a40_new_upload_removes_old_receipt(tmp_path, monkeypatch):
    import streamlit as st
    from test_preview_pagination import Upload

    directory = Path(__file__).resolve().parents[1] / "examples" / "import_cases"
    content = [(directory / "product_revision2.csv").read_bytes()]
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda label, **kw: Upload("same.csv", content[0]) if label == "候选 CSV" else None,
    )
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    button(app, "预检所选文件").click().run()
    next(t for t in app.text_input if t.label == "本地操作者标识").set_value("local")
    button(app, "确认并原子提交").click().run()
    assert button(app, "查看本批修订")
    assert not any(b.label == "确认并原子提交" for b in app.button)
    content[0] = (directory / "product_same_revision_conflict.csv").read_bytes()
    app.run()
    assert not any(b.label == "查看本批修订" for b in app.button)
    button(app, "预检所选文件").click().run()
    assert app.error and not app.exception
    assert not any(b.label == "确认并原子提交" for b in app.button)
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == 1


def test_a40_revision_recheck_refreshes_time_sources_and_selected_order(tmp_path, monkeypatch):
    import streamlit as st
    from test_preview_pagination import Upload

    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    app.radio(key="import_group").set_value("订单、费用与结算").run()
    button(app, "预检 P4 合成示例").click().run()
    next(t for t in app.text_input if t.label == "P4 本地操作者标识").set_value("local")
    button(app, "确认并原子提交 P4 数据").click().run()
    button(app, "按当前时点重新核对订单").click().run()
    app.text_input(key="p4_report_as_of").set_value("2026-10-27T09:00:00+08:00").run()
    app.selectbox(key="r3_order_source").set_value("OL-04").run()
    assert any("贡献利润：未知" in m.value for m in app.markdown)
    button(app, "导入有依据的修订").click().run()
    content = (
        Path(__file__).resolve().parents[1] / "examples/import_cases/expense_actual_revision2.csv"
    ).read_bytes()
    monkeypatch.setattr(
        st,
        "file_uploader",
        lambda label, **kw: Upload("expense.csv", content) if label == "费用 CSV" else None,
    )
    app.run()
    app.checkbox(key="p4_refunds_excluded_合成演示").check().run()
    button(app, "预检 P4 所选文件").click().run()
    next(t for t in app.text_input if t.label == "P4 本地操作者标识").set_value("local")
    button(app, "确认并原子提交 P4 数据").click().run()
    assert app.session_state["p4_report_as_of"] == "2026-10-27T09:00:00+08:00"
    from xhs_seller.services.p4_report import build_report

    with pytest.raises(ValueError):  # Current revisions cannot replay the older report.
        build_report(engine, app.session_state["p4_report_as_of"])
    button(app, "按当前时点重新核对订单").click().run()
    assert not app.exception and not app.error
    assert app.selectbox(key="r3_order_source").value == "OL-04"
    assert any("贡献利润：41.50" in m.value for m in app.markdown)
    assert any('"revision": 2' in j.value and '"amount_cents": 700' in j.value for j in app.json)
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == 2


def test_a40_missing_receipt_target_does_not_open_another_batch(tmp_path, monkeypatch):
    app, _ = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    button(app, "预检合成研究示例").click().run()
    next(t for t in app.text_input if t.label == "本地操作者标识").set_value("local")
    button(app, "确认并原子提交").click().run()
    button(app, "查看本批修订").click().run()
    app.session_state["p1_history_target"] = "missing-batch"
    app.run()
    assert any("定位批次不存在" in e.value for e in app.error)
    assert not any(s.label == "选择历史批次" for s in app.selectbox)


def test_a40_recheck_from_legacy_import_without_selected_order(tmp_path, monkeypatch):
    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "订单成本与对账")
    app.radio(key="p4_operation").set_value("CSV 导入").run()
    button(app, "预检 P4 合成示例").click().run()
    next(t for t in app.text_input if t.label == "P4 本地操作者标识").set_value("local")
    button(app, "确认并原子提交 P4 数据").click().run()
    button(app, "按当前时点重新核对订单").click().run()
    assert not app.exception and not app.error
    assert app.radio(key="p4_operation").value == "报表与核对"
    assert any("350.00" in m.value for m in app.markdown)
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == 1


@pytest.mark.parametrize("group", ["候选、报价与研究", "订单、费用与结算"])
@pytest.mark.parametrize("failure", ["invalid_counts", "read_failure"])
def test_a40_receipt_failure_is_safe_and_recovers_without_resubmitting(
    tmp_path, monkeypatch, group, failure
):
    from sqlalchemy import event
    from sqlalchemy.exc import OperationalError

    app, engine = open_app(tmp_path, monkeypatch)
    navigate(app, "数据导入")
    app.radio(key="import_group").set_value(group).run()
    is_p4 = group == "订单、费用与结算"
    button(app, "预检 P4 合成示例" if is_p4 else "预检合成研究示例").click().run()
    label = "P4 本地操作者标识" if is_p4 else "本地操作者标识"
    next(t for t in app.text_input if t.label == label).set_value("local")
    button(app, "确认并原子提交 P4 数据" if is_p4 else "确认并原子提交").click().run()
    with Session(engine) as session:
        batch = session.exec(select(ImportBatch)).one()
        original_counts = batch.counts_json
        batch_id = batch.batch_id
        audit_count = len(session.exec(select(RevisionAudit)).all())
        if failure == "invalid_counts":
            batch.counts_json = '{"private_raw_input": '
            session.commit()

    def fail(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT IMPORT_BATCH."):
            raise OperationalError(statement, parameters, Exception("private_raw_input"))

    if failure == "read_failure":
        event.listen(engine, "before_cursor_execute", fail)
    try:
        app.run()
    finally:
        if failure == "read_failure":
            event.remove(engine, "before_cursor_execute", fail)
    assert not app.exception
    assert any("回执" in e.value for e in app.error)
    assert not any("private_raw_input" in e.value for e in app.error)
    assert not app.success
    assert not any(b.label in {"查看本批修订", "按当前时点重新核对订单"} for b in app.button)
    assert app.session_state["p4_receipt" if is_p4 else "p1_receipt"] == batch_id
    with Session(engine) as session:
        stored = session.get(ImportBatch, batch_id)
        stored.counts_json = original_counts
        session.commit()
    app.run()
    assert not app.exception and not app.error
    assert button(app, "查看本批修订")
    assert any(batch_id in m.value for m in app.markdown)
    with Session(engine) as session:
        assert len(session.exec(select(ImportBatch)).all()) == 1
        assert len(session.exec(select(RevisionAudit)).all()) == audit_count
