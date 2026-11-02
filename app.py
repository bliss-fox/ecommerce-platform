import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from xhs_seller.db import REAL_DB, SchemaMismatchError, init_db, make_engine
from xhs_seller.seed_demo import seed
from xhs_seller.ui_backup import render as render_backup
from xhs_seller.ui_collaboration import render as render_collaboration
from xhs_seller.ui_collaboration import sync_space
from xhs_seller.ui_database import (
    failure_screen,
    mark_failure,
    render_guarded,
    restore_after_failure,
)
from xhs_seller.ui_import_center import render as render_import_center
from xhs_seller.ui_navigation import render_navigation, resolve_target
from xhs_seller.ui_navigation import sync_space as sync_navigation_space
from xhs_seller.ui_p1 import render as render_p1
from xhs_seller.ui_p2 import render as render_p2
from xhs_seller.ui_p3 import render as render_p3
from xhs_seller.ui_p4 import render as render_p4
from xhs_seller.ui_p5 import render as render_p5
from xhs_seller.ui_p5 import render_review
from xhs_seller.ui_products import render as render_products
from xhs_seller.ui_workbench import render as render_workbench


def show():
    restore_after_failure()
    space_label = st.session_state.get("data_space", "合成演示")
    st.set_page_config(page_title=f"卖家助手 · {space_label}", layout="wide")
    st.sidebar.title("小红书卖家助手")
    mode = st.sidebar.selectbox("数据空间", ["合成演示", "真实数据"], key="data_space")
    if mode == "合成演示":
        st.warning("纯合成演示数据：仅用于离线软件验证，不代表真实报价、需求或经营结果。")
        engine = make_engine()
    else:
        st.info("真实数据使用独立的本地数据库；不会自动加入演示数据。")
        engine = make_engine(REAL_DB)
    sync_navigation_space(engine, mode)
    sync_space(engine, mode)
    page = render_navigation()
    if page == "数据备份":
        render_backup(engine, mode)
        return
    if failure_screen(page):
        return
    try:
        if mode == "合成演示":
            seed(engine)
        else:
            init_db(engine)
    except SchemaMismatchError as exc:
        st.error(f"{exc}。本次初始化未修改数据库；请先备份并确认适用的迁移方案。")
        return
    except SQLAlchemyError:
        mark_failure(page, initialization=True)
        failure_screen(page)
        return
    render_guarded(page, lambda: render_page(engine, mode, page))


def render_page(engine, mode, page):
    try:
        target_id = resolve_target(engine, page)
    except ValueError as exc:
        st.error(str(exc))
        return
    if page == "经营提醒":
        render_workbench(engine, mode)
        return
    if page == "离线协作":
        render_collaboration(engine, mode)
        return
    if page == "编辑与导入":
        render_p1(engine, mode)
        return
    if page == "数据导入":
        render_import_center(engine, mode)
        return
    if page == "研究与评分":
        render_p2(engine, mode)
        return
    if page == "离线草稿":
        render_p3(engine, mode)
        return
    if page == "订单成本与对账":
        render_p4(engine, mode, focus_id=target_id)
        return
    if page == "订单费用导入":
        from xhs_seller.ui_p4 import render_import

        st.header("订单、费用与结算导入")
        render_import(engine, mode)
        return
    if page == "异常与复盘":
        render_review(engine)
        return
    if page == "本地提案与复盘":
        render_p5(engine, mode, focus_id=target_id)
        return
    render_products(engine, mode, page)


show()
