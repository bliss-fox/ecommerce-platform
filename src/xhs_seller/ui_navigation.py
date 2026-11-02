"""Six workspaces and session-only IDs; destinations always reload business data."""

import streamlit as st
from sqlmodel import Session

from xhs_seller.agents.snapshot import space_key
from xhs_seller.models import (
    ActionProposal,
    CandidateCheck,
    OrderLine,
    ProductCandidate,
    ProductResearchLink,
    ResearchNote,
    Sku,
    SupplierQuote,
)

WORKSPACES = {
    "协作工作台": ["经营提醒", "离线协作"],
    "选品工作区": ["候选与报价", "SKU 经济性", "研究与评分"],
    "内容工作区": ["离线草稿"],
    "经营核对": ["订单成本与对账", "异常与复盘"],
    "决策记录": ["本地提案与复盘"],
    "数据管理": ["数据导入", "编辑与导入", "订单费用导入", "数据备份"],
}


def sync_space(engine, mode):
    space = space_key(engine, mode)
    if st.session_state.get("nav_space") != space:
        # Unkeyed form widgets are removed too: old operator/decision values must
        # not reappear even when the two databases contain identical stable IDs.
        keep = {
            "nav_workspace",
            "data_space",
            "m1_session",
            "p1_operation",
            "p4_operation",
            "p5_operation",
        }
        keep.update(k for k in st.session_state if k.startswith("nav_page_"))
        for key in list(st.session_state):
            if key not in keep:
                del st.session_state[key]
        st.session_state.nav_space = space
    # Keep the chosen report time across import/history navigation. Streamlit
    # otherwise discards the widget when hidden, silently resetting it on return.
    if "p4_report_as_of" in st.session_state:
        st.session_state.p4_report_as_of = st.session_state.p4_report_as_of


def render_navigation():
    workspace = st.sidebar.radio(
        "工作区", list(WORKSPACES), key="nav_workspace", on_change=clear_target
    )
    pages = WORKSPACES[workspace]
    key = f"nav_page_{workspace}"
    if len(pages) == 1:
        st.session_state[key] = pages[0]
        st.sidebar.caption(f"页面：{pages[0]}")
        return pages[0]
    return st.sidebar.radio("页面", pages, key=key, on_change=clear_target)


def clear_target():
    st.session_state.pop("nav_target", None)


def go(page, kind=None, record_id=None, parent_id=None):
    workspace = next(group for group, pages in WORKSPACES.items() if page in pages)
    st.session_state.nav_workspace = workspace
    st.session_state[f"nav_page_{workspace}"] = page
    st.session_state.nav_target = (page, kind, record_id, parent_id, st.session_state.nav_space)


def resolve_target(engine, page):
    target = st.session_state.get("nav_target")
    if not target:
        return None
    destination, kind, record_id, parent_id, space = target
    if destination != page or space != st.session_state.nav_space:
        raise ValueError("导航上下文已变化，请重新选择目标")
    if kind is None:
        clear_target()
        return None
    allowed = {
        "sku": {"候选与报价", "SKU 经济性", "研究与评分", "离线草稿", "编辑与导入"},
        "product": {"编辑与导入"},
        "quote": {"编辑与导入"},
        "research": {"编辑与导入"},
        "link": {"编辑与导入"},
        "check": {"编辑与导入"},
        "order": {"订单成本与对账"},
        "proposal": {"本地提案与复盘"},
    }
    if kind not in allowed or page not in allowed[kind]:
        raise ValueError("导航目标类型与页面不匹配")
    model = {
        "sku": Sku,
        "order": OrderLine,
        "proposal": ActionProposal,
        "product": ProductCandidate,
        "quote": SupplierQuote,
        "research": ResearchNote,
        "link": ProductResearchLink,
        "check": CandidateCheck,
    }[kind]
    with Session(engine) as session:
        row = session.get(model, record_id)
        if row is None:
            raise ValueError("定位对象不存在，请重新选择；未自动选择其他记录")
        actual_parent = getattr(
            row, "product_id" if kind in {"sku", "quote", "link", "check"} else "sku_id", None
        )
        if parent_id is not None and parent_id != actual_parent:
            raise ValueError("定位对象归属不匹配，请重新选择")
    if kind == "sku":
        st.session_state.nav_sku = record_id
    if page == "编辑与导入":
        st.session_state.p1_target = (kind, record_id)
    clear_target()
    return record_id


def remember_sku(key):
    st.session_state.nav_sku = st.session_state[key]


def select_sku(skus, mode, key):
    by_id = {row.sku_id: row for row in skus}
    desired = st.session_state.get("nav_sku")
    if desired and desired not in by_id:
        st.error(f"当前 SKU {desired} 不存在，请重新选择；未自动替换为其他 SKU。")
        if st.button("清除失效 SKU 选择", key=f"{key}_clear"):
            st.session_state.pop("nav_sku", None)
            st.session_state.pop(key, None)
            st.rerun()
        return None
    if desired is not None:
        st.session_state[key] = desired
    sku_id = st.selectbox("选择 SKU", list(by_id), key=key, on_change=remember_sku, args=(key,))
    st.session_state.nav_sku = sku_id
    st.write(f"当前对象：{sku_id} · {by_id[sku_id].title}")
    return sku_id


def sku_links(sku, *, current_page):
    for page, label in (
        ("SKU 经济性", "查看经济性"),
        ("研究与评分", "查看研究与评分"),
        ("离线草稿", "核对事实与素材"),
    ):
        if page != current_page:
            st.button(
                label,
                key=f"nav_sku_{page}",
                on_click=go,
                args=(page, "sku", sku.sku_id, sku.product_id),
            )
