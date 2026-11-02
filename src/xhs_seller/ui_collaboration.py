"""Offline task room; workers never call Streamlit or receive an engine."""

import json
from decimal import Decimal

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from xhs_seller.agents.report import export_report, navigation_target
from xhs_seller.agents.runtime import RunSession
from xhs_seller.agents.snapshot import current, read_snapshot, space_key
from xhs_seller.models import Sku
from xhs_seller.services.drafts import local_time
from xhs_seller.services.scoring import timestamp
from xhs_seller.ui_navigation import go
from xhs_seller.ui_p2 import DIMENSIONS, STATUSES
from xhs_seller.ui_time import report_time_input

ROLES = {
    "selection": "选品 Agent",
    "cost": "成本 Agent",
    "content": "内容 Agent",
    "review": "复核 Agent",
    "summary": "主 Agent 汇总",
    "main": "主 Agent",
}
STATES = {
    "running": "处理中",
    "completed": "已完成",
    "partial": "部分结果",
    "failed": "失败",
    "cancelled": "已取消",
    "timed_out": "已超时",
}
COST_LABELS = {
    "unit_cost": "采购",
    "shipping": "发货",
    "packaging": "包装",
    "platform": "平台",
    "marketing": "获客",
    "after_sales": "售后损失",
}
ERRORS = {
    "STEP_TIMEOUT": "步骤超时，结果未采用",
    "TOOL_DENIED": "拒绝越权工具调用",
    "INVALID_OUTPUT_OR_SOURCE": "来源或产物校验失败，结果未采用",
    "ROLE_FAILED": "角色处理失败，未保留异常原文",
    "RESOURCE_UNAVAILABLE": "执行资源不可用",
}


def money(value):
    return "未知" if value is None else f"{Decimal(value):.2f} 元"


def sync_space(engine, mode):
    if "m1_session" not in st.session_state:
        st.session_state.m1_session = RunSession()
    manager = st.session_state.m1_session
    space = space_key(engine, mode)
    if manager.space != space:
        manager.switch(space)
        for key in list(st.session_state):
            if key.startswith("m1_") and key != "m1_session":
                del st.session_state[key]
    return manager


def goto_content(engine, mode, run, sku_id):
    try:
        target = navigation_target(engine, mode, run, sku_id)
    except ValueError as exc:
        st.session_state.m1_flash = str(exc)
        return
    go("离线草稿", "sku", target)
    st.session_state.m1_navigate = True


def show_results(view, roles=("summary", "review", "selection", "cost", "content")):
    for role in roles:
        result = view["results"].get(role)
        if not result:
            continue
        data = json.loads(result.payload)
        st.subheader(ROLES[role])
        if role == "review":
            st.write("已核对获准产物与确定性工具输出；来源真实性和经营决定仍需人工核验。")
            if data["missing_roles"]:
                st.warning(
                    "仅复核部分产物；缺失：" + "、".join(ROLES[r] for r in data["missing_roles"])
                )
        elif role == "summary":
            st.write(data["note"])
            if data["missing_roles"]:
                st.warning("缺失角色：" + "、".join(ROLES[r] for r in data["missing_roles"]))
            for sku_id, report in data["business"].items():
                st.write(f"{sku_id} · {STATUSES[report['status']]}")
                if report["blocked"]:
                    st.error("存在失败阻断，不能采用建议；详见选品依据。")
                if report["contradiction_keys"]:
                    st.warning("存在反驳来源，须人工复核。")
            if data["hypotheses"] is not None:
                for sku_id, quotes in data["hypotheses"].items():
                    for quote_id, row in quotes.items():
                        if row["saved_quote"]:
                            st.write(
                                f"{sku_id} / {quote_id}（当前报价假设）：预计贡献 {money(row['contribution'])}；保本 CAC {money(row['break_even_cac'])}"
                            )
            for sku_id, row in data["content_ready"].items():
                st.write(f"{sku_id}：{row['verified_facts']} 条人工已核实事实。")
                if row["needs_verification"]:
                    st.warning(row["needs_verification"])
            st.write("人工建议：" + " ".join(data["next_steps"]))
        elif role == "selection":
            for sku_id, report in data.items():
                total = report["total"] if report["total"] is not None else "未知"
                st.write(
                    f"{sku_id} · {STATUSES[report['status']]} · 总分 {total} · 证据覆盖率 {report['coverage']}%"
                )
                if report["missing"]:
                    st.warning(
                        "待补或过期项："
                        + "、".join(DIMENSIONS.get(k, "场景适用性") for k in report["missing"])
                    )
        elif role == "cost":
            st.caption("一单一件假设估算，不是实际订单利润；比较不改变已保存报价。")
            for sku_id, quotes in data.items():
                st.write(sku_id)
                for quote_id, row in quotes.items():
                    with st.container(border=True):
                        st.write(f"{quote_id} · {'已保存报价' if row['saved_quote'] else '仅比较'}")
                        st.write(
                            f"预计贡献：{money(row['contribution'])}；保本 CAC：{money(row['break_even_cac'])}"
                        )
                        st.write(f"已知成本小计：{money(row['known_cost'])}")
                        if row["missing"]:
                            st.warning(
                                "未知费用：" + "、".join(COST_LABELS[k] for k in row["missing"])
                            )
                        if row["scenario"] == "mismatch":
                            st.warning("场景不匹配，不可据此采用。")
                        elif row["scenario"] == "unknown":
                            st.warning("起订量或代发信息未知，场景适用性未确认。")
        else:
            st.caption("以下为事实原文与未保存的离线模板；不代表权利已核验或内容可发布。")
            for sku_id, preview in data.items():
                st.write(sku_id)
                st.text(preview["body"] or "暂无已核实事实")
                st.text(preview["shot_list"])
                if preview["needs_verification"]:
                    st.warning(preview["needs_verification"])
        with st.expander(f"{ROLES[role]} · 来源、修订与完整产物"):
            st.json({"refs": result.refs, "result": data})


@st.fragment(run_every="1s")
def task_view(engine, mode, manager):
    if st.session_state.pop("m1_navigate", False):
        st.rerun(scope="app")
    if "m1_flash" in st.session_state:
        st.warning(st.session_state.pop("m1_flash"))
    run = manager.run
    if not run:
        return
    view = run.view()
    previous = st.session_state.get("m1_seen_run_status")
    st.session_state.m1_seen_run_status = (run.run_id, view["status"])
    if previous == (run.run_id, "running") and view["status"] != "running":
        # Refresh controls outside the fragment when the background task ends.
        st.rerun(scope="app")
    st.subheader(f"任务 · {STATES[view['status']]}")
    st.write("对象：" + "、".join(run.snapshot.sku_ids))
    st.caption(f"运行 {run.run_id}；报告时点 {local_time(run.snapshot.as_of)}")
    valid = current(engine, mode, run.snapshot)
    if not valid:
        st.error("来源已更新或无法读取：以下仅为旧结果；请重新运行。停止导出和跳转。")
    if view["status"] == "running":
        if st.button("取消任务", key="m1_cancel"):
            run.cancel()
            st.rerun(scope="app")
    elif view["status"] != "completed":
        st.warning("本次不是完整评估；缺失角色不能补为正常结论。")
    if "review" not in view["results"] and view["results"]:
        st.warning("以下为未经复核的部分产物。")
    for role, code in view["errors"].items():
        st.error(f"{ROLES[role]}：{ERRORS[code]}")
    show_results(view, roles=("summary",))
    if valid and view["status"] in {"completed", "partial"}:
        if st.button("生成本次报告", key="m1_export"):
            try:
                st.session_state.m1_report = (run.run_id, export_report(engine, mode, run))
            except ValueError as exc:
                st.error(str(exc))
        report = st.session_state.get("m1_report")
        if report and report[0] == run.run_id:
            st.caption("文件为生成时已核对的快照；来源之后变化时须重新生成。")
            st.download_button(
                "下载本次报告",
                report[1],
                file_name=f"sku-review-{run.run_id}.md",
                mime="text/markdown",
                key="m1_download",
            )
        for sku_id in run.snapshot.sku_ids:
            st.button(
                f"核对 {sku_id} 的事实与素材",
                key=f"m1_content_{sku_id}",
                on_click=goto_content,
                args=(engine, mode, run, sku_id),
            )
    else:
        st.session_state.pop("m1_report", None)
    with st.expander("任务过程、角色结果与来源"):
        for role, state in view["states"].items():
            st.write(f"{ROLES[role]} · {STATES.get(state, state)}")
        with st.expander("实际运行事件"):
            st.json(
                [
                    {
                        "顺序": e.sequence,
                        "角色": ROLES[e.role],
                        "状态": e.state,
                        "时间": local_time(e.at),
                        "摘要": e.detail,
                    }
                    for e in view["events"]
                ],
            )
        show_results(view, roles=("review", "selection", "cost", "content"))


def render(engine, mode):
    manager = sync_space(engine, mode)
    st.header("离线协作工作台")
    st.warning("离线协作演示：固定规则与模板分工，无真实模型；任务成功不代表可以销售或发布。")
    st.caption("仅本地会话保留进度，刷新或重启不保证恢复；说明文字仅作备注。最多三个 SKU。")
    if "m1_flash" in st.session_state:
        st.warning(st.session_state.pop("m1_flash"))
    with Session(engine) as session:
        skus = session.exec(select(Sku).order_by(Sku.sku_id)).all()
    if not skus:
        st.info("尚未录入 SKU，请到编辑与导入录入候选、报价和 SKU。")
        return
    st.caption("固定任务：SKU 评估 · 经济性、证据缺口与内容准备")
    labels = {s.sku_id: f"{s.sku_id} · {s.title}" for s in skus}
    if "m1_targets" not in st.session_state and manager.run:
        st.session_state.m1_targets = [s for s in manager.run.snapshot.sku_ids if s in labels]
    if "m1_as_of" not in st.session_state and manager.run:
        st.session_state.m1_as_of = manager.run.snapshot.as_of
    ids = st.multiselect(
        "评估对象（最多 3 个 SKU）", list(labels), format_func=labels.get, key="m1_targets"
    )
    goal = st.text_area("目标说明（可选，不含个人信息或凭证）", max_chars=500, key="m1_goal")
    value = report_time_input("报告时点（带时区）", key="m1_as_of")
    running = bool(manager.run and manager.run.view()["status"] == "running")
    if st.button(
        "重新运行" if manager.run else "开始离线评估",
        disabled=running, key="m1_start", type="primary",
    ):
        try:
            snap = read_snapshot(engine, mode, ids, timestamp(value))
            manager.start(snap, rerun=True, goal=goal)
            st.session_state.pop("m1_report", None)
        except ValueError as exc:
            st.error(str(exc))
        except (SQLAlchemyError, TypeError, KeyError):
            st.error("来源读取失败，请检查本地数据后重试。")
    st.button("刷新任务状态", key="m1_refresh")
    task_view(engine, mode, manager)
