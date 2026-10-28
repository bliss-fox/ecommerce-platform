"""P1 local editing and import UI, backed by the shared preflight service."""

import csv
import hashlib
import io
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import func, text
from sqlmodel import Session, select

from xhs_seller.models import ImportBatch, ImportFailure, RevisionAudit
from xhs_seller.services.imports import (
    CONTRACT,
    MONEY_FIELDS,
    SPECS,
    TIME_FIELDS,
    BaselineChangedError,
    InputFile,
    commit,
    preflight,
)
from xhs_seller.ui_csv import warn_formula_fields
from xhs_seller.ui_preview import preview_page

NAMES = {
    "product": "候选",
    "quote": "报价",
    "sku": "SKU",
    "research": "研究",
    "link": "研究关联",
    "check": "候选核验",
}
LABELS = {
    "research_id": "研究 ID",
    "link_id": "关联 ID",
    "check_id": "核验 ID",
    "keyword": "研究关键词",
    "captured_at": "采集时间（含时区）",
    "published_at": "发布时间（含时区，可空）",
    "pain_point": "痛点记录",
    "evidence_summary": "证据摘要",
    "evidence_status": "证据状态",
    "permission_basis": "许可依据",
    "dedupe_key": "原始材料去重键",
    "evidence_ref": "依据索引",
    "relationship": "关联关系",
    "active": "关联是否有效 true / false",
    "check_type": "核验类型",
    "result": "核验结果",
    "checked_at": "核验时间（含时区，可空）",
    "note": "核验备注",
    "product_id": "候选 ID",
    "quote_id": "报价 ID",
    "sku_id": "SKU ID",
    "sku_code": "SKU 编码",
    "revision": "修订号",
    "updated_at": "修订时间（含时区）",
    "product_name": "商品名称",
    "category": "类目",
    "status": "状态",
    "risk_notes": "风险备注",
    "supplier_name": "供应商标签",
    "source_ref": "报价来源索引",
    "quoted_at": "报价时间（含时区）",
    "unit_cost": "采购成本（元）",
    "shipping_per_order": "发货成本（元）",
    "min_qty": "起订量",
    "dropship_supported": "代发 true / false / 空",
    "lead_time_hours": "时效（小时）",
    "return_terms": "退货条款",
    "sample_result": "样品结论",
    "sample_evidence_ref": "样品依据索引",
    "title": "SKU 标题",
    "sale_price": "售价（元）",
    "packaging_per_order": "包装费用（元）",
    "platform_fee_per_order": "平台费用（元）",
    "marketing_per_order": "获客费用（元）",
    "after_sales_loss_per_order": "售后增量损失（元）",
    "assumption_ref": "假设来源索引",
    "assumptions_as_of": "假设时间（含时区）",
}

MULTILINE_FIELDS = {
    "product_name",
    "category",
    "risk_notes",
    "supplier_name",
    "source_ref",
    "return_terms",
    "sample_evidence_ref",
    "sku_code",
    "title",
    "assumption_ref",
    "keyword",
    "pain_point",
    "evidence_summary",
    "dedupe_key",
    "evidence_ref",
    "note",
}


def csv_input(kind, row):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=SPECS[kind][2])
    writer.writeheader()
    writer.writerow(row)
    return InputFile(kind, "manual-edit.csv", stream.getvalue().encode("utf-8"))


def serialize_field(obj, name):
    key = name + "_cents" if name in MONEY_FIELDS else name
    value = getattr(obj, key)
    if value is None:
        return ""
    if name in MONEY_FIELDS:
        return f"{value // 100}.{value % 100:02d}"
    if name in TIME_FIELDS:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def store_preview(engine, inputs, mode, source):
    st.session_state.p1_preview = preflight(engine, inputs, real_data=mode == "真实数据")
    st.session_state.p1_mode = mode
    st.session_state.p1_source = source
    st.session_state.p1_inputs = [
        (item.kind, item.name, hashlib.sha256(item.content).hexdigest()) for item in inputs
    ]


def edit_form(engine, mode):
    kind = st.selectbox("实体类型", list(NAMES), format_func=NAMES.get, key="edit_kind")
    model, id_name, fields = SPECS[kind]
    with Session(engine) as session:
        objects = session.exec(select(model)).all()
    by_id = {getattr(obj, id_name): obj for obj in objects}
    selected = st.selectbox("选择记录", ["新增"] + sorted(by_id), key=f"edit_record_{mode}_{kind}")
    existing = by_id.get(selected)
    revision = existing.revision if existing else None
    context = (mode, kind, selected, revision)
    revision_key = f"p1_seen_{mode}_{kind}_{selected}"
    previous_revision = st.session_state.get(revision_key)
    if existing and previous_revision is not None and previous_revision != revision:
        st.warning("记录已变化，表单已刷新；请核对最新内容后再预检。")
    if existing:
        st.session_state[revision_key] = revision
    field_scope = f"{mode}_{kind}_{selected}" + (f"_v{revision}" if existing else "")
    if (
        st.session_state.get("p1_source") == "手工编辑"
        and st.session_state.get("p1_edit_context") != context
    ):
        st.session_state.pop("p1_preview", None)
        st.session_state.pop("p1_edit_row", None)
    preview = st.session_state.get("p1_preview")
    locked = (
        st.session_state.get("p1_source") == "手工编辑"
        and st.session_state.get("p1_edit_context") == context
        and preview is not None
        and not preview.errors
    )
    if locked:
        st.info("当前预检内容已锁定；要修改，请先取消预检并重新预检。")
        if st.button("修改并重新预检"):
            for name, value in st.session_state.pop("p1_edit_row").items():
                st.session_state[f"field_{field_scope}_{name}"] = value
            st.session_state.p1_restore_context = context
            st.session_state.pop("p1_preview", None)
            st.rerun()
        return
    restoring = st.session_state.pop("p1_restore_context", None) == context
    st.caption("归档候选或 SKU 请修改状态；为保留引用和修订历史，不提供物理删除。")
    with st.form(f"edit_form_{field_scope}"):
        row = {}
        for name in fields:
            if existing is not None and name == "revision":
                value = str(existing.revision + 1)
            elif existing is not None and name == "updated_at":
                value = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            elif existing is not None:
                value = serialize_field(existing, name)
            elif name == "revision":
                value = "1"
            elif name in TIME_FIELDS:
                value = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()
            else:
                value = ""
            input_widget = st.text_area if name in MULTILINE_FIELDS else st.text_input
            row[name] = input_widget(
                LABELS[name],
                value=None if restoring else value,
                key=f"field_{field_scope}_{name}",
                disabled=(
                    existing is not None
                    and (
                        name == id_name
                        or name
                        in (
                            {"product_id"}
                            if kind == "quote"
                            else {"product_id", "sku_code"}
                            if kind == "sku"
                            else {"product_id", "research_id"}
                            if kind == "link"
                            else {"product_id", "check_type"}
                            if kind == "check"
                            else set()
                        )
                    )
                ),
            )
        submitted = st.form_submit_button("预检编辑")
    if submitted:
        store_preview(engine, [csv_input(kind, row)], mode, "手工编辑")
        st.session_state.p1_edit_context = context
        preview = st.session_state.p1_preview
        if not preview.errors:
            if existing and (
                preview.changes[0].old is None or preview.changes[0].old["revision"] != revision
            ):
                st.session_state.pop("p1_preview", None)
                st.warning("记录已变化，请刷新并核对最新内容后重新预检。")
                return
            st.session_state.p1_edit_row = row
            st.rerun()


def import_form(engine, mode):
    st.caption(f"数据契约：{CONTRACT}。可在同一批次中上传多个类型，全部预检通过后才可提交。")
    files = []
    for kind, label in NAMES.items():
        upload = st.file_uploader(f"{label} CSV", type="csv", key=f"upload_{mode}_{kind}")
        if upload is not None:
            files.append(InputFile(kind, upload.name, upload.getvalue()))
    fingerprints = [
        (item.kind, item.name, hashlib.sha256(item.content).hexdigest()) for item in files
    ]
    if st.session_state.get("p1_mode") != mode:
        st.session_state.pop("p1_preview", None)
    if (
        st.session_state.get("p1_source") == "CSV 导入"
        and st.session_state.get("p1_inputs") != fingerprints
    ) or (st.session_state.get("p1_source") == "合成研究示例" and files):
        st.session_state.pop("p1_preview", None)
    if st.button("预检所选文件", disabled=not files):
        store_preview(engine, files, mode, "CSV 导入")
    if mode == "合成演示" and st.button(
        "预检合成研究示例", disabled=bool(files), help="请先清除已选 CSV 文件。"
    ):
        directory = Path(__file__).resolve().parents[2] / "examples" / "p2"
        examples = [
            InputFile(kind, name, (directory / name).read_bytes())
            for kind, name in (
                ("research", "research_notes.csv"),
                ("link", "product_research_links.csv"),
                ("check", "candidate_checks.csv"),
            )
        ]
        store_preview(engine, examples, mode, "合成研究示例")


def preview_panel(engine, mode, operation):
    preview = st.session_state.get("p1_preview")
    if (
        preview is None
        or st.session_state.get("p1_mode") != mode
        or (
            st.session_state.get("p1_source") != operation
            and not (
                operation == "CSV 导入" and st.session_state.get("p1_source") == "合成研究示例"
            )
        )
    ):
        return
    st.subheader("预检结果")
    st.write(f"来源：{st.session_state.p1_source}；批次：{preview.batch_id}")
    st.write("文件：" + "、".join(item["label"] for item in preview.files))
    st.write(
        f"新增 {preview.counts['insert']}，更新 {preview.counts['update']}，不变 {preview.counts['noop']}，错误 {preview.counts['rejected']}"
    )
    for warning in preview_page(
        preview.warnings, batch_id=preview.batch_id, kind="p1_warning", label="预检警告页"
    ):
        st.warning(warning)
    if preview.errors:
        st.error("存在错误，整批不能提交。错误报告不包含原始行内容。")
        errors = [vars(item) for item in preview.errors]
        st.dataframe(errors, hide_index=True)
        warn_formula_fields(errors, {"record_id": "记录 ID"}, "错误报告 CSV")
        out = io.StringIO(newline="")
        writer = csv.DictWriter(out, fieldnames=list(errors[0]))
        writer.writeheader()
        writer.writerows(errors)
        st.download_button(
            "下载错误报告", out.getvalue().encode("utf-8-sig"), "errors.csv", "text/csv"
        )
        return
    st.caption("请核对以下变更；金额字段以整数分显示，null 表示未知。")
    changes = preview_page(
        preview.changes, batch_id=preview.batch_id, kind="p1_change", label="预检变更页"
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
            st.write(f"{NAMES[change.kind]} {change.record_id} 未知费用：{', '.join(missing)}")
        if change.old is not None and change.action == "update":
            for name, value in change.new.items():
                if name.endswith("_cents") and value != change.old[name]:
                    st.write(f"{change.record_id} {name}：{change.old[name]} → {value} 分")
    for change in changes:
        with st.expander(f"{NAMES[change.kind]} {change.record_id} · {change.action}"):
            st.json({"原值": display_snapshot(change.old), "新值": display_snapshot(change.new)})
    with st.form("confirm_import"):
        operator = st.text_input("本地操作者标识", help="使用不含姓名、电话或凭证的本地标识。")
        confirm = st.form_submit_button("确认并原子提交")
    if confirm:
        try:
            commit(engine, preview, operator=operator)
        except BaselineChangedError as exc:
            st.session_state.pop("p1_preview", None)
            st.session_state.pop("p1_edit_row", None)
            st.error(str(exc))
        except ValueError as exc:
            st.error(str(exc))
        else:
            if st.session_state.get("p1_source") == "手工编辑":
                edit_mode, kind, selected, _revision = st.session_state.p1_edit_context
                if selected != "新增":
                    st.session_state[f"p1_seen_{edit_mode}_{kind}_{selected}"] = preview.changes[
                        0
                    ].new["revision"]
            st.session_state.pop("p1_preview", None)
            st.session_state.pop("p1_edit_row", None)
            st.success("已提交；业务记录和修订审计已写入同一事务。")


def shanghai(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def display_snapshot(row):
    if row is None:
        return None
    return {
        key: shanghai(value) if key in TIME_FIELDS and value is not None else value
        for key, value in row.items()
    }


def history(engine, mode):
    st.subheader("本地导入和修订历史")
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        batches = session.exec(select(ImportBatch).order_by(ImportBatch.confirmed_at.desc())).all()
        failures = session.exec(
            select(ImportFailure).order_by(ImportFailure.created_at.desc())
        ).all()
        if not batches and not failures:
            st.info("暂无批次记录。")
            return
        kind = st.radio(
            "历史类别",
            ["成功批次", "失败预检"],
            index=0 if batches else 1,
            horizontal=True,
            key=f"p1_history_kind_{mode}",
        )
        records = batches if kind == "成功批次" else failures
        if not records:
            st.info("此类别暂无批次记录。")
            return
        by_id = {record.batch_id: record for record in records}
        selected = st.selectbox(
            "选择历史批次",
            list(by_id),
            format_func=lambda batch_id: (
                f"{shanghai(by_id[batch_id].confirmed_at if kind == '成功批次' else by_id[batch_id].created_at)} · {batch_id}"
            ),
            key=f"p1_history_batch_{mode}_{kind}",
        )
        record = by_id[selected]
        if kind == "失败预检":
            st.json(
                {
                    "文件": json.loads(record.files_json),
                    "结果": json.loads(record.counts_json),
                    "错误": json.loads(record.errors_json),
                }
            )
            return
        st.write(f"操作者：{record.operator}；契约：{record.contract_version}")
        st.json({"文件": json.loads(record.files_json), "结果": json.loads(record.counts_json)})
        count = session.exec(
            select(func.count())
            .select_from(RevisionAudit)
            .where(RevisionAudit.batch_id == selected)
        ).one()
        if not count:
            st.info("此批次没有业务修订。")
            return
        page_size = 50
        page_count = (count + page_size - 1) // page_size
        page = (
            st.number_input(
                "修订页",
                min_value=1,
                max_value=page_count,
                value=1,
                step=1,
                key=f"p1_history_page_{mode}_{selected}",
            )
            if page_count > 1
            else 1
        )
        st.caption(f"共 {count} 条修订，第 {page} / {page_count} 页，每页最多 {page_size} 条。")
        audits = session.exec(
            select(RevisionAudit)
            .where(RevisionAudit.batch_id == selected)
            .order_by(RevisionAudit.entity_type, RevisionAudit.record_id, RevisionAudit.audit_id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        ).all()
        for audit in audits:
            st.write(
                f"{NAMES.get(audit.entity_type, audit.entity_type)} {audit.record_id}：{audit.old_revision} → {audit.new_revision}"
            )
            st.json(
                {
                    "原值": display_snapshot(json.loads(audit.old_json))
                    if audit.old_json
                    else None,
                    "新值": display_snapshot(json.loads(audit.new_json)),
                }
            )


def render(engine, mode):
    st.header("编辑、导入与审计")
    operation = st.radio("操作", ["手工编辑", "CSV 导入", "修订历史"], horizontal=True)
    if operation == "手工编辑":
        edit_form(engine, mode)
    elif operation == "CSV 导入":
        import_form(engine, mode)
    else:
        history(engine, mode)
    if operation != "修订历史":
        preview_panel(engine, mode, operation)
