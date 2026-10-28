from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlmodel import Session, select

from xhs_seller.db import init_db, make_engine
from xhs_seller.models import ProductCandidate, Sku, SupplierQuote
from xhs_seller.seed_demo import seed


@pytest.mark.parametrize("precreate_tables", [False, True])
def test_concurrent_demo_initialization_preserves_records(tmp_path, precreate_tables):
    path = tmp_path / "demo.db"
    if precreate_tables:
        engine = make_engine(path)
        init_db(engine)
        engine.dispose()

    def run_concurrently():
        gate = Barrier(4)

        def initialize():
            engine = make_engine(path)
            try:
                gate.wait(timeout=10)
                seed(engine)
            finally:
                engine.dispose()

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(initialize) for _ in range(4)]
            for future in futures:
                future.result(timeout=20)

    run_concurrently()
    engine = make_engine(path)
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        product.product_name = "保留的本地修改"
        session.commit()
    engine.dispose()

    run_concurrently()
    engine = make_engine(path)
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").product_name == "保留的本地修改"
        assert len(session.exec(select(ProductCandidate)).all()) == 3
        assert len(session.exec(select(SupplierQuote)).all()) == 6
        assert len(session.exec(select(Sku)).all()) == 3
    engine.dispose()


def test_seed_failure_rolls_back_previously_flushed_records(tmp_path, monkeypatch):
    from xhs_seller import seed_demo

    engine = make_engine(tmp_path / "failed-seed.db")
    read_rows = seed_demo.read_rows

    def invalid_quote(name):
        rows = read_rows(name)
        if name == "supplier_quotes.csv":
            rows[0]["dropship_supported"] = "invalid"
        return rows

    with monkeypatch.context() as patch:
        patch.setattr(seed_demo, "read_rows", invalid_quote)
        with pytest.raises(ValueError, match="代发字段"):
            seed(engine)
    with Session(engine) as session:
        assert not session.exec(select(ProductCandidate)).all()
        assert not session.exec(select(SupplierQuote)).all()
        assert not session.exec(select(Sku)).all()
    seed(engine)
    with Session(engine) as session:
        assert len(session.exec(select(ProductCandidate)).all()) == 3
    engine.dispose()
