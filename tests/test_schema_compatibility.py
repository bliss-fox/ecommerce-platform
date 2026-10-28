from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.schema import CreateTable
from streamlit.testing.v1 import AppTest

from xhs_seller.db import init_db, make_engine
from xhs_seller.models import Sku


def incompatible_database(path):
    engine = make_engine(path)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE product_candidate (product_id TEXT PRIMARY KEY, product_name TEXT)"
        )
        connection.exec_driver_sql(
            "INSERT INTO product_candidate (product_id, product_name) VALUES ('PC-OLD', 'preserved')"
        )
    return engine


def assert_original_database_intact(engine):
    assert inspect(engine).get_table_names() == ["product_candidate"]
    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            "SELECT product_id, product_name FROM product_candidate"
        ).all() == [("PC-OLD", "preserved")]


def test_incompatible_existing_table_is_rejected_before_creating_tables(tmp_path):
    engine = incompatible_database(tmp_path / "old.db")
    with pytest.raises(RuntimeError, match="product_candidate"):
        init_db(engine)
    assert_original_database_intact(engine)
    engine.dispose()


@pytest.mark.parametrize(
    ("removed", "reason"),
    [
        ("PRIMARY KEY (sku_id),", "主键"),
        ("UNIQUE (sku_code),", "唯一约束"),
        (
            "FOREIGN KEY(quote_id, product_id) REFERENCES supplier_quote (quote_id, product_id),",
            "外键",
        ),
        ("title VARCHAR NOT NULL", "必填约束"),
    ],
)
def test_missing_model_constraint_is_rejected_without_schema_changes(tmp_path, removed, reason):
    engine = make_engine(tmp_path / "missing-constraint.db")
    ddl = str(CreateTable(Sku.__table__).compile(engine))
    assert removed in ddl
    replacement = "title VARCHAR" if removed.startswith("title ") else ""
    with engine.begin() as connection:
        connection.exec_driver_sql(ddl.replace(removed, replacement, 1))
        before = connection.exec_driver_sql(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='sku'"
        ).scalar_one()
    with pytest.raises(RuntimeError, match=reason):
        init_db(engine)
    assert inspect(engine).get_table_names() == ["sku"]
    with engine.connect() as connection:
        assert (
            connection.exec_driver_sql(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='sku'"
            ).scalar_one()
            == before
        )
    engine.dispose()


@pytest.mark.parametrize("partial", [False, True])
def test_unique_index_can_satisfy_constraint_only_when_not_partial(tmp_path, partial):
    engine = make_engine(tmp_path / "indexed.db")
    ddl = str(CreateTable(Sku.__table__).compile(engine))
    with engine.begin() as connection:
        connection.exec_driver_sql(ddl.replace("UNIQUE (sku_code),", "", 1))
        suffix = " WHERE status = 'ready_for_review'" if partial else ""
        connection.exec_driver_sql(f"CREATE UNIQUE INDEX sku_unique_code ON sku (sku_code){suffix}")
    if partial:
        with pytest.raises(RuntimeError, match="唯一约束"):
            init_db(engine)
        assert inspect(engine).get_table_names() == ["sku"]
    else:
        init_db(engine)
        assert "product_candidate" in inspect(engine).get_table_names()
    engine.dispose()


@pytest.mark.parametrize("mode", ["合成演示", "真实数据"])
def test_incompatible_database_has_actionable_ui_error_without_changes(monkeypatch, tmp_path, mode):
    from xhs_seller import db

    incompatible = incompatible_database(tmp_path / "incompatible.db")
    demo = incompatible if mode == "合成演示" else make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else incompatible)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    if mode == "真实数据":
        app.sidebar.selectbox[0].set_value(mode).run()
    assert not app.exception
    assert any(
        "数据库结构" in item.value and "product_candidate" in item.value for item in app.error
    )
    assert_original_database_intact(incompatible)
    incompatible.dispose()
    if demo is not incompatible:
        demo.dispose()
