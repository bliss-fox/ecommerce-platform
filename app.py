import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st
from sqlmodel import Session, select

from xhs_seller.db import REAL_DB, SchemaMismatchError, init_db, make_engine
from xhs_seller.models import ProductCandidate, Sku, SupplierQuote
from xhs_seller.seed_demo import seed
from xhs_seller.services.economics import calculate
from xhs_seller.ui_backup import render as render_backup
from xhs_seller.ui_p1 import render as render_p1
from xhs_seller.ui_p2 import render as render_p2
from xhs_seller.ui_p3 import render as render_p3
from xhs_seller.ui_p4 import render as render_p4
from xhs_seller.ui_p5 import render as render_p5


def amount(cents):
    return "未知" if cents is None else f"¥{cents // 100}.{cents % 100:02d}"


def show():
    st.set_page_config(page_title="卖家助手 · 合成演示", layout="wide")
    st.title("小红书卖家助手")
    mode = st.sidebar.selectbox("数据空间", ["合成演示", "真实数据"])
    if mode == "合成演示":
        st.warning("纯合成演示数据：仅用于离线软件验证，不代表真实报价、需求或经营结果。")
        engine = make_engine()
    else:
        st.info("真实数据使用独立的本地数据库；不会自动加入演示数据。")
        engine = make_engine(REAL_DB)
    page = st.sidebar.radio(
        "导航",
        [
            "首页",
            "候选与报价",
            "SKU 经济性",
            "编辑与导入",
            "研究与评分",
            "离线草稿",
            "订单成本与对账",
            "本地提案与复盘",
            "数据备份",
        ],
    )
    if page == "数据备份":
        render_backup(engine, mode)
        return
    try:
        if mode == "合成演示":
            seed(engine)
        else:
            init_db(engine)
    except SchemaMismatchError as exc:
        st.error(f"{exc}。本次初始化未修改数据库；请先备份并确认适用的迁移方案。")
        return
    if page == "编辑与导入":
        render_p1(engine, mode)
        return
    if page == "研究与评分":
        render_p2(engine, mode)
        return
    if page == "离线草稿":
        render_p3(engine, mode)
        return
    if page == "订单成本与对账":
        render_p4(engine, mode)
        return
    if page == "本地提案与复盘":
        render_p5(engine, mode)
        return
    with Session(engine) as session:
        products = session.exec(
            select(ProductCandidate).order_by(ProductCandidate.product_id)
        ).all()
        quotes = session.exec(select(SupplierQuote).order_by(SupplierQuote.quote_id)).all()
        skus = session.exec(select(Sku).order_by(Sku.sku_id)).all()
        if page == "首页":
            st.header("本地首页")
            st.write(f"{len(products)} 个候选 · {len(quotes)} 份报价 · {len(skus)} 个 SKU")
            st.write("金额是单件经营假设，并非实际订单利润；经营决定由店主自行作出。")
            st.write("订单成本和结算可在本地导入核对；经营建议仍由人工决定。")
        elif page == "候选与报价":
            st.header("候选与报价")
            for product in products:
                st.subheader(f"{product.product_id} · {product.product_name}")
                st.write(
                    f"类目：{product.category}；状态：{product.status}；风险：{product.risk_notes or '未记录'}"
                )
                for quote in quotes:
                    if quote.product_id != product.product_id:
                        continue
                    with st.container(border=True):
                        st.write(f"{quote.quote_id} · {quote.supplier_name}")
                        st.write(
                            f"采购：{amount(quote.unit_cost_cents)}；发货：{amount(quote.shipping_per_order_cents)}"
                        )
                        dropship = {True: "支持", False: "不支持", None: "未知"}[
                            quote.dropship_supported
                        ]
                        st.write(
                            f"起订量：{quote.min_qty if quote.min_qty is not None else '未知'}；代发：{dropship}；时效（小时）：{quote.lead_time_hours if quote.lead_time_hours is not None else '未知'}"
                        )
                        st.write(
                            f"退货条款：{quote.return_terms or '未知'}；样品：{quote.sample_result}"
                        )
                        if (
                            quote.min_qty is not None and quote.min_qty > 1
                        ) or quote.dropship_supported is False:
                            st.warning("场景不匹配，不可据此采用。")
        else:
            st.header("SKU 经济性")
            st.caption("人民币，一单一件；结果为估计，非实际订单利润。")
            for sku in skus:
                quote = next(q for q in quotes if q.quote_id == sku.quote_id)
                costs = {
                    "unit_cost": quote.unit_cost_cents,
                    "shipping": quote.shipping_per_order_cents,
                    "packaging": sku.packaging_per_order_cents,
                    "platform": sku.platform_fee_per_order_cents,
                    "marketing": sku.marketing_per_order_cents,
                    "after_sales": sku.after_sales_loss_per_order_cents,
                }
                result = calculate(sku.sale_price_cents, costs)
                st.subheader(f"{sku.sku_code} · {sku.title}")
                st.write(f"选用报价：{quote.quote_id}；售价：{amount(sku.sale_price_cents)}")
                st.write(f"已知成本小计：¥{result.known_cost:.2f}")
                if result.contribution is None:
                    st.write("贡献利润：不可完整计算；贡献率：不可完整计算")
                    labels = {
                        "unit_cost": "采购",
                        "shipping": "发货",
                        "packaging": "包装",
                        "platform": "平台",
                        "marketing": "获客",
                        "after_sales": "售后损失",
                    }
                    st.write("未知费用：" + "、".join(labels[item] for item in result.missing))
                else:
                    st.write(
                        f"预计贡献利润：¥{result.contribution:.2f}；贡献率：{result.margin:.2%}"
                    )
                if result.break_even_cac is None:
                    st.write("最多可承受获客费用的估计值：不可完整计算")
                else:
                    st.write(f"最多可承受获客费用的估计值：¥{result.break_even_cac:.2f}")
                if (
                    quote.min_qty is not None and quote.min_qty > 1
                ) or quote.dropship_supported is False:
                    st.warning("场景不匹配，不可据此采用。")
                with st.expander("比较同一候选的报价"):
                    st.caption(
                        "对每个报价沿用当前 SKU 的售价与其他费用假设；此处比较不会修改选中报价。"
                    )
                    for alternative in quotes:
                        if alternative.product_id != sku.product_id:
                            continue
                        alternative_costs = dict(
                            costs,
                            unit_cost=alternative.unit_cost_cents,
                            shipping=alternative.shipping_per_order_cents,
                        )
                        compared = calculate(sku.sale_price_cents, alternative_costs)
                        st.write(
                            f"{alternative.quote_id} · {alternative.supplier_name}：采购 {amount(alternative.unit_cost_cents)}，发货 {amount(alternative.shipping_per_order_cents)}"
                        )
                        st.write(f"已知成本小计：¥{compared.known_cost:.2f}")
                        if compared.break_even_cac is None:
                            st.write("最多可承受获客费用：不可完整计算")
                        else:
                            st.write(f"最多可承受获客费用的估计值：¥{compared.break_even_cac:.2f}")
                        if compared.contribution is None:
                            labels = {
                                "unit_cost": "采购",
                                "shipping": "发货",
                                "packaging": "包装",
                                "platform": "平台",
                                "marketing": "获客",
                                "after_sales": "售后损失",
                            }
                            st.write(
                                "贡献利润不可完整计算；未知费用："
                                + "、".join(labels[item] for item in compared.missing)
                            )
                        else:
                            delta = (
                                compared.contribution - result.contribution
                                if result.contribution is not None
                                else None
                            )
                            suffix = f"；相对当前报价 {delta:+.2f} 元" if delta is not None else ""
                            st.write(f"预计贡献利润：¥{compared.contribution:.2f}{suffix}")
                        if (
                            alternative.min_qty is not None and alternative.min_qty > 1
                        ) or alternative.dropship_supported is False:
                            st.warning("场景不匹配，不可据此采用。")
                        elif alternative.min_qty is None or alternative.dropship_supported is None:
                            st.info("起订量或代发信息未知，场景适用性未确认。")


show()
