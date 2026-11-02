"""Focused candidate details and temporary quote comparison."""

import streamlit as st

from xhs_seller.services.product_queries import compare_quote, read_catalog
from xhs_seller.ui_navigation import go, select_sku, sku_links
from xhs_seller.ui_p1 import shanghai

COST_LABELS = {
    "unit_cost": "采购",
    "shipping": "发货",
    "packaging": "包装",
    "platform": "平台",
    "marketing": "获客",
    "after_sales": "售后损失",
}


def amount(cents):
    return "未知" if cents is None else f"¥{cents // 100}.{cents % 100:02d}"


def edit_button(kind, record_id, parent_id, label):
    st.button(
        label,
        key=f"r2_edit_{kind}_{record_id}",
        on_click=go,
        args=("编辑与导入", kind, record_id, parent_id),
    )


def change_product():
    st.session_state.pop("nav_sku", None)
    for key in list(st.session_state):
        if key.startswith("nav_product_sku_"):
            del st.session_state[key]


def scenario(quote):
    if (quote.min_qty is not None and quote.min_qty > 1) or quote.dropship_supported is False:
        st.warning("场景不匹配，不可据此采用。")
    elif quote.min_qty is None or quote.dropship_supported is None:
        st.info("起订量或代发信息未知，场景适用性未确认。")


def quote_details(quote):
    st.write(f"{quote.quote_id} · {quote.supplier_name}")
    st.write(
        f"采购：{amount(quote.unit_cost_cents)}；发货：{amount(quote.shipping_per_order_cents)}"
    )
    dropship = {True: "支持", False: "不支持", None: "未知"}[quote.dropship_supported]
    st.write(
        f"起订量：{quote.min_qty if quote.min_qty is not None else '未知'}；代发：{dropship}；"
        f"时效（小时）：{quote.lead_time_hours if quote.lead_time_hours is not None else '未知'}"
    )
    st.write(f"退货条款：{quote.return_terms or '未知'}；样品：{quote.sample_result}")
    scenario(quote)
    with st.expander(f"报价来源与版本 · {quote.quote_id}"):
        st.write(f"报价时间：{shanghai(quote.quoted_at)}；来源：{quote.source_ref}")
        st.write(f"样品依据：{quote.sample_evidence_ref or '未提供'}")
        st.write(f"修订 {quote.revision}；更新时间：{shanghai(quote.updated_at)}")
    edit_button("quote", quote.quote_id, quote.product_id, f"编辑报价 {quote.quote_id}")


def economics(sku, quote):
    result = compare_quote(sku, quote)
    scenario(quote)
    st.write(f"已知成本小计：¥{result.known_cost:.2f}")
    if result.contribution is None:
        st.write("贡献利润：不可完整计算；贡献率：不可完整计算")
        st.write("未知费用：" + "、".join(COST_LABELS[name] for name in result.missing))
    else:
        st.write(f"预计贡献利润：¥{result.contribution:.2f}；贡献率：{result.margin:.2%}")
    if result.break_even_cac is None:
        st.write("最多可承受获客费用的估计值：不可完整计算")
    else:
        st.write(f"最多可承受获客费用的估计值：¥{result.break_even_cac:.2f}")


def render(engine, mode, page):
    st.header(page)
    catalog = read_catalog(engine)
    if not catalog.products:
        st.info("尚未录入候选；请先在编辑与导入中录入候选、报价和 SKU。")
        st.button("录入候选", on_click=go, args=("编辑与导入",))
        return
    desired = st.session_state.get("nav_sku")
    if desired and desired not in catalog.skus:
        # Reuse the standard explicit recovery path, never silently replace IDs.
        select_sku(list(catalog.skus.values()), mode, f"nav_product_sku_{mode}")
        return
    left, right = st.columns([1, 3])
    with left:
        if page == "候选与报价":
            if desired:
                st.session_state.r2_product = catalog.skus[desired].product_id
            if st.session_state.get("r2_product") not in (None, *catalog.products):
                st.error("当前候选不存在，请重新选择；未自动替换为其他候选。")
                if st.button("清除失效候选选择"):
                    st.session_state.pop("r2_product", None)
                    st.rerun()
                return
            product_id = st.selectbox(
                "选择候选",
                list(catalog.products),
                key="r2_product",
                format_func=lambda value: f"{value} · {catalog.products[value].product_name}",
                on_change=change_product,
            )
            skus = [s for s in catalog.skus.values() if s.product_id == product_id]
        else:
            skus = list(catalog.skus.values())
            product_id = None
        # A candidate changes the option set; a distinct widget identity also
        # clears the browser's selected label, not only Python session state.
        sku_key = f"nav_product_sku_{mode}" + (f"_{product_id}" if product_id else "")
        sku_id = select_sku(skus, mode, sku_key) if skus else None
        sku = catalog.skus[sku_id] if sku_id else None
        if sku:
            product_id = sku.product_id
            sku_links(sku, current_page=page)
        elif page == "SKU 经济性":
            st.info("尚无 SKU；请先录入 SKU。")
            st.button("编辑与导入", on_click=go, args=("编辑与导入",))
            return
    with right:
        product = catalog.products[product_id]
        st.subheader(f"{product.product_id} · {product.product_name}")
        st.write(
            f"类目：{product.category}；状态：{product.status}；风险：{product.risk_notes or '未记录'}"
        )
        if sku:
            st.write(f"SKU：{sku.sku_id} · {sku.title}；状态：{sku.status}")
            st.write(f"已保存报价：{sku.quote_id}；售价：{amount(sku.sale_price_cents)}")
        else:
            st.info("此候选尚无 SKU，可先查看报价，再到编辑与导入录入 SKU。")
        with st.expander("版本与假设详情"):
            st.write(f"候选修订 {product.revision}；更新时间：{shanghai(product.updated_at)}")
            if sku:
                st.write(f"SKU 修订 {sku.revision}；更新时间：{shanghai(sku.updated_at)}")
                st.write(
                    f"假设来源：{sku.assumption_ref}；假设时间：{shanghai(sku.assumptions_as_of)}"
                )
                for label, value in (
                    ("包装", sku.packaging_per_order_cents),
                    ("平台", sku.platform_fee_per_order_cents),
                    ("获客", sku.marketing_per_order_cents),
                    ("售后损失", sku.after_sales_loss_per_order_cents),
                ):
                    st.write(f"{label}：{amount(value)}")
        edit_button("product", product_id, None, "编辑候选")
        if sku:
            edit_button("sku", sku.sku_id, product_id, "更换 SKU 报价 / 编辑假设")
        quotes = [q for q in catalog.quotes.values() if q.product_id == product_id]
        if page == "候选与报价":
            st.subheader("同候选报价")
            if not quotes:
                st.info("此候选尚无报价。")
            for quote in quotes:
                with st.container(border=True):
                    quote_details(quote)
        else:
            st.caption("人民币，一单一件；结果为假设估计，非实际订单利润。")
            st.subheader("已保存报价的经济性")
            economics(sku, catalog.quotes[sku.quote_id])
            st.subheader("比较同一候选的报价")
            st.caption("沿用同一 SKU 售价及其他费用假设；选择只用于临时比较，不修改已保存报价。")
            ids = [q.quote_id for q in quotes]
            comparison = st.selectbox(
                "临时比较报价",
                ids,
                index=ids.index(sku.quote_id),
                key=f"r2_compare_{sku_id}_v{sku.revision}",
            )
            with st.container(border=True):
                st.write(f"临时比较报价：{comparison}")
                economics(sku, catalog.quotes[comparison])
                quote_details(catalog.quotes[comparison])
