from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlmodel import Session
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import Expense, Sku
from xhs_seller.seed_demo import seed
from xhs_seller.services.imports import InputFile, commit, preflight


@pytest.mark.parametrize(
    ("navigation", "page", "module_name"),
    [
        ("订单成本与对账", "报表与核对", "ui_p4"),
        ("本地提案与复盘", "异常与复盘", "ui_p5"),
        ("研究与评分", None, "ui_p2"),
    ],
)
def test_default_report_time_includes_recent_subsecond_revision(
    monkeypatch, tmp_path, caplog, navigation, page, module_name
):
    from streamlit.elements.lib import policies

    from xhs_seller import db, ui_time

    monkeypatch.setattr(policies, "_shown_default_value_warning", False)
    caplog.set_level("WARNING", logger="streamlit.elements.lib.policies")
    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    expense = (
        "expense_id,revision,updated_at,expense_date,category,amount,basis,allocation_mode,order_line_id,source_ref,remark\n"
        "E-RECENT,1,2026-10-27T12:00:00.200000+08:00,2026-10-27,marketing,1.00,actual,unallocated,,fixture://expense/recent,\n"
    )
    preview = preflight(
        engine,
        [InputFile("expense", "expense.csv", expense.encode())],
        real_data=False,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    with Session(engine) as session:
        sku = session.get(Sku, "S-01")
        sku.updated_at = "2026-10-27T04:00:00.200000+00:00"
        session.commit()

    clock = [datetime(2026, 10, 27, 12, 0, 0, 500000, tzinfo=ZoneInfo("Asia/Shanghai"))]

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            moment = clock[0]
            return moment.astimezone(tz) if tz else moment.replace(tzinfo=None)

    monkeypatch.setattr(db, "make_engine", lambda: engine)
    monkeypatch.setattr(ui_time, "datetime", FixedDateTime)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value(navigation).run()
    if page and app.radio[0].value != page:
        app.radio[0].set_value(page).run()
    assert not app.exception
    assert not app.error
    key = {
        "ui_p2": "p2_asof_合成演示",
        "ui_p4": "p4_report_as_of",
        "ui_p5": "p5_review_as_of",
    }[module_name]
    assert "12:00:00.500000+08:00" in app.text_input(key=key).value
    chosen = "2026-10-27T12:00:00.300000+08:00"
    app.text_input(key=key).set_value(chosen).run()
    clock[0] += timedelta(seconds=10)
    with Session(engine) as session:
        record = (
            session.get(Sku, "S-01") if module_name == "ui_p2" else session.get(Expense, "E-RECENT")
        )
        record.revision += 1
        record.updated_at = "2026-10-27T04:00:05+00:00"
        session.commit()
    app.run()
    assert app.text_input(key=key).value == chosen
    assert app.error
    app.button(key=f"{key}_now").click().run()
    assert app.text_input(key=key).value == "2026-10-27T12:00:10.500000+08:00"
    assert not app.exception and not app.error
    assert not any(
        "was created with a default value" in record.message for record in caplog.records
    )
