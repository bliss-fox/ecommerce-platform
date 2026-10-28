from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.llm.mock import MockLLM
from xhs_seller.models import ProductCandidate, Sku, SupplierQuote
from xhs_seller.seed_demo import seed
from xhs_seller.services.economics import calculate, money_to_cents


@pytest.fixture
def populated(tmp_path):
    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    seed(engine)
    return engine


def costs(quote, sku):
    return {
        "unit_cost": quote.unit_cost_cents,
        "shipping": quote.shipping_per_order_cents,
        "packaging": sku.packaging_per_order_cents,
        "platform": sku.platform_fee_per_order_cents,
        "marketing": sku.marketing_per_order_cents,
        "after_sales": sku.after_sales_loss_per_order_cents,
    }


def test_a01_idempotent_scope_and_foreign_keys(populated):
    with Session(populated) as session:
        assert len(session.exec(select(ProductCandidate)).all()) == 3
        assert len(session.exec(select(SupplierQuote)).all()) == 6
        assert len(session.exec(select(Sku)).all()) == 3
        assert session.exec(text("PRAGMA foreign_keys")).one()[0] == 1
    assert set(inspect(populated).get_table_names()) == {
        "product_candidate",
        "supplier_quote",
        "sku",
        "import_batch",
        "import_failure",
        "research_note",
        "product_research_link",
        "candidate_check",
        "draft_fact",
        "draft_asset",
        "content_draft",
        "draft_audit",
        "revision_audit",
        "order_line",
        "expense",
        "expense_allocation",
        "cost_adjustment",
        "settlement_snapshot",
        "action_proposal",
        "proposal_audit",
    }
    assert "离线占位" in MockLLM().generate("test")


def test_quote_must_belong_to_sku_candidate(populated):
    with Session(populated) as session:
        sku = session.get(Sku, "S-01")
        sku.quote_id = "Q-02A"
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert session.get(Sku, "S-01").quote_id == "Q-01A"


def test_a02_complete_and_alternate_quote(populated):
    with Session(populated) as session:
        sku1 = session.get(Sku, "S-01")
        sku2 = session.get(Sku, "S-02")
        first = calculate(sku1.sale_price_cents, costs(session.get(SupplierQuote, "Q-01A"), sku1))
        second = calculate(sku2.sale_price_cents, costs(session.get(SupplierQuote, "Q-02A"), sku2))
        alternative = calculate(
            sku1.sale_price_cents, costs(session.get(SupplierQuote, "Q-01B"), sku1)
        )
    assert (first.known_cost, first.contribution, first.break_even_cac) == (
        Decimal("21.50"),
        Decimal("8.40"),
        Decimal("11.40"),
    )
    assert f"{first.margin:.2%}" == "28.09%"
    assert (second.contribution, second.break_even_cac) == (Decimal("13.40"), Decimal("18.40"))
    assert f"{second.margin:.2%}" == "26.85%"
    assert (alternative.known_cost, alternative.contribution) == (Decimal("21.90"), Decimal("8.00"))


def test_a03_unknown_and_explicit_zero(populated):
    with Session(populated) as session:
        sku = session.get(Sku, "S-03")
        inputs = costs(session.get(SupplierQuote, "Q-03A"), sku)
        result = calculate(sku.sale_price_cents, inputs)
        assert result.known_cost == Decimal("11.30")
        assert result.missing == ("marketing",)
        assert result.contribution is None and result.margin is None
        assert result.break_even_cac == Decimal("8.60")
        inputs["marketing"] = 0
        assert calculate(sku.sale_price_cents, inputs).contribution == Decimal("8.60")
        assert (
            calculate(
                sku.sale_price_cents, costs(session.get(SupplierQuote, "Q-03B"), sku)
            ).break_even_cac
            is None
        )


def test_a04_money_and_negative_profit(populated):
    assert money_to_cents("0.10") + money_to_cents("0.20") == money_to_cents("0.30")
    for bad in ("1.001", "NaN", "Infinity", "-1.00", "1e2", "1,000"):
        with pytest.raises(ValueError):
            money_to_cents(bad)
    with Session(populated) as session:
        sku = session.get(Sku, "S-01")
        inputs = costs(session.get(SupplierQuote, "Q-01A"), sku)
        result = calculate(1000, inputs)
        assert (result.contribution, result.break_even_cac) == (Decimal("-11.50"), Decimal("-8.50"))
        with pytest.raises(ValueError):
            calculate(0, inputs)
        inputs["unit_cost"] = -1
        with pytest.raises(ValueError):
            calculate(1000, inputs)


def test_a01_three_pages(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "ui.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    assert not app.exception
    assert any("纯合成演示数据" in item.value for item in app.warning)
    assert any("3 个候选" in item.value for item in app.markdown)
    app.sidebar.radio[0].set_value("候选与报价").run()
    assert not app.exception
    assert sum("合成甲工厂" in item.value for item in app.markdown) == 1
    assert any("Q-03B" in item.value for item in app.markdown)
    app.sidebar.radio[0].set_value("SKU 经济性").run()
    assert not app.exception
    assert any("¥8.40" in item.value for item in app.markdown)
    assert any("不可完整计算" in item.value for item in app.markdown)
