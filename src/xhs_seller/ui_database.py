"""Session-only recovery for database errors not handled within a page."""

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

FAULT = "database_fault"
RESET = "database_reset_pending"
EPOCH = "database_form_epoch"


def recovery_key(key):
    epoch = st.session_state.get(EPOCH, 0)
    return f"{key}_recovery{epoch}" if epoch else key


def restore_after_failure():
    """Discard action state before any main-page widgets are instantiated again."""
    if not st.session_state.pop(RESET, False):
        return
    manager = st.session_state.get("m1_session")
    if manager is not None and manager.run is not None:
        manager.run.cancel()
        manager.run = None  # Keep the pool and its occupied resource slots.
    keep = {
        FAULT, EPOCH, "data_space", "m1_session", "p1_operation", "p4_operation", "p5_operation",
        "p4_report_as_of", "p5_review_as_of", "import_group", "import_active_group",
        "edit_kind", "r2_product", "r3_order_source",
    }
    for key in list(st.session_state):
        if key not in keep and not key.startswith(("nav_", "p3_fact_", "p3_asset_", "p3_draft_")):
            del st.session_state[key]


def selected_target(page):
    mode = st.session_state.data_space
    if page == "编辑与导入" and st.session_state.get("p1_operation") == "手工编辑":
        kind = st.session_state.get("edit_kind")
        record_id = st.session_state.get(f"edit_record_{mode}_{kind}")
        if record_id and record_id != "新增":
            return (page, kind, record_id, None, st.session_state.nav_space)
    if page == "本地提案与复盘" and st.session_state.get("p5_operation") == "审核与待办":
        version = st.session_state.get(f"p5_selection_version_{mode}")
        record_id = st.session_state.get(f"p5_selected_{mode}_{version}")
        if record_id:
            return (page, "proposal", record_id, None, st.session_state.nav_space)
    return None


def mark_failure(page, *, initialization=False, target=None):
    mode = st.session_state.data_space
    context_keys = {
        "编辑与导入": ("p1_operation", "edit_kind"),
        "订单成本与对账": ("p4_operation", "p4_report_as_of"),
        "本地提案与复盘": ("p5_operation", "p5_review_as_of"),
        "异常与复盘": ("p5_review_as_of",),
        "数据导入": ("import_group", "import_active_group", "p4_report_as_of"),
        "研究与评分": (f"p2_asof_{mode}",),
        "离线协作": ("m1_as_of",),
        "经营提醒": ("wb_as_of",),
        "离线草稿": tuple(
            key for key in st.session_state
            if key.startswith((f"p3_fact_{mode}_", f"p3_asset_{mode}_", f"p3_draft_{mode}_"))
        ),
    }.get(page, ())
    st.session_state[FAULT] = {
        "page": page,
        "initialization": initialization,
        "target": target or st.session_state.get("nav_target") or selected_target(page),
        "context": {key: st.session_state[key] for key in context_keys if key in st.session_state},
    }
    # Streamlit's implicit widget IDs are not enumerable through session_state.
    # Fresh form identities also revoke their client-supplied values after reload.
    st.session_state[EPOCH] = st.session_state.get(EPOCH, 0) + 1
    st.session_state[RESET] = True
    # These are application artifacts, not widget values; revoke them immediately.
    for key in list(st.session_state):
        if key in {"p1_preview", "p1_edit_row", "p4_preview", "m1_report", "backup_snapshot"} or (
            key.endswith(("_receipt", "_import_flash")) or key.startswith("p5_decision_notice_")
        ):
            del st.session_state[key]


def retry(page):
    fault = st.session_state.pop(FAULT)
    restore_after_failure()
    if fault["page"] == page:
        for key, value in fault["context"].items():
            st.session_state[key] = value
    target = fault["target"]
    if target is not None and target[0] == page:
        st.session_state.nav_target = target


def failure_screen(page):
    fault = st.session_state.get(FAULT)
    if fault is None or (not fault["initialization"] and fault["page"] != page):
        return False
    st.error(
        "本地数据库操作失败，当前页面已停止。请重新读取并核对已保存记录及审计，"
        "再判断保存或决定的结果；系统不会自动重试原操作。"
    )
    st.caption(
        "重新读取会清除未提交表单、旧预检和会话下载，保留数据空间、页面及目标定位。"
        "也可从侧栏进入数据备份。"
    )
    st.button("重新读取页面", key="database_retry", on_click=retry, args=(page,))
    return True


def render_guarded(page, render):
    target = st.session_state.get("nav_target")
    content = st.empty()
    try:
        with content.container():
            render()
    except SQLAlchemyError:
        # Remove every previously rendered control/result in the failed page.
        content.empty()
        mark_failure(page, target=target)
        failure_screen(page)
