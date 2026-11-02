"""Shared presentation only; validation and atomic writes remain in imports."""

import csv
import io
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from xhs_seller.services.imports import SPECS, TIME_FIELDS, preflight, read_receipt
from xhs_seller.ui_csv import warn_formula_fields
from xhs_seller.ui_navigation import go
from xhs_seller.ui_preview import preview_page
from xhs_seller.ui_time import reset_time

GROUPS = ["候选、报价与研究", "订单、费用与结算"]
P4_NAMES = {
    "order": "订单行",
    "expense": "费用",
    "allocation": "费用分摊",
    "adjustment": "成本调整",
    "settlement": "结算快照",
}


def ensure_group(group):
    if st.session_state.get("import_active_group") != group:
        keep = {"p1_operation", "p4_operation", "p4_report_as_of"}
        for key in list(st.session_state):
            if key not in keep and key.startswith(("p1_", "p4_", "field_", "upload_", "preview_")):
                del st.session_state[key]
        st.session_state.import_active_group = group
    st.session_state.nav_import_group = group


def clear_result(namespace):
    st.session_state.pop(f"{namespace}_preview", None)
    st.session_state.pop(f"{namespace}_receipt", None)
    st.session_state.pop(f"{namespace}_import_flash", None)


def prepare_preview(engine, inputs, mode, namespace, **conditions):
    # A failed replacement must never leave the preceding preview confirmable.
    clear_result(namespace)
    try:
        preview = preflight(engine, inputs, real_data=mode == "真实数据", **conditions)
    except SQLAlchemyError:
        st.error(
            "预检失败，暂时无法读取数据库或保存预检记录。"
            "请核对本地数据库后重新预检；本次未提交业务数据。"
        )
        return None
    st.session_state[f"{namespace}_preview"] = preview
    return preview


def shanghai(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def display_snapshot(row):
    if row is None:
        return None
    return {
        key: shanghai(value) if key in TIME_FIELDS and value is not None else value
        for key, value in row.items()
    }


def file_step(namespace, names):
    st.subheader("1. 选择类型与文件")
    st.caption("按确定的文件类型导入，不会自动猜测列名。")
    kind = st.selectbox(
        "下载模板类型", list(names), format_func=names.get, key=f"{namespace}_template"
    )
    # Headers come from the contract-backed specs; templates contain no business data.
    out = io.StringIO(newline="")
    csv.writer(out).writerow(SPECS[kind][2])
    st.download_button(
        "下载空白 CSV 模板",
        out.getvalue().encode("utf-8-sig"),
        f"{kind}-template.csv",
        "text/csv",
        key=f"{namespace}_template_download",
    )


def preview_details(preview, names, namespace):
    st.subheader("3. 预检与差异")
    st.write(f"预检结果 · 批次：{preview.batch_id}")
    st.write("文件：" + "、".join(item["label"] for item in preview.files))
    st.caption(f"预检时点（上海）：{shanghai(preview.created_at)}")
    st.write(
        f"新增 {preview.counts['insert']}，更新 {preview.counts['update']}，"
        f"不变 {preview.counts['noop']}，错误 {preview.counts['rejected']}"
    )
    for warning in preview_page(
        preview.warnings, batch_id=preview.batch_id, kind=f"{namespace}_warning", label="预检警告页"
    ):
        st.warning(warning)
    if preview.errors:
        st.error("存在错误，整批不能提交。错误报告不包含原始行内容。")
        errors = [vars(item) for item in preview.errors]
        st.dataframe(errors, hide_index=True)
        label = "订单导入错误报告" if namespace == "p4" else "错误报告 CSV"
        warn_formula_fields(errors, {"record_id": "记录 ID"}, label)
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=list(errors[0]))
        writer.writeheader()
        writer.writerows(errors)
        st.download_button(
            "下载订单导入错误报告" if namespace == "p4" else "下载错误报告",
            out.getvalue().encode("utf-8-sig"),
            f"{namespace}-errors.csv",
            "text/csv",
        )
        return False
    st.caption("请核对变更前后值；金额以整数分显示，null 表示未知。")
    changes = preview_page(
        preview.changes, batch_id=preview.batch_id, kind=f"{namespace}_change", label="预检变更页"
    )
    if len(preview.changes) > len(changes):
        st.caption("请逐页核对；确认时提交整批预检结果。")
    for change in changes:
        missing = [
            name.removesuffix("_cents")
            for name, value in change.new.items()
            if name.endswith("_cents") and value is None
        ]
        if missing:
            st.write(f"{names[change.kind]} {change.record_id} 未知金额：{', '.join(missing)}")
        if change.old is not None and change.action == "update":
            for name, value in change.new.items():
                if name.endswith("_cents") and value != change.old[name]:
                    st.write(f"{change.record_id} {name}：{change.old[name]} → {value} 分")
        with st.expander(f"{names[change.kind]} {change.record_id} · {change.action}"):
            st.json({"原值": display_snapshot(change.old), "新值": display_snapshot(change.new)})
    st.subheader("4. 确认写入")
    return True


def open_batch(mode, batch_id):
    st.session_state.p1_operation = "修订历史"
    st.session_state[f"p1_history_kind_{mode}"] = "成功批次"
    st.session_state.p1_history_target = batch_id
    go("编辑与导入")


def recheck_orders():
    reset_time("p4_report_as_of")
    st.session_state.p4_operation = "报表与核对"
    go(
        "订单成本与对账",
        "order" if st.session_state.get("nav_order") else None,
        st.session_state.get("nav_order"),
    )


def receipt(engine, mode, namespace):
    batch_id = st.session_state.get(f"{namespace}_receipt")
    if not batch_id:
        return
    flash = st.session_state.pop(f"{namespace}_import_flash", None)
    try:
        result = read_receipt(engine, batch_id)
        confirmed_at = shanghai(result["confirmed_at"]) if result is not None else None
    except SQLAlchemyError:
        st.error("暂时无法读取提交回执，请重新读取核对；这不表示已提交数据已回滚，请勿重复提交。")
        return
    except (ValueError, OverflowError):
        st.error("提交回执元数据无效，请先备份并人工核对本批记录；未显示成功结果。")
        return
    if result is None:
        st.error("本批提交记录已不存在，请查看修订历史；未显示旧成功结果。")
        return
    if flash:
        st.success(flash)
    st.subheader("5. 查看结果")
    st.write(f"已提交批次：{batch_id}；确认时间：{confirmed_at}")
    counts = result["counts"]
    st.write(
        f"本批提交时：新增 {counts['insert']}，更新 {counts['update']}，不变 {counts['noop']}。"
    )
    st.caption("这是本批提交记录，当前业务数据可能已有后续修订。")
    st.button(
        "查看本批修订", key=f"{namespace}_history", on_click=open_batch, args=(mode, batch_id)
    )
    if namespace == "p4":
        st.button("按当前时点重新核对订单", on_click=recheck_orders)


def open_order_import():
    st.session_state.import_group = GROUPS[1]
    st.session_state.nav_import_group = GROUPS[1]
    go("数据导入")
