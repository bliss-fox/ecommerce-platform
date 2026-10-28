import csv
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text
from sqlmodel import Session

from xhs_seller.db import init_db, make_engine
from xhs_seller.models import ProductCandidate, Sku, SupplierQuote
from xhs_seller.services.economics import money_to_cents

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "p1"


def read_rows(name):
    with (EXAMPLES / name).open(encoding="utf-8-sig", newline="") as source:
        return list(csv.DictReader(source))


def utc(value):
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("时间必须包含时区")
    return parsed.astimezone(UTC).isoformat()


def optional(value):
    return value.strip() if value.strip() else None


def seed(engine):
    init_db(engine)
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        for row in read_rows("product_candidates.csv"):
            if session.get(ProductCandidate, row["product_id"]) is None:
                session.add(
                    ProductCandidate(
                        **{
                            **row,
                            "revision": int(row["revision"]),
                            "updated_at": utc(row["updated_at"]),
                            "risk_notes": optional(row["risk_notes"]),
                        }
                    )
                )
        session.flush()
        for row in read_rows("supplier_quotes.csv"):
            if session.get(SupplierQuote, row["quote_id"]) is None:
                for field in ("unit_cost", "shipping_per_order"):
                    value = optional(row.pop(field))
                    row[field + "_cents"] = money_to_cents(value) if value is not None else None
                for field in ("min_qty", "lead_time_hours"):
                    value = optional(row[field])
                    row[field] = int(value) if value is not None else None
                value = optional(row["dropship_supported"])
                if value not in ("true", "false", None):
                    raise ValueError("代发字段必须是 true、false 或空")
                row["dropship_supported"] = {"true": True, "false": False, None: None}[value]
                for field in ("return_terms", "sample_evidence_ref"):
                    row[field] = optional(row[field])
                row["revision"] = int(row["revision"])
                row["updated_at"] = utc(row["updated_at"])
                row["quoted_at"] = utc(row["quoted_at"])
                session.add(SupplierQuote(**row))
        session.flush()
        for row in read_rows("skus.csv"):
            if session.get(Sku, row["sku_id"]) is None:
                for field in (
                    "sale_price",
                    "packaging_per_order",
                    "platform_fee_per_order",
                    "marketing_per_order",
                    "after_sales_loss_per_order",
                ):
                    value = optional(row.pop(field))
                    row[field + "_cents"] = money_to_cents(value) if value is not None else None
                row["revision"] = int(row["revision"])
                row["updated_at"] = utc(row["updated_at"])
                row["assumptions_as_of"] = utc(row["assumptions_as_of"])
                session.add(Sku(**row))
        session.commit()


if __name__ == "__main__":
    engine = make_engine()
    seed(engine)
    print("纯合成演示数据库已初始化。")
