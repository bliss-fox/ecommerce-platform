"""Bound the number of import preview elements rendered in one rerun."""

import streamlit as st


def preview_page(items, *, batch_id, kind, label, page_size=50):
    if len(items) <= page_size:
        return items
    pages = (len(items) + page_size - 1) // page_size
    page = st.number_input(
        label,
        min_value=1,
        max_value=pages,
        value=1,
        step=1,
        key=f"preview_{kind}_{batch_id}",
    )
    start = (page - 1) * page_size
    st.caption(
        f"共 {len(items)} 条，第 {page} / {pages} 页；当前显示 {start + 1}–{min(start + page_size, len(items))}。"
    )
    return items[start : start + page_size]
