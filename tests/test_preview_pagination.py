import csv
import io
from pathlib import Path

import streamlit as st
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import OrderLine, ProductCandidate
from xhs_seller.services.imports import SPECS


class Upload:
    def __init__(self, name, data):
        self.name = name
        self.data = data

    def getvalue(self):
        return self.data


def make_csv(kind, rows):
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SPECS[kind][2])
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def test_p1_preview_pages_still_commit_the_complete_batch(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    rows = [
        {
            "product_id": f"PC-BULK-{i:03}",
            "revision": "1",
            "updated_at": "2026-10-27T08:00:00+08:00",
            "product_name": f"合成候选 {i}",
            "category": "测试",
            "status": "researching",
            "risk_notes": "",
        }
        for i in range(1, 102)
    ]
    upload = Upload("products.csv", make_csv("product", rows))
    monkeypatch.setattr(
        st, "file_uploader", lambda label, **kwargs: upload if label == "候选 CSV" else None
    )
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("CSV 导入").run()
    next(button for button in app.button if button.label == "预检所选文件").click().run()
    assert not app.exception and not app.error
    assert len(app.get("expander")) == 50
    assert any("新增 101" in item.value for item in app.markdown)
    next(item for item in app.number_input if item.label == "预检变更页").set_value(3).run()
    assert len(app.get("expander")) == 1
    assert "PC-BULK-101" in app.get("expander")[0].label
    next(item for item in app.text_input if item.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception and not app.error
    with Session(engine) as session:
        assert (
            len(
                session.exec(
                    select(ProductCandidate).where(ProductCandidate.product_id.like("PC-BULK-%"))
                ).all()
            )
            == 101
        )
    engine.dispose()


def test_p4_preview_pages_changes_and_warnings_independently(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    rows = [
        {
            "order_line_id": f"OL-BULK-{i:03}",
            "revision": "1",
            "updated_at": "2026-10-27T08:00:00+08:00",
            "store_id": "STORE",
            "order_id": f"ORDER-BULK-{i:03}",
            "line_item_id": "LINE-1",
            "sku_id": "S-01",
            "quantity": "1",
            "paid_amount": "10.00",
            "refunded_amount": "0.00",
            "returned_units": "0",
            "status": "paid",
            "paid_at": "2026-10-20T10:00:00+08:00",
            "refund_updated_at": "",
        }
        for i in range(1, 102)
    ]
    upload = Upload("orders.csv", make_csv("order", rows))
    monkeypatch.setattr(
        st, "file_uploader", lambda label, **kwargs: upload if label == "订单行 CSV" else None
    )
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("订单成本与对账").run()
    app.radio[0].set_value("CSV 导入").run()
    app.checkbox(key="p4_mapping_合成演示").check().run()
    next(button for button in app.button if button.label == "预检 P4 所选文件").click().run()
    assert not app.exception and not app.error
    assert len(app.get("expander")) == 50
    assert len([item for item in app.warning if "未知或未归属费用" in item.value]) == 50
    next(item for item in app.number_input if item.label == "预检警告页").set_value(3).run()
    assert any("OL-BULK-101" in item.value for item in app.warning)
    next(item for item in app.number_input if item.label == "预检变更页").set_value(3).run()
    assert len(app.get("expander")) == 1
    assert "OL-BULK-101" in app.get("expander")[0].label
    next(item for item in app.text_input if item.label == "P4 本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:p4_confirm-确认并原子提交 P4 数据").click().run()
    assert not app.exception and not app.error
    with Session(engine) as session:
        assert len(session.exec(select(OrderLine)).all()) == 101
    engine.dispose()
