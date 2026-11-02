"""Read-only product workspace snapshot and existing unit economics."""

from dataclasses import dataclass

from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import ProductCandidate, Sku, SupplierQuote
from xhs_seller.services.economics import calculate


@dataclass(frozen=True)
class Catalog:
    products: dict[str, ProductCandidate]
    quotes: dict[str, SupplierQuote]
    skus: dict[str, Sku]


def read_catalog(engine):
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        return Catalog(
            {
                p.product_id: p
                for p in session.exec(
                    select(ProductCandidate).order_by(ProductCandidate.product_id)
                ).all()
            },
            {
                q.quote_id: q
                for q in session.exec(select(SupplierQuote).order_by(SupplierQuote.quote_id)).all()
            },
            {s.sku_id: s for s in session.exec(select(Sku).order_by(Sku.sku_id)).all()},
        )


def compare_quote(sku, quote):
    if sku.product_id != quote.product_id:
        raise ValueError("报价不属于当前 SKU 的候选")
    return calculate(
        sku.sale_price_cents,
        {
            "unit_cost": quote.unit_cost_cents,
            "shipping": quote.shipping_per_order_cents,
            "packaging": sku.packaging_per_order_cents,
            "platform": sku.platform_fee_per_order_cents,
            "marketing": sku.marketing_per_order_cents,
            "after_sales": sku.after_sales_loss_per_order_cents,
        },
    )
