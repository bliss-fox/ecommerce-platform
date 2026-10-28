"""P2 evidence inspection and deterministic local scoring."""

import json
from collections import defaultdict
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import (
    CandidateCheck,
    ProductCandidate,
    ProductResearchLink,
    ResearchNote,
    RevisionAudit,
    Sku,
    SupplierQuote,
)
from xhs_seller.services.scoring import rank, score, timestamp
from xhs_seller.ui_time import report_time_input

DIMENSIONS = {
    "demand": "需求",
    "margin": "利润",
    "fulfillment": "履约",
    "content": "内容可展示",
    "compliance": "销售与声明核验",
}
STATUSES = {
    "blocked": "阻断",
    "inactive": "已归档或拒绝，不参与排序",
    "incomplete": "资料不完整",
    "scenario_mismatch": "场景不匹配",
    "not_viable": "单件贡献非正",
    "review_required": "反驳证据待复核",
    "ready_for_manual_review": "可进入人工评审",
}
SLOTS = {
    "demand_1": "第 1 个独立支持来源",
    "demand_2": "第 2 个独立支持来源",
    "demand_3": "第 3 个独立支持来源",
    "economics": "经济性假设",
    "fulfillment": "履约证据",
    "visual_demo": "可视化演示",
    "fact_sheet": "事实表",
    "media_rights": "素材权利",
    "sales_eligibility": "销售条件",
    "claims_review": "声明核验",
}


DISPLAY_TIMES = {
    "as_of",
    "captured_at",
    "published_at",
    "checked_at",
    "quoted_at",
    "assumptions_as_of",
    "observed_at",
    "updated_at",
}


def localize(value):
    if isinstance(value, dict):
        return {
            key: timestamp(item).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()
            if key in DISPLAY_TIMES and item is not None
            else localize(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [localize(item) for item in value]
    return value


def render_result(sku, result):
    st.subheader(f"{sku.sku_code} · {STATUSES[result['status']]}")
    st.write(
        f"规则 {result['rule_version']}；报告时间 {localize({'as_of': result['as_of']})['as_of']}；候选修订 {result['product_revision']}，SKU 修订 {result['sku_revision']}，报价 {result['quote_id']} 修订 {result['quote_revision']}"
    )
    if result["total"] is None:
        st.write(f"完整总分：不可计算；已知分项贡献小计：{result['subtotal']:.2f}")
    else:
        st.write(f"总分：{result['total']:.2f}")
    st.write(
        f"证据覆盖率：{result['coverage']}%（已填 {result['coverage'] // 10}/10 槽；不是预测准确率）"
    )
    if result["blocked"]:
        st.error("存在失败阻断；只有同一记录更高修订的有依据 pass 才能解除。")
        st.json(localize(result["blocked"]))
    if result["inactive_reasons"]:
        labels = {
            "product_archived": "候选已归档",
            "product_rejected": "候选已拒绝",
            "sku_archived": "SKU 已归档",
        }
        st.warning(
            "不参与待评审排序：" + "、".join(labels[item] for item in result["inactive_reasons"])
        )
    if result["missing"]:
        st.warning(
            "待补或过期项：" + "、".join(DIMENSIONS.get(key, key) for key in result["missing"])
        )
    if result["contradiction_keys"]:
        st.warning("存在有效反驳证据，需人工复核。")
    st.subheader("评分分项")
    for key, label in DIMENSIONS.items():
        value = result["dimensions"][key]
        contribution = result["contributions"][key]
        st.write(
            f"{label}：{value if value is not None else '未知'}；加权贡献：{f'{contribution:.2f}' if contribution is not None else '未知'}"
        )
    with st.expander("需求来源、去重与反驳"):
        st.write("有效独立支持键：" + ("、".join(result["support_keys"]) or "无"))
        st.write("有效独立反驳键：" + ("、".join(result["contradiction_keys"]) or "无"))
        if result["conflict"]:
            st.warning("证据冲突：需求分上限为 40。")
        if not result["evidence"]:
            st.info("暂无关联研究记录。")
        for item in result["evidence"]:
            st.write(
                f"研究 {item['research_id']}（修订 {item['research_revision']}），关系 {item['relationship']}：{item['validity_reason']}；去重键 {item['dedupe_key']}"
            )
            st.json(localize(item))
            for field, label in (("source_ref", "打开来源"), ("evidence_ref", "打开证据")):
                ref = item[field]
                if (
                    ref is not None
                    and urlsplit(ref).scheme in {"http", "https"}
                    and urlsplit(ref).hostname
                ):
                    st.link_button(
                        f"{label} · {item['research_id']}",
                        ref,
                        key=f"evidence_{item['link_id']}_{field}",
                    )
    with st.expander("经济性与履约输入"):
        st.caption(
            "利润维度按贡献率分档：≥35% 为 100、≥25% 为 80、≥15% 为 60、正数为 30、非正为 0。"
        )
        st.write(
            "履约分项：样品 pass 40 / fail 0，支持代发 20 / 不支持 0，时效不超过 48 小时 20、49–72 小时 10、超过 72 小时 0，退货条款已记录 20；未知或报价无效为 null。"
        )
        st.json(result["component_points"]["fulfillment"])
        st.write(
            f"报价时间有效：{result['quote_valid']}；费用假设时间有效：{result['assumptions_valid']}"
        )
        st.json(
            json.dumps(
                localize({"经济性": result["economics"], "履约": result["fulfillment_inputs"]}),
                ensure_ascii=False,
                default=str,
            )
        )
    with st.expander("核验记录和 10 个证据槽"):
        st.caption("核验 pass 获得该项满分，fail 得 0；未知或过期为 null。")
        st.json(result["component_points"]["checks"])
        st.json(localize(result["checks"]))
        for key, filled in result["slots"].items():
            if key.startswith("demand_"):
                reason = f"有效独立支持来源 {len(result['support_keys'])} 个"
            elif key == "economics":
                missing = result["economics"]["missing"]
                reason = f"未知费用 {', '.join(missing) if missing else '无'}；报价有效 {result['quote_valid']}；假设有效 {result['assumptions_valid']}"
            elif key == "fulfillment":
                inputs = result["fulfillment_inputs"]
                gaps = []
                if not result["quote_valid"]:
                    gaps.append("报价过期或晚于报告时间")
                if inputs["sample_result"] == "unknown":
                    gaps.append("样品结果未知")
                if inputs["sample_evidence_ref"] is None:
                    gaps.append("缺少样品依据")
                if inputs["dropship_supported"] is None:
                    gaps.append("代发未知")
                if inputs["lead_time_hours"] is None:
                    gaps.append("时效未知")
                if not inputs["return_terms"]:
                    gaps.append("退货条款未知")
                reason = "、".join(gaps) if gaps else "样品、代发、时效和退货条款均已记录"
            else:
                reason = result["check_reasons"][key]
            st.write(f"{SLOTS[key]}：{'已满足' if filled else '未满足'}；{reason}")


def render(engine, mode):
    st.header("研究证据与规则评分")
    st.caption(
        "筛选规则不是销量预测；评分与建议均需人工复核。报告使用当前修订快照，不回放历史版本。"
    )
    if mode == "合成演示":
        st.warning("合成数据评分，不能据此采购。请在“编辑与导入”预检并确认加载合成研究示例。")
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        products = {row.product_id: row for row in session.exec(select(ProductCandidate)).all()}
        skus = session.exec(select(Sku).order_by(Sku.sku_id)).all()
        quotes = {row.quote_id: row for row in session.exec(select(SupplierQuote)).all()}
        notes = session.exec(select(ResearchNote)).all()
        links = session.exec(select(ProductResearchLink)).all()
        checks = session.exec(select(CandidateCheck)).all()
        history = session.exec(
            select(RevisionAudit).where(RevisionAudit.entity_type.in_(("quote", "check")))
        ).all()
    if not skus:
        st.info("请先添加候选、报价和 SKU。")
        return
    raw_as_of = report_time_input(
        "报告时间（ISO 8601，必须带时区）",
        f"p2_asof_{mode}",
    )
    try:
        as_of = timestamp(raw_as_of)
    except ValueError as exc:
        st.error(f"报告时间无效：{exc}")
        return
    sku_id = st.selectbox("选择 SKU", [row.sku_id for row in skus], key=f"p2_sku_{mode}")
    sku = next(row for row in skus if row.sku_id == sku_id)
    quote_ids = sorted(q.quote_id for q in quotes.values() if q.product_id == sku.product_id)
    quote_id = st.selectbox(
        "评分使用的报价",
        quote_ids,
        index=quote_ids.index(sku.quote_id),
        key=f"p2_quote_{mode}_{sku_id}",
    )
    demo = mode == "合成演示"
    notes_by_id = {item.research_id: item for item in notes}
    links_by_product = defaultdict(list)
    for link in links:
        links_by_product[link.product_id].append(link)
    checks_by_product = defaultdict(list)
    for check in checks:
        checks_by_product[check.product_id].append(check)
    history_by_record = defaultdict(list)
    for audit in history:
        history_by_record[(audit.entity_type, audit.record_id)].append(audit)

    def evaluate(item, quote):
        product_links = links_by_product[item.product_id]
        product_checks = checks_by_product[item.product_id]
        relevant_history = list(history_by_record[("quote", quote.quote_id)])
        for check in product_checks:
            relevant_history.extend(history_by_record[("check", check.check_id)])
        return score(
            products[item.product_id],
            item,
            quote,
            [notes_by_id[link.research_id] for link in product_links],
            product_links,
            product_checks,
            relevant_history,
            as_of,
            demo=demo,
        )

    try:
        result = evaluate(sku, quotes[quote_id])
    except ValueError as exc:
        st.error(f"当前 SKU 无法评分：{exc}")
    else:
        render_result(sku, result)
    st.subheader("可进入人工评审的排序")
    st.caption("排序使用每个 SKU 已保存的报价；上方选择的其他报价仅用于本次单项评分。")
    reports = []
    for item in skus:
        try:
            reports.append(evaluate(item, quotes[item.quote_id]))
        except ValueError as exc:
            st.warning(f"{item.sku_code} 无法评分：{exc}")
    ready = rank(reports)
    if ready:
        for position, item in enumerate(ready, 1):
            st.write(
                f"{position}. {item['product_id']} / {item['sku_id']} · {item['total']:.2f} 分 · 覆盖 {item['coverage']}%"
            )
    else:
        st.info("当前没有可进入人工评审的候选。")
    for status, label in STATUSES.items():
        if status == "ready_for_manual_review":
            continue
        grouped = [item for item in reports if item["status"] == status]
        if grouped:
            st.write(f"{label}：" + "、".join(item["sku_id"] for item in grouped))
