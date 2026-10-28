from pathlib import Path

import pytest
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import OrderLine


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("=1+1", True),
        (" +1+1", True),
        ("-1+1", True),
        ("-1-1", True),
        ("@SUM(A1)", True),
        ("\ttext", True),
        ("fixture://expense/1", False),
        ("普通备注", False),
        ("", False),
    ],
)
def test_spreadsheet_formula_risk(value, expected):
    from xhs_seller.ui_csv import spreadsheet_formula_risk

    assert spreadsheet_formula_risk(value) is expected


def test_formula_warning_identifies_fields_without_modifying_export(monkeypatch):
    import streamlit as st

    from xhs_seller.ui_csv import warn_formula_fields
    from xhs_seller.ui_p4 import csv_bytes

    messages = []
    monkeypatch.setattr(st, "warning", messages.append)
    rows = [{"id": "-1-1", "remark": "=1+1"}]
    warn_formula_fields(rows, {"id": "记录 ID", "remark": "备注"}, "测试 CSV")
    assert len(messages) == 1 and "记录 ID" in messages[0] and "备注" in messages[0]
    assert b"-1-1,=1+1" in csv_bytes(rows, ["id", "remark"])
    messages.clear()
    warn_formula_fields(
        [{"id": "P-1", "remark": "普通备注"}], {"id": "记录 ID", "remark": "备注"}, "测试 CSV"
    )
    assert not messages


def test_report_distinguishes_no_assigned_expense_from_recorded_zero(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    assert not app.exception
    assert any("已归属费用（实际）：未记录" in item.value for item in app.markdown)
    assert any("已归属费用（估计）：未记录" in item.value for item in app.markdown)

    order = (
        "order_line_id,revision,updated_at,store_id,order_id,line_item_id,sku_id,quantity,paid_amount,refunded_amount,returned_units,status,paid_at,refund_updated_at\n"
        "OL-ZERO,1,2026-10-27T08:00:00+08:00,STORE,ORDER-ZERO,LINE-1,S-01,1,10.00,0.00,0,paid,2026-10-20T10:00:00+08:00,\n"
    )
    expense = (
        "expense_id,revision,updated_at,expense_date,category,amount,basis,allocation_mode,order_line_id,source_ref,remark\n"
        "E-ZERO,1,2026-10-27T08:00:00+08:00,2026-10-20,goods,0.00,actual,direct,OL-ZERO,fixture://expense/zero,\n"
    )
    preview = preflight(
        engine,
        [
            InputFile("order", "orders.csv", order.encode()),
            InputFile("expense", "expenses.csv", expense.encode()),
        ],
        real_data=False,
        orders_are_mapped=True,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    next(item for item in app.text_input if item.label.startswith("报告 as_of")).set_value(
        "2026-10-27T12:00:00+08:00"
    ).run()
    assert not app.exception
    assert any("已归属费用（实际）：0.00 元（1 项）" in item.value for item in app.markdown)
    assert any("已归属费用（估计）：未记录" in item.value for item in app.markdown)
    assert any("未知成本：1 行" in item.value for item in app.markdown)
    estimated = (
        expense.replace("E-ZERO", "E-EST")
        .replace(",goods,", ",marketing,")
        .replace(",actual,", ",estimated,")
        .replace("fixture://expense/zero", "fixture://expense/estimate")
    )
    preview = preflight(
        engine,
        [InputFile("expense", "estimated.csv", estimated.encode())],
        real_data=False,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    app.run()
    assert not app.exception
    assert any("已归属费用（估计）：0.00 元（1 项）" in item.value for item in app.markdown)
    assert any("未知成本：1 行" in item.value for item in app.markdown)
    engine.dispose()


def test_p4_sample_requires_confirmation_and_shows_report(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    assert not app.exception
    app.radio[0].set_value("CSV 导入").run()
    next(button for button in app.button if button.label == "预检 P4 合成示例").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert not session.exec(select(OrderLine)).all()
    next(item for item in app.text_input if item.label == "P4 本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:p4_confirm-确认并原子提交 P4 数据").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert len(session.exec(select(OrderLine)).all()) == 5
    app.radio[0].set_value("报表与核对").run()
    next(item for item in app.text_input if item.label.startswith("报告 as_of")).set_value(
        "2026-10-27T12:00:00+08:00"
    ).run()
    assert not app.exception
    assert any("350.00" in item.value for item in app.markdown)
    assert any("265.50" in item.value and "20.00" in item.value for item in app.markdown)
    assert any("未知成本：1 行" in item.value for item in app.markdown)
    assert any(item.value == "SKU 汇总" for item in app.subheader)
    sku_summary = app.dataframe[1].value.set_index("sku_id")
    assert sku_summary.loc["S-01", "contribution_cny"] == "81.00"
    assert sku_summary.loc["S-03", "contribution_cny"] == "未知"
    assert any(
        "实际成本" in item.value
        and "260.00" in item.value
        and "167.00" in item.value
        and "93.00" in item.value
        and "4 行" in item.value
        for item in app.markdown
    )
    import streamlit as st

    downloads = {}

    def capture(label, data, *args, **kwargs):
        downloads[label] = data

    monkeypatch.setattr(st, "download_button", capture)
    next(item for item in app.text_input if item.label.startswith("报告 as_of")).set_value(
        "2026-10-27T04:00:00Z"
    ).run()
    assert any("2026-10-27T12:00:00+08:00" in item.value for item in app.caption)
    assert "2026-10-27T12:00:00+08:00" in downloads["下载费用核对表"].decode("utf-8-sig")
    assert "S-01" in downloads["下载 SKU 汇总"].decode("utf-8-sig")
    from xhs_seller.services.imports import InputFile, commit, preflight

    sample = (
        Path(__file__).resolve().parents[1] / "examples/import_cases/expense_actual_revision2.csv"
    ).read_bytes()
    estimated = sample.replace(b",actual,direct,", b",estimated,direct,")
    preview = preflight(
        engine,
        [InputFile("expense", "expense.csv", estimated)],
        real_data=False,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    app.run()
    assert not app.exception
    assert any(
        "含估计" in item.value
        and "90.00" in item.value
        and "48.50" in item.value
        and "41.50" in item.value
        and "1 行" in item.value
        for item in app.markdown
    )
    assert any("未知成本：0 行" in item.value for item in app.markdown)
    sku_summary = app.dataframe[1].value.set_index("sku_id")
    assert sku_summary.loc["S-03", "cost_status"] == "estimated"
    assert sku_summary.loc["S-03", "contribution_cny"] == "41.50"
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("修订历史").run()
    assert not app.exception


def test_offsetting_settlement_differences_remain_visible(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight
    from xhs_seller.services.proposals import review

    engine = make_engine(tmp_path / "settlement-summary.db")
    seed(engine)
    directory = Path(__file__).resolve().parents[1] / "examples" / "p4"
    files = [
        InputFile(kind, name, (directory / name).read_bytes())
        for kind, name in (
            ("order", "orders.csv"),
            ("expense", "expenses.csv"),
            ("allocation", "expense_allocations.csv"),
            ("adjustment", "cost_adjustments.csv"),
            ("settlement", "settlements.csv"),
        )
    ]
    preview = preflight(
        engine,
        files,
        real_data=False,
        orders_are_mapped=True,
        settlements_cover_full_principal=True,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    changed = (
        b"settlement_id,revision,updated_at,order_line_id,cutoff_at,covered_refunded_amount,platform_fee_deducted,other_withheld,settlement_adjustments,actual_received,source_ref\n"
        b"SET-01,2,2026-10-27T10:00:00+08:00,OL-01,2026-10-26T18:00:00+08:00,0.00,6.00,0.00,0.00,114.50,fixture://settlement/01\n"
    )
    preview = preflight(
        engine,
        [InputFile("settlement", "settlement.csv", changed)],
        real_data=False,
        settlements_cover_full_principal=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    report, issues, _ = review(engine, "2026-10-27T12:00:00+08:00")
    assert {item["id"] for item in issues if item["type"] == "settlement_difference"} == {
        "OL-01",
        "OL-05",
    }
    assert report["settlement_summary"]["difference_count"] == 2
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    next(item for item in app.text_input if item.label.startswith("报告 as_of")).set_value(
        "2026-10-27T12:00:00+08:00"
    ).run()
    assert not app.exception
    assert any(
        "结算差异净额：0.00" in item.value
        and "待核对：2 行" in item.value
        and "正差额：0.50" in item.value
        and "负差额：-0.50" in item.value
        for item in app.markdown
    )


def test_allocation_proposal_is_visible_and_does_not_write(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    app.radio[0].set_value("CSV 导入").run()
    next(button for button in app.button if button.label == "预检 P4 合成示例").click().run()
    next(item for item in app.text_input if item.label == "P4 本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:p4_confirm-确认并原子提交 P4 数据").click().run()
    data = (
        "expense_id,revision,updated_at,expense_date,category,amount,basis,allocation_mode,order_line_id,source_ref,remark\n"
        "E-POOL-NEW,1,2026-10-27T08:00:00+08:00,2026-10-20,marketing,0.10,actual,unallocated,,fixture://expense/new,=1+1\n"
    )
    preview = preflight(
        engine,
        [InputFile("expense", "expenses.csv", data.encode())],
        real_data=False,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    app.radio[0].set_value("分摊建议").run()
    app.multiselect[0].set_value(["OL-02", "OL-04", "OL-05"]).run()
    assert not app.exception
    assert any("已有同类成本来源" in message.value for message in app.error)
    assert not app.dataframe
    assert not any(button.label.startswith("下载建议分摊") for button in app.get("download_button"))
    orders = (
        "order_line_id,revision,updated_at,store_id,order_id,line_item_id,sku_id,quantity,paid_amount,refunded_amount,returned_units,status,paid_at,refund_updated_at\n"
        + "".join(
            f"OL-NEW-{i},1,2026-10-27T08:00:00+08:00,DEMO-STORE,ORDER-NEW-{i},LINE-1,S-01,1,10.00,0.00,0,paid,2026-10-20T10:00:00+08:00,\n"
            for i in range(1, 4)
        )
    )
    order_preview = preflight(
        engine,
        [InputFile("order", "orders.csv", orders.encode())],
        real_data=False,
        orders_are_mapped=True,
    )
    assert not order_preview.errors
    commit(engine, order_preview, operator="local")
    app.run()
    app.multiselect[0].set_value(["OL-NEW-1", "OL-NEW-2", "OL-NEW-3"]).run()
    assert not app.exception and not app.error
    assert any(button.label == "下载配套分摊文件包" for button in app.get("download_button"))
    import streamlit as st

    downloads = {}

    def capture_download(label, data, *args, **kwargs):
        downloads[label] = data

    monkeypatch.setattr(st, "download_button", capture_download)
    app.run()
    assert not app.exception and not app.error
    assert any("公式" in warning.value and "备注" in warning.value for warning in app.warning)
    table = app.dataframe[0].value
    assert list(table["amount"]) == ["0.04", "0.03", "0.03"]
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(downloads["下载配套分摊文件包"])) as bundle:
        assert set(bundle.namelist()) == {"expenses.csv", "expense_allocations.csv"}
        assert bundle.read("expenses.csv") == downloads["下载费用池修订 CSV"]
        assert bundle.read("expense_allocations.csv") == downloads["下载建议分摊 CSV"]
        bundled_expense = bundle.read("expenses.csv")
        bundled_allocations = bundle.read("expense_allocations.csv")
    generated = preflight(
        engine,
        [
            InputFile("expense", "expenses.csv", bundled_expense),
            InputFile("allocation", "expense_allocations.csv", bundled_allocations),
        ],
        real_data=False,
        costs_exclude_refunds=True,
    )
    assert not generated.errors
    change = next(item for item in generated.changes if item.kind == "expense")
    assert change.new["remark"] == "=1+1"
    with Session(engine) as session:
        from xhs_seller.models import Expense

        assert session.get(Expense, "E-POOL-NEW").allocation_mode == "unallocated"


def test_allocation_proposal_rejects_future_expense_timestamp(monkeypatch, tmp_path):
    from datetime import UTC, datetime, timedelta

    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    orders = (
        "order_line_id,revision,updated_at,store_id,order_id,line_item_id,sku_id,quantity,paid_amount,refunded_amount,returned_units,status,paid_at,refund_updated_at\n"
        "OL-NEW,1,2026-10-27T08:00:00+08:00,DEMO-STORE,ORDER-NEW,LINE-1,S-01,1,10.00,0.00,0,paid,2026-10-20T10:00:00+08:00,\n"
    )
    expense = (
        "expense_id,revision,updated_at,expense_date,category,amount,basis,allocation_mode,order_line_id,source_ref,remark\n"
        f"E-FUTURE,1,{future},2026-10-20,marketing,0.10,actual,unallocated,,fixture://expense/future,\n"
    )
    preview = preflight(
        engine,
        [
            InputFile("order", "orders.csv", orders.encode()),
            InputFile("expense", "expenses.csv", expense.encode()),
        ],
        real_data=False,
        orders_are_mapped=True,
        costs_exclude_refunds=True,
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    app.radio[0].set_value("分摊建议").run()
    app.multiselect[0].set_value(["OL-NEW"]).run()
    assert not app.exception
    assert any("时间" in item.value and "费用" in item.value for item in app.error)
    assert not app.get("download_button")


@pytest.mark.parametrize(
    ("upload_label", "confirmation_key"),
    [
        ("订单行 CSV", "p4_mapping_合成演示"),
        ("费用 CSV", "p4_refunds_excluded_合成演示"),
        ("成本调整 CSV", "p4_refunds_excluded_合成演示"),
        ("结算快照 CSV", "p4_full_principal_合成演示"),
    ],
)
def test_p4_confirmation_resets_when_its_file_changes(
    monkeypatch, tmp_path, upload_label, confirmation_key
):
    import io

    import streamlit as st

    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)

    class Upload(io.BytesIO):
        name = "same-name.csv"

    def simulated_upload(label, *, type, key):
        value = st.text_input(label, key=key)
        return Upload(value.encode()) if label == upload_label and value else None

    monkeypatch.setattr(st, "file_uploader", simulated_upload)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    app.radio[0].set_value("CSV 导入").run()
    next(item for item in app.text_input if item.label == upload_label).set_value("file A").run()
    app.checkbox(key=confirmation_key).check().run()
    assert app.checkbox(key=confirmation_key).value
    next(item for item in app.text_input if item.label == upload_label).set_value("file B").run()
    assert not app.exception
    assert not app.checkbox(key=confirmation_key).value


def test_p4_replaced_order_requires_new_confirmation_before_commit(monkeypatch, tmp_path):
    import io

    import streamlit as st

    from xhs_seller import db
    from xhs_seller.models import Expense

    engine = make_engine(tmp_path / "attestation.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    orders = (
        b"order_line_id,revision,updated_at,store_id,order_id,line_item_id,sku_id,quantity,paid_amount,refunded_amount,returned_units,status,paid_at,refund_updated_at\n"
        b"OL-NEW-A,1,2026-10-27T08:00:00+08:00,DEMO-STORE,ORDER-A,LINE-1,S-01,1,10.00,0.00,0,paid,2026-10-20T10:00:00+08:00,\n"
    )
    expense = (
        b"expense_id,revision,updated_at,expense_date,category,amount,basis,allocation_mode,order_line_id,source_ref,remark\n"
        b"E-NEW,1,2026-10-27T08:00:00+08:00,2026-10-20,marketing,1.00,actual,unallocated,,fixture://expense/new,\n"
    )

    class Upload(io.BytesIO):
        name = "data.csv"

    def simulated_upload(label, *, type, key):
        value = st.text_input(label, key=key)
        if label == "订单行 CSV" and value:
            return Upload(orders if value == "A" else orders.replace(b"OL-NEW-A", b"OL-NEW-B"))
        if label == "费用 CSV" and value:
            return Upload(expense)
        return None

    monkeypatch.setattr(st, "file_uploader", simulated_upload)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    app.radio[0].set_value("CSV 导入").run()
    next(item for item in app.text_input if item.label == "订单行 CSV").set_value("A").run()
    next(item for item in app.text_input if item.label == "费用 CSV").set_value("selected").run()
    app.checkbox(key="p4_mapping_合成演示").check().run()
    app.checkbox(key="p4_refunds_excluded_合成演示").check().run()
    next(item for item in app.button if item.label == "预检 P4 所选文件").click().run()
    assert any(item.label == "确认并原子提交 P4 数据" for item in app.button)

    next(item for item in app.text_input if item.label == "订单行 CSV").set_value("B").run()
    assert not app.checkbox(key="p4_mapping_合成演示").value
    assert app.checkbox(key="p4_refunds_excluded_合成演示").value
    assert any("相关文件已变更" in item.value for item in app.info)
    assert not any(item.label == "确认并原子提交 P4 数据" for item in app.button)
    next(item for item in app.button if item.label == "预检 P4 所选文件").click().run()
    assert any("存在错误" in item.value for item in app.error)
    assert not any(item.label == "确认并原子提交 P4 数据" for item in app.button)
    app.checkbox(key="p4_mapping_合成演示").check().run()
    next(item for item in app.button if item.label == "预检 P4 所选文件").click().run()
    assert not app.error and not app.exception
    next(item for item in app.text_input if item.label == "P4 本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:p4_confirm-确认并原子提交 P4 数据").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert session.get(OrderLine, "OL-NEW-A") is None
        assert session.get(OrderLine, "OL-NEW-B") is not None
        assert session.get(Expense, "E-NEW") is not None
