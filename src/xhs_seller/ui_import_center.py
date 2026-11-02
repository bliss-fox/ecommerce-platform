"""One entry point for the two explicitly selected import workflows."""

import streamlit as st

from xhs_seller.ui_import_flow import GROUPS, ensure_group
from xhs_seller.ui_navigation import go
from xhs_seller.ui_p1 import import_form, preview_panel
from xhs_seller.ui_p4 import render_import


def render(engine, mode):
    st.header("数据导入")
    st.caption("选择文件 → 核对来源 → 预检与差异 → 确认写入 → 查看结果")
    if "import_group" not in st.session_state:
        st.session_state.import_group = st.session_state.get("nav_import_group", GROUPS[0])
    group = st.radio("导入内容", GROUPS, key="import_group", horizontal=True)
    ensure_group(group)
    if group == GROUPS[0]:
        import_form(engine, mode)
        preview_panel(engine, mode, "CSV 导入")
    else:
        if st.session_state.get("nav_order"):
            st.caption(
                f"待重新核对订单：{st.session_state.nav_order}；文件仍按整批校验，不自动筛选或改写。"
            )
            st.button(
                "返回订单核对",
                on_click=go,
                args=("订单成本与对账", "order", st.session_state.nav_order),
            )
        render_import(engine, mode)
