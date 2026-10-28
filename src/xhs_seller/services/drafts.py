"""Offline, evidence-bound drafts and their local revision history."""

import json
import re
from datetime import UTC, datetime
from pathlib import PureWindowsPath
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import ContentDraft, DraftAsset, DraftAudit, DraftFact, Sku
from xhs_seller.services.imports import ID_PATTERN, SENSITIVE_TEXT, dumps, parse_value

PLACEHOLDER = "【标题待填写】"
DEFAULT_SHOTS = "拍摄商品整体与细节，先核实素材权利。\n核对已确认的商品事实后，再制作说明。"
SHOT_TEMPLATES = frozenset(DEFAULT_SHOTS.splitlines()) | {
    "拍摄整体",
    "拍摄商品整体",
    "拍摄商品细节",
    "拍摄商品包装",
}
SHOT_FACT_PREFIX = "拍摄已核实事实："
FIRST_PERSON = re.compile(r"(?:我|本人).{0,12}(?:用|使用|体验|试|购买)|亲测|亲身体验")


class StaleDraftError(ValueError):
    pass


def now():
    return datetime.now(UTC).isoformat()


def validate_revision_time(timestamp, previous):
    if datetime.fromisoformat(timestamp) <= datetime.fromisoformat(previous):
        raise ValueError("当前时间未晚于已有修订时间，请核对系统时钟后重试")
    return timestamp


def validate_operator(value):
    value = value.strip()
    if not ID_PATTERN.fullmatch(value) or SENSITIVE_TEXT.search(value):
        raise ValueError("请填写不含个人信息的本地操作者标识")
    return value


def clean_text(value, *, required=False, single_line=False):
    value = value.strip()
    if required and not value:
        raise ValueError("必填文本不能为空")
    if single_line and ("\n" in value or "\r" in value):
        raise ValueError("事实和标题必须为单行文本")
    if SENSITIVE_TEXT.search(value):
        raise ValueError("文本包含疑似个人信息或凭证")
    return value


def validate_ref(value, *, real_data):
    return parse_value("research", "evidence_ref", clean_text(value, single_line=True), real_data)


def validate_checked_at(value):
    if not value.strip():
        return None
    checked = datetime.fromisoformat(value.strip())
    if checked.utcoffset() is None:
        raise ValueError("核验时间必须带时区")
    if checked > datetime.now(UTC):
        raise ValueError("核验时间不能晚于当前时间")
    return checked.astimezone(UTC).isoformat()


def validate_local_path(value):
    value = clean_text(value, required=True, single_line=True)
    path = PureWindowsPath(value)
    if "://" in value or value.startswith(("\\\\", "//")) or ".." in path.parts:
        raise ValueError("素材索引必须是本地路径，且不能包含上级目录")
    return value


def audit(session, entity_type, obj, old, operator):
    session.add(
        DraftAudit(
            audit_id=str(uuid4()),
            entity_type=entity_type,
            record_id=getattr(
                obj, {"fact": "fact_id", "asset": "asset_id", "draft": "draft_id"}[entity_type]
            ),
            operator=operator,
            updated_at=obj.updated_at,
            old_json=dumps(old) if old is not None else None,
            new_json=dumps(obj.model_dump()),
        )
    )


def existing_record(session, model, record_id, expected_revision, sku_id):
    if record_id is None:
        if session.get(Sku, sku_id) is None:
            raise ValueError("SKU 不存在")
        return None
    obj = session.get(model, record_id)
    if obj is None or obj.sku_id != sku_id:
        raise ValueError("记录不存在或不属于所选 SKU")
    if obj.revision != expected_revision:
        raise StaleDraftError("记录已变化，请刷新后重试")
    return obj


def save_fact(
    engine,
    *,
    sku_id,
    kind,
    fact_text,
    status,
    evidence_ref,
    checked_at,
    operator,
    real_data,
    fact_id=None,
    expected_revision=None,
):
    operator = validate_operator(operator)
    fact_text = clean_text(fact_text, required=True, single_line=True)
    if kind not in {"product_fact", "usage_experience"} or status not in {"verified", "unverified"}:
        raise ValueError("事实类型或核验状态无效")
    if kind != "usage_experience" and FIRST_PERSON.search(fact_text):
        raise ValueError("第一人称或亲测内容须作为使用经历记录")
    evidence = validate_ref(evidence_ref, real_data=real_data)
    checked = validate_checked_at(checked_at)
    if status == "verified" and (evidence is None or checked is None):
        raise ValueError("已核实事实必须填写证据索引和核验时间")
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        obj = existing_record(session, DraftFact, fact_id, expected_revision, sku_id)
        old = obj.model_dump() if obj is not None else None
        if obj is not None and status == "verified":
            audits = session.exec(
                select(DraftAudit).where(
                    DraftAudit.entity_type == "fact", DraftAudit.record_id == obj.fact_id
                )
            ).all()
            verified_history = [
                item
                for audit_record in audits
                if (item := json.loads(audit_record.new_json))["status"] == "verified"
            ]
            if obj.status == "verified":
                verified_history.append(old)
            if verified_history:
                latest = max(
                    verified_history,
                    key=lambda item: (datetime.fromisoformat(item["checked_at"]), item["revision"]),
                )
                checked_time = datetime.fromisoformat(checked)
                latest_time = datetime.fromisoformat(latest["checked_at"])
                if checked_time < latest_time:
                    raise ValueError("核验时间不能早于历史已核实时间")
                changed = (latest["text"], latest["kind"], latest["evidence_ref"]) != (
                    fact_text,
                    kind,
                    evidence,
                )
                if changed and checked_time == latest_time:
                    raise ValueError("修改已核实事实或依据时，须重新核验并填写更晚的核验时间")
        if obj is None:
            obj = DraftFact(
                fact_id=str(uuid4()),
                sku_id=sku_id,
                revision=1,
                updated_at=now(),
                kind=kind,
                text=fact_text,
                status=status,
                evidence_ref=evidence,
                checked_at=checked,
            )
            session.add(obj)
        else:
            obj.revision += 1
            obj.updated_at = validate_revision_time(now(), obj.updated_at)
            obj.kind, obj.text, obj.status = kind, fact_text, status
            obj.evidence_ref, obj.checked_at = evidence, checked
        audit(session, "fact", obj, old, operator)
        session.commit()
        session.refresh(obj)
        return obj


def save_asset(
    engine,
    *,
    sku_id,
    local_path,
    rights_basis,
    rights_evidence_ref,
    operator,
    real_data,
    asset_id=None,
    expected_revision=None,
    rights_confirmed=False,
):
    operator = validate_operator(operator)
    local_path = validate_local_path(local_path)
    if rights_basis not in {"unknown", "manual_owned", "authorized"}:
        raise ValueError("素材权利依据无效")
    evidence = validate_ref(rights_evidence_ref, real_data=real_data)
    if rights_basis != "unknown" and evidence is None:
        raise ValueError("已声明权利依据的素材必须提供依据索引")
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        obj = existing_record(session, DraftAsset, asset_id, expected_revision, sku_id)
        old = obj.model_dump() if obj is not None else None
        changed_rights = obj is None or (
            obj.local_path,
            obj.rights_basis,
            obj.rights_evidence_ref,
        ) != (local_path, rights_basis, evidence)
        if rights_basis != "unknown" and changed_rights and not rights_confirmed:
            raise ValueError("请确认权利依据适用于当前素材路径")
        if obj is None:
            obj = DraftAsset(
                asset_id=str(uuid4()),
                sku_id=sku_id,
                revision=1,
                updated_at=now(),
                local_path=local_path,
                rights_basis=rights_basis,
                rights_evidence_ref=evidence,
            )
            session.add(obj)
        else:
            obj.revision += 1
            obj.updated_at = validate_revision_time(now(), obj.updated_at)
            obj.local_path, obj.rights_basis, obj.rights_evidence_ref = (
                local_path,
                rights_basis,
                evidence,
            )
        audit(session, "asset", obj, old, operator)
        session.commit()
        session.refresh(obj)
        return obj


def create_draft(engine, *, sku_id, operator):
    operator = validate_operator(operator)
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        if session.get(Sku, sku_id) is None:
            raise ValueError("SKU 不存在")
        facts = session.exec(
            select(DraftFact).where(DraftFact.sku_id == sku_id).order_by(DraftFact.fact_id)
        ).all()
        assets = session.exec(
            select(DraftAsset).where(DraftAsset.sku_id == sku_id).order_by(DraftAsset.asset_id)
        ).all()
        snapshot_facts = [fact.model_dump() for fact in facts]
        snapshot_assets = [asset.model_dump() for asset in assets]
        time = now()
        obj = ContentDraft(
            draft_id=str(uuid4()),
            sku_id=sku_id,
            revision=1,
            created_at=time,
            updated_at=time,
            facts_json=dumps(snapshot_facts),
            assets_json=dumps(snapshot_assets),
            title=PLACEHOLDER,
            body="\n".join(dict.fromkeys(fact.text for fact in facts if fact.status == "verified")),
            shot_list=DEFAULT_SHOTS,
            needs_verification="",
            status="draft",
        )
        session.add(obj)
        audit(session, "draft", obj, None, operator)
        session.commit()
        session.refresh(obj)
        return obj


def require_current_sources(session, draft):
    for model, attr, key in (
        (DraftFact, "facts_json", "fact_id"),
        (DraftAsset, "assets_json", "asset_id"),
    ):
        snapshots = {item[key]: item for item in json.loads(getattr(draft, attr))}
        rows = session.exec(select(model).where(model.sku_id == draft.sku_id)).all()
        current = {getattr(item, key): item.model_dump() for item in rows}
        if current != snapshots:
            raise StaleDraftError("草稿引用的事实或素材已变化，请重新生成草稿")


def validate_draft_fields(draft, title, body, shot_list, needs_verification, status):
    title = clean_text(title, required=True, single_line=True)
    body = clean_text(body)
    shot_list = clean_text(shot_list)
    needs_verification = clean_text(needs_verification)
    if status not in {"draft", "reviewed", "rejected"}:
        raise ValueError("草稿状态无效")
    verified = {
        item["text"] for item in json.loads(draft.facts_json) if item["status"] == "verified"
    }
    if title != PLACEHOLDER and title not in verified:
        raise ValueError("标题只能使用占位文字或一条已核实事实")
    lines = [line.strip() for line in body.splitlines() if line.strip()]
    if len(set(lines)) != len(lines) or any(line not in verified for line in lines):
        raise ValueError("正文每行必须与一条已核实事实完全一致，且不能重复")
    shots = [line.strip() for line in shot_list.splitlines() if line.strip()]
    if any(
        line not in SHOT_TEMPLATES
        and not (
            line.startswith(SHOT_FACT_PREFIX) and line.removeprefix(SHOT_FACT_PREFIX) in verified
        )
        for line in shots
    ):
        raise ValueError(
            "拍摄清单须使用固定任务或‘拍摄已核实事实：’加事实原文；其他内容放入待核实清单"
        )
    return title, "\n".join(lines), "\n".join(shots), needs_verification, status


def save_draft(
    engine,
    *,
    draft_id,
    expected_revision,
    title,
    body,
    shot_list,
    needs_verification,
    status,
    operator,
):
    operator = validate_operator(operator)
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        obj = session.get(ContentDraft, draft_id)
        if obj is None:
            raise ValueError("草稿不存在")
        if obj.revision != expected_revision:
            raise StaleDraftError("草稿已变化，请刷新后重试")
        require_current_sources(session, obj)
        title, body, shot_list, needs_verification, status = validate_draft_fields(
            obj, title, body, shot_list, needs_verification, status
        )
        old = obj.model_dump()
        content_changed = (title, body, shot_list, needs_verification) != (
            obj.title,
            obj.body,
            obj.shot_list,
            obj.needs_verification,
        )
        if content_changed and status != "draft":
            audits = session.exec(
                select(DraftAudit).where(
                    DraftAudit.entity_type == "draft", DraftAudit.record_id == obj.draft_id
                )
            ).all()
            previously_reviewed = obj.status == "reviewed" or any(
                json.loads(item.new_json)["status"] == "reviewed" for item in audits
            )
            if previously_reviewed:
                raise ValueError("修改已审核内容须先保存为 draft，再重新审核")
        obj.revision += 1
        obj.updated_at = validate_revision_time(now(), obj.updated_at)
        obj.title, obj.body, obj.shot_list = title, body, shot_list
        obj.needs_verification, obj.status = needs_verification, status
        audit(session, "draft", obj, old, operator)
        session.commit()
        session.refresh(obj)
        return obj


def local_time(value):
    return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).isoformat()


def escape_markdown(value):
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", value)


def render_markdown(engine, draft_id):
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        draft = session.get(ContentDraft, draft_id)
        if draft is None:
            raise ValueError("草稿不存在")
        require_current_sources(session, draft)
        validate_draft_fields(
            draft, draft.title, draft.body, draft.shot_list, draft.needs_verification, draft.status
        )
        facts = json.loads(draft.facts_json)
        assets = json.loads(draft.assets_json)
        verified = {item["text"]: item for item in facts if item["status"] == "verified"}
        parts = [
            "# 离线内容草稿",
            "",
            f"状态：{draft.status}（内部记录，不代表发布）",
            f"SKU：{escape_markdown(draft.sku_id)}；草稿版本：{draft.revision}",
            "",
            "## 标题",
            escape_markdown(draft.title),
            "",
            "## 已核实事实正文",
        ]
        if draft.title != PLACEHOLDER:
            title_fact = verified[draft.title]
            parts.insert(
                7,
                f"依据：{escape_markdown(title_fact['evidence_ref'])}；核验时间：{escape_markdown(local_time(title_fact['checked_at']))}；事实版本：{title_fact['revision']}",
            )
        if draft.body:
            for line in draft.body.splitlines():
                fact = verified[line]
                parts.append(
                    f"- {escape_markdown(line)}  （依据：{escape_markdown(fact['evidence_ref'])}；核验时间：{escape_markdown(local_time(fact['checked_at']))}；事实版本：{fact['revision']}）"
                )
        else:
            parts.append("- 暂无可用的已核实事实。")
        parts.extend(["", "## 拍摄清单"])
        for line in draft.shot_list.splitlines():
            if line.startswith(SHOT_FACT_PREFIX):
                shot_fact = verified[line.removeprefix(SHOT_FACT_PREFIX)]
                parts.append(
                    f"- {escape_markdown(line)} （依据：{escape_markdown(shot_fact['evidence_ref'])}；事实版本：{shot_fact['revision']}）"
                )
            else:
                parts.append(f"- {escape_markdown(line)}")
        parts.extend(["", "## 待核实清单"])
        for fact in facts:
            if fact["status"] == "unverified":
                parts.append(f"- 待核实：{escape_markdown(fact['text'])}")
        if not assets:
            parts.append("- 尚无素材记录；请核实素材及权利。")
        for asset in assets:
            if asset["rights_basis"] == "unknown":
                parts.append(f"- 待核实素材权利：{escape_markdown(asset['local_path'])}")
        parts.extend(
            f"- {escape_markdown(line)}"
            for line in draft.needs_verification.splitlines()
            if line.strip()
        )
        parts.extend(["", "## 素材与权利依据（人工复核）"])
        for asset in assets:
            ref = (
                asset["rights_evidence_ref"]
                if asset["rights_evidence_ref"] is not None
                else "未提供"
            )
            parts.append(
                f"- {escape_markdown(asset['local_path'])}；权利依据：{asset['rights_basis']}；索引：{escape_markdown(ref)}；素材版本：{asset['revision']}"
            )
        if not assets:
            parts.append("- 未记录素材。")
        parts.append("\n本稿仅供人工核对，不代表可发布或已发布。")
        return "\n".join(parts) + "\n"
