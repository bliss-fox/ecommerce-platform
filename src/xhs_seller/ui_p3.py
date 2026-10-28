"""Local evidence entry and editing of offline, non-publishing drafts."""

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import ContentDraft, DraftAsset, DraftAudit, DraftFact, Sku
from xhs_seller.services.drafts import (
    PLACEHOLDER,
    StaleDraftError,
    create_draft,
    render_markdown,
    save_asset,
    save_draft,
    save_fact,
)

KIND_LABELS = {"product_fact": "商品事实", "usage_experience": "使用经历"}
FACT_STATUS = {"unverified": "待核实", "verified": "人工已核实"}
RIGHTS_LABELS = {
    "unknown": "未知",
    "manual_owned": "自有素材（人工声明）",
    "authorized": "已获授权（人工声明）",
}
DRAFT_STATUS = {"draft": "草稿", "reviewed": "内部已审核", "rejected": "内部已拒绝"}


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def localize_audit(snapshot):
    if snapshot is None:
        return None
    return {
        key: local_time(value)
        if key in {"updated_at", "created_at", "checked_at"} and value is not None
        else [localize_audit(item) for item in json.loads(value)]
        if key in {"facts_json", "assets_json"}
        else value
        for key, value in snapshot.items()
    }


def flash_and_refresh(message):
    st.session_state.p3_flash = message
    st.rerun()


def track_form_revision(mode, sku_id, entity, record_id, revision):
    key = f"p3_seen_{mode}_{sku_id}_{entity}_{record_id}"
    previous = st.session_state.get(key)
    if previous is not None and previous != revision:
        st.warning("记录已变化，表单已刷新；请核对最新内容后再保存。")
    st.session_state[key] = revision
    return key


def fact_tab(engine, sku_id, operator, mode, facts):
    st.caption(
        "事实由人核实；标为已核实必须填写依据索引和带时区的核验时间。第一人称使用经历须单独记录。"
    )
    ids = {item.fact_id: item for item in facts}
    selected = st.selectbox("选择事实", ["新增"] + sorted(ids), key=f"p3_fact_{mode}_{sku_id}")
    existing = ids.get(selected)
    revision_key = (
        track_form_revision(mode, sku_id, "fact", selected, existing.revision) if existing else None
    )
    form_id = f"p3_fact_form_{mode}_{sku_id}_{selected}"
    if existing:
        form_id += f"_v{existing.revision}"
    with st.form(form_id):
        kinds = list(KIND_LABELS)
        kind = st.selectbox(
            "事实类型",
            kinds,
            index=kinds.index(existing.kind) if existing else 0,
            format_func=KIND_LABELS.get,
        )
        text = st.text_input("事实文本（单行）", value=existing.text if existing else "")
        statuses = list(FACT_STATUS)
        status = st.selectbox(
            "核验状态",
            statuses,
            index=statuses.index(existing.status) if existing else 0,
            format_func=FACT_STATUS.get,
        )
        evidence = st.text_input(
            "证据索引", value=existing.evidence_ref if existing and existing.evidence_ref else ""
        )
        checked_at = st.text_input(
            "核验时间（带时区）",
            value=local_time(existing.checked_at) if existing and existing.checked_at else "",
        )
        submitted = st.form_submit_button("保存事实")
    if submitted:
        try:
            save_fact(
                engine,
                sku_id=sku_id,
                kind=kind,
                fact_text=text,
                status=status,
                evidence_ref=evidence,
                checked_at=checked_at,
                operator=operator,
                real_data=mode == "真实数据",
                fact_id=existing.fact_id if existing else None,
                expected_revision=existing.revision if existing else None,
            )
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            if existing:
                st.session_state[revision_key] = existing.revision + 1
            flash_and_refresh("事实已保存；相关旧草稿需重新生成。")
    for item in facts:
        st.write(
            f"{item.fact_id} · 修订 {item.revision} · {FACT_STATUS[item.status]} · {item.text}"
        )


def asset_tab(engine, sku_id, operator, mode, assets):
    st.caption("仅保存本地路径索引，不读取或上传素材；权利信息由人提供，未自动认定可发布。")
    ids = {item.asset_id: item for item in assets}
    selected = st.selectbox("选择素材", ["新增"] + sorted(ids), key=f"p3_asset_{mode}_{sku_id}")
    existing = ids.get(selected)
    revision_key = (
        track_form_revision(mode, sku_id, "asset", selected, existing.revision)
        if existing
        else None
    )
    form_id = f"p3_asset_form_{mode}_{sku_id}_{selected}"
    if existing:
        form_id += f"_v{existing.revision}"
    with st.form(form_id):
        path = st.text_input("本地路径索引", value=existing.local_path if existing else "")
        choices = list(RIGHTS_LABELS)
        basis = st.selectbox(
            "权利依据",
            choices,
            index=choices.index(existing.rights_basis) if existing else 0,
            format_func=RIGHTS_LABELS.get,
        )
        ref = st.text_input(
            "权利依据索引",
            value=existing.rights_evidence_ref if existing and existing.rights_evidence_ref else "",
        )
        rights_confirmed = st.checkbox("本人确认权利依据适用于当前素材路径")
        submitted = st.form_submit_button("保存素材")
    if submitted:
        try:
            save_asset(
                engine,
                sku_id=sku_id,
                local_path=path,
                rights_basis=basis,
                rights_evidence_ref=ref,
                operator=operator,
                real_data=mode == "真实数据",
                asset_id=existing.asset_id if existing else None,
                expected_revision=existing.revision if existing else None,
                rights_confirmed=rights_confirmed,
            )
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))
        else:
            if existing:
                st.session_state[revision_key] = existing.revision + 1
            flash_and_refresh("素材索引已保存；相关旧草稿需重新生成。")
    for item in assets:
        st.write(
            f"{item.asset_id} · 修订 {item.revision} · {RIGHTS_LABELS[item.rights_basis]} · {item.local_path}"
        )


def draft_tab(engine, sku_id, operator, mode, drafts):
    st.caption("模板不联网。正文每行须与一条已核实事实完全一致；标题使用占位文字或一条已核实事实。")
    if st.button("生成离线草稿", key=f"p3_generate_{mode}_{sku_id}"):
        try:
            draft = create_draft(engine, sku_id=sku_id, operator=operator)
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[f"p3_draft_{mode}_{sku_id}"] = draft.draft_id
            flash_and_refresh("草稿已生成，尚未发布。")
    if not drafts:
        st.info("暂无草稿，可先记录事实与素材再生成。")
        return
    ids = {item.draft_id: item for item in drafts}
    selected = st.selectbox("选择草稿", sorted(ids), key=f"p3_draft_{mode}_{sku_id}")
    draft = ids[selected]
    st.write(f"草稿修订 {draft.revision}；当前状态：{DRAFT_STATUS[draft.status]}。")
    st.caption("修改已审核内容时，先保存为草稿，再单独审核；审核只表示内部状态。")
    revision_key = track_form_revision(mode, sku_id, "draft", selected, draft.revision)
    with st.form(f"p3_draft_form_{mode}_{sku_id}_{selected}_v{draft.revision}"):
        title = st.text_input(
            "标题", value=draft.title, help=f"可用 {PLACEHOLDER} 或完整的已核实事实原文。"
        )
        body = st.text_area("事实正文（每行一条）", value=draft.body, height=140)
        shots = st.text_area(
            "拍摄清单（每行一项）",
            value=draft.shot_list,
            height=100,
            help="可使用默认任务、拍摄整体/商品整体/商品细节/商品包装，或填写‘拍摄已核实事实：’加事实原文。其他内容请放在待核实事项中。",
        )
        needs = st.text_area(
            "补充待核实事项（每行一项）", value=draft.needs_verification, height=90
        )
        statuses = list(DRAFT_STATUS)
        status = st.selectbox(
            "内部状态", statuses, index=statuses.index(draft.status), format_func=DRAFT_STATUS.get
        )
        submitted = st.form_submit_button("保存草稿")
    if submitted:
        try:
            save_draft(
                engine,
                draft_id=draft.draft_id,
                expected_revision=draft.revision,
                title=title,
                body=body,
                shot_list=shots,
                needs_verification=needs,
                status=status,
                operator=operator,
            )
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.session_state[revision_key] = draft.revision + 1
            flash_and_refresh("草稿修订已保存；内部审核不代表发布。")
    try:
        markdown = render_markdown(engine, draft.draft_id)
    except StaleDraftError as exc:
        st.error(str(exc))
        return
    st.caption("预览和下载使用已保存的草稿版本；未保存的编辑不包含在导出中。")
    st.code(markdown, language="markdown")
    st.download_button(
        "下载 Markdown 草稿",
        markdown.encode("utf-8"),
        file_name=f"draft-{draft.draft_id}.md",
        mime="text/markdown",
    )


def render(engine, mode):
    st.header("离线内容草稿")
    st.warning("草稿仅供人工核对；系统不核验事实真伪、素材权利，也不发布或执行平台操作。")
    with Session(engine) as session:
        skus = session.exec(select(Sku).order_by(Sku.sku_id)).all()
    if not skus:
        st.info("请先添加 SKU。")
        return
    if "p3_flash" in st.session_state:
        st.success(st.session_state.pop("p3_flash"))
    sku_id = st.selectbox("选择 SKU", [item.sku_id for item in skus], key=f"p3_sku_{mode}")
    operator = st.text_input(
        "本地操作者标识", key=f"p3_operator_{mode}", help="使用不含个人信息的本地标识。"
    )
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        facts = session.exec(select(DraftFact).where(DraftFact.sku_id == sku_id)).all()
        assets = session.exec(select(DraftAsset).where(DraftAsset.sku_id == sku_id)).all()
        drafts = session.exec(select(ContentDraft).where(ContentDraft.sku_id == sku_id)).all()
        relevant_ids = (
            {item.fact_id for item in facts}
            | {item.asset_id for item in assets}
            | {item.draft_id for item in drafts}
        )
        audits = [
            item
            for item in session.exec(select(DraftAudit)).all()
            if item.record_id in relevant_ids
        ]
    tab1, tab2, tab3, tab4 = st.tabs(
        ["事实记录", "素材清单", "草稿与导出", "修订历史"],
        key=f"p3_tabs_{mode}_{sku_id}",
        on_change="rerun",
    )
    with tab1:
        fact_tab(engine, sku_id, operator, mode, facts)
    with tab2:
        asset_tab(engine, sku_id, operator, mode, assets)
    with tab3:
        draft_tab(engine, sku_id, operator, mode, drafts)
    with tab4:
        if not audits:
            st.info("暂无修订记录。")
        for item in sorted(audits, key=lambda row: row.updated_at, reverse=True):
            with st.expander(
                f"{item.entity_type} · {item.record_id} · {local_time(item.updated_at)}"
            ):
                st.write(f"本地标识：{item.operator}")
                st.json(
                    {
                        "原值": localize_audit(json.loads(item.old_json))
                        if item.old_json
                        else None,
                        "新值": localize_audit(json.loads(item.new_json)),
                    }
                )
