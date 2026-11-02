import sqlite3
from decimal import Decimal

import pytest
from sqlalchemy import event

from xhs_seller.db import make_engine
from xhs_seller.seed_demo import seed
from xhs_seller.services.product_queries import compare_quote, read_catalog


def test_a33_catalog_and_comparison_are_read_only(tmp_path):
    engine = make_engine(tmp_path / "catalog.db")
    seed(engine)
    with sqlite3.connect(engine.url.database) as connection:
        before = list(connection.iterdump())
    catalog = read_catalog(engine)
    sku = catalog.skus["S-01"]
    assert compare_quote(sku, catalog.quotes["Q-01A"]).contribution == Decimal("8.40")
    assert compare_quote(sku, catalog.quotes["Q-01B"]).contribution == Decimal("8.00")
    with pytest.raises(ValueError, match="不属于"):
        compare_quote(sku, catalog.quotes["Q-02A"])
    with sqlite3.connect(engine.url.database) as connection:
        assert list(connection.iterdump()) == before


def test_a33_catalog_uses_one_read_snapshot_during_concurrent_write(tmp_path):
    engine = make_engine(tmp_path / "snapshot.db")
    seed(engine)
    with sqlite3.connect(engine.url.database) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    changed = []

    def concurrent_write(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith("SELECT SUPPLIER_QUOTE.") and not changed:
            with sqlite3.connect(engine.url.database) as connection:
                connection.execute("UPDATE sku SET revision=2 WHERE sku_id='S-01'")
                connection.execute("UPDATE supplier_quote SET revision=2 WHERE quote_id='Q-01A'")
            changed.append(True)

    event.listen(engine, "before_cursor_execute", concurrent_write)
    try:
        catalog = read_catalog(engine)
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_write)
    assert changed
    assert catalog.skus["S-01"].revision == catalog.quotes["Q-01A"].revision == 1
    current = read_catalog(engine)
    assert current.skus["S-01"].revision == current.quotes["Q-01A"].revision == 2
