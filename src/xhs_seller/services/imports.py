"""CSV preflight and explicit, atomic commit for P1 and P2 entities."""

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from pathlib import PurePath
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

from sqlalchemy import text
from sqlmodel import Session, select

from xhs_seller.models import (
    CandidateCheck,
    CostAdjustment,
    Expense,
    ExpenseAllocation,
    ImportBatch,
    ImportFailure,
    OrderLine,
    ProductCandidate,
    ProductResearchLink,
    ResearchNote,
    RevisionAudit,
    SettlementSnapshot,
    Sku,
    SupplierQuote,
)
from xhs_seller.services.economics import money_to_cents

CONTRACT = "xhs-local-cny-v0.2"
SPECS = {
    "product": (
        ProductCandidate,
        "product_id",
        (
            "product_id",
            "revision",
            "updated_at",
            "product_name",
            "category",
            "status",
            "risk_notes",
        ),
    ),
    "quote": (
        SupplierQuote,
        "quote_id",
        (
            "quote_id",
            "revision",
            "updated_at",
            "product_id",
            "supplier_name",
            "source_ref",
            "quoted_at",
            "unit_cost",
            "shipping_per_order",
            "min_qty",
            "dropship_supported",
            "lead_time_hours",
            "return_terms",
            "sample_result",
            "sample_evidence_ref",
        ),
    ),
    "sku": (
        Sku,
        "sku_id",
        (
            "sku_id",
            "revision",
            "updated_at",
            "sku_code",
            "product_id",
            "quote_id",
            "title",
            "sale_price",
            "packaging_per_order",
            "platform_fee_per_order",
            "marketing_per_order",
            "after_sales_loss_per_order",
            "assumption_ref",
            "assumptions_as_of",
            "status",
        ),
    ),
    "research": (
        ResearchNote,
        "research_id",
        (
            "research_id",
            "revision",
            "updated_at",
            "keyword",
            "source_ref",
            "captured_at",
            "published_at",
            "pain_point",
            "evidence_summary",
            "evidence_status",
            "permission_basis",
            "dedupe_key",
            "evidence_ref",
        ),
    ),
    "link": (
        ProductResearchLink,
        "link_id",
        (
            "link_id",
            "revision",
            "updated_at",
            "product_id",
            "research_id",
            "relationship",
            "active",
        ),
    ),
    "check": (
        CandidateCheck,
        "check_id",
        (
            "check_id",
            "revision",
            "updated_at",
            "product_id",
            "check_type",
            "result",
            "checked_at",
            "evidence_ref",
            "note",
        ),
    ),
}
SPECS.update(
    {
        "order": (
            OrderLine,
            "order_line_id",
            (
                "order_line_id",
                "revision",
                "updated_at",
                "store_id",
                "order_id",
                "line_item_id",
                "sku_id",
                "quantity",
                "paid_amount",
                "refunded_amount",
                "returned_units",
                "status",
                "paid_at",
                "refund_updated_at",
            ),
        ),
        "expense": (
            Expense,
            "expense_id",
            (
                "expense_id",
                "revision",
                "updated_at",
                "expense_date",
                "category",
                "amount",
                "basis",
                "allocation_mode",
                "order_line_id",
                "source_ref",
                "remark",
            ),
        ),
        "allocation": (
            ExpenseAllocation,
            "allocation_id",
            ("allocation_id", "revision", "updated_at", "expense_id", "order_line_id", "amount"),
        ),
        "adjustment": (
            CostAdjustment,
            "adjustment_id",
            (
                "adjustment_id",
                "revision",
                "updated_at",
                "expense_id",
                "order_line_id",
                "kind",
                "amount",
                "basis",
                "occurred_at",
                "recovered_units",
                "source_ref",
                "reason",
            ),
        ),
        "settlement": (
            SettlementSnapshot,
            "settlement_id",
            (
                "settlement_id",
                "revision",
                "updated_at",
                "order_line_id",
                "cutoff_at",
                "covered_refunded_amount",
                "platform_fee_deducted",
                "other_withheld",
                "settlement_adjustments",
                "actual_received",
                "source_ref",
            ),
        ),
    }
)
MONEY_FIELDS = {
    "unit_cost",
    "shipping_per_order",
    "sale_price",
    "packaging_per_order",
    "platform_fee_per_order",
    "marketing_per_order",
    "after_sales_loss_per_order",
    "paid_amount",
    "refunded_amount",
    "amount",
    "covered_refunded_amount",
    "platform_fee_deducted",
    "other_withheld",
    "settlement_adjustments",
    "actual_received",
}
OPTIONAL = {
    "risk_notes",
    "published_at",
    "checked_at",
    "evidence_ref",
    "note",
    "unit_cost",
    "shipping_per_order",
    "min_qty",
    "dropship_supported",
    "lead_time_hours",
    "return_terms",
    "sample_evidence_ref",
    "packaging_per_order",
    "platform_fee_per_order",
    "marketing_per_order",
    "after_sales_loss_per_order",
}
ID_FIELDS = {
    "product_id",
    "quote_id",
    "sku_id",
    "research_id",
    "link_id",
    "check_id",
    "order_line_id",
    "store_id",
    "order_id",
    "line_item_id",
    "expense_id",
    "allocation_id",
    "adjustment_id",
    "settlement_id",
}
TIME_FIELDS = {
    "updated_at",
    "quoted_at",
    "assumptions_as_of",
    "captured_at",
    "published_at",
    "checked_at",
    "paid_at",
    "refund_updated_at",
    "occurred_at",
    "cutoff_at",
}
REF_FIELDS = {"source_ref", "sample_evidence_ref", "assumption_ref", "evidence_ref"}
ENUMS = {
    ("order", "status"): {"paid", "shipped", "completed", "cancelled"},
    ("expense", "category"): {
        "goods",
        "outbound_shipping",
        "return_shipping",
        "packaging",
        "platform_fee",
        "marketing",
        "other",
        "overhead",
    },
    ("expense", "basis"): {"actual", "estimated", "unknown"},
    ("expense", "allocation_mode"): {"direct", "pool", "unallocated", "overhead"},
    ("adjustment", "kind"): {"correction", "recovery"},
    ("adjustment", "basis"): {"actual", "estimated"},
    ("research", "evidence_status"): {"unverified", "verified", "rejected"},
    ("research", "permission_basis"): {"manual_owned", "authorized_export", "synthetic", "unknown"},
    ("link", "relationship"): {"supports", "contradicts", "context"},
    ("check", "check_type"): {
        "visual_demo",
        "fact_sheet",
        "media_rights",
        "sales_eligibility",
        "claims_review",
    },
    ("check", "result"): {"pass", "fail", "unknown"},
    ("product", "status"): {"researching", "shortlisted", "rejected", "archived"},
    ("quote", "sample_result"): {"unknown", "pass", "fail"},
    ("sku", "status"): {"draft", "ready_for_review", "archived"},
}
SECRET_KEY = re.compile(
    r"(?:token|cookie|password|passwd|secret|credential|api[_-]?key|authorization|phone|mobile|email|address|consumer|手机号|电话|地址|身份证|消费者|姓名)",
    re.IGNORECASE,
)
SENSITIVE_TEXT = re.compile(
    r"(?:\b1[3-9]\d{9}\b|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:token|cookie|password|api[_-]?key)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
INT_PATTERN = re.compile(r"[0-9]+\Z")


@dataclass(frozen=True)
class InputFile:
    kind: str
    name: str
    content: bytes


@dataclass(frozen=True)
class Issue:
    file: str
    row_number: int
    record_id: str
    field: str
    error_code: str
    message: str


@dataclass(frozen=True)
class Change:
    kind: str
    record_id: str
    action: str
    old: dict | None
    new: dict
    file: str
    row_number: int


@dataclass
class Preview:
    batch_id: str
    created_at: str
    baseline: str
    database: str
    files: list[dict]
    changes: list[Change] = field(default_factory=list)
    errors: list[Issue] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def counts(self):
        rejected = {(item.file, item.row_number) for item in self.errors}
        counts = {name: 0 for name in ("insert", "update", "noop")}
        for change in self.changes:
            if (change.file, change.row_number) not in rejected:
                counts[change.action] += 1
        return counts | {"rejected": len(rejected)}


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def now():
    return datetime.now(UTC).isoformat()


def snapshot(session):
    return {
        kind: {
            getattr(row, id_field): row.model_dump() for row in session.exec(select(model)).all()
        }
        for kind, (model, id_field, _) in SPECS.items()
    }


def digest(snapshot_data):
    return hashlib.sha256(dumps(snapshot_data).encode("utf-8")).hexdigest()


def safe_filename(name):
    # Filename is metadata, not file contents. Strip any directory component.
    filename = PurePath(name.replace("\\", "/")).name
    if SENSITIVE_TEXT.search(filename) or SECRET_KEY.search(filename):
        return "redacted.csv"
    return filename


def issue(preview, filename, row, record_id, field_name, code, message):
    safe_id = (
        record_id
        if ID_PATTERN.fullmatch(record_id) and not SENSITIVE_TEXT.search(record_id)
        else ""
    )
    preview.errors.append(Issue(filename, row, safe_id, field_name, code, message))


def parse_value(kind, name, value, real_data):
    value = value.strip()
    if not value:
        if name in OPTIONAL or (kind, name) in {
            ("order", "refund_updated_at"),
            ("expense", "amount"),
            ("expense", "order_line_id"),
            ("expense", "remark"),
            ("adjustment", "recovered_units"),
        }:
            return None
        raise ValueError("必填字段为空")
    if SENSITIVE_TEXT.search(value):
        raise PermissionError("疑似隐私信息或凭证")
    if name in ID_FIELDS and not ID_PATTERN.fullmatch(value):
        raise ValueError("ID 格式无效")
    if name in MONEY_FIELDS:
        signed = (kind == "adjustment" and name == "amount") or (
            kind == "settlement" and name in {"settlement_adjustments", "actual_received"}
        )
        result = (
            -money_to_cents(value[1:])
            if signed and value.startswith("-")
            else money_to_cents(value)
        )
        if name == "sale_price" and result == 0:
            raise ValueError("售价必须大于零")
        return result
    if name in TIME_FIELDS:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return parsed.astimezone(UTC).isoformat()
    if name == "expense_date":
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError("日期格式无效")
        return value
    if name in {
        "revision",
        "min_qty",
        "lead_time_hours",
        "quantity",
        "returned_units",
        "recovered_units",
    }:
        if not INT_PATTERN.fullmatch(value):
            raise ValueError("必须为整数")
        result = int(value)
        minimum = 0 if name in {"lead_time_hours", "returned_units"} else 1
        if result < minimum or result > 9223372036854775807:
            raise ValueError("整数超出允许范围")
        return result
    if name in {"dropship_supported", "active"}:
        if value not in {"true", "false"}:
            raise ValueError("只接受 true 或 false")
        return value == "true"
    if (kind, name) in ENUMS and value not in ENUMS[kind, name]:
        raise ValueError("枚举值无效")
    if name in REF_FIELDS:
        parsed = urlsplit(value)
        if (
            parsed.username
            or parsed.password
            or any(
                (
                    SECRET_KEY.search(key)
                    or re.search(
                        r"(?:user|uid|openid|unionid|account|customer|buyer)", key, re.IGNORECASE
                    )
                )
                for key, _ in parse_qsl(parsed.query, keep_blank_values=True)
            )
        ):
            raise PermissionError("引用中包含疑似身份信息或凭证")
        if real_data and parsed.scheme == "fixture":
            raise PermissionError("合成演示引用不能导入真实库")
    return value


def read_file(preview, item, filename, real_data, seen):
    if item.kind not in SPECS:
        issue(preview, filename, 1, "", "", "SCHEMA_ERROR", "不支持的文件类型")
        return []
    expected = SPECS[item.kind][2]
    try:
        content = item.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        issue(preview, filename, 1, "", "", "SCHEMA_ERROR", "文件必须为 UTF-8")
        return []
    reader = csv.reader(io.StringIO(content, newline=""), strict=True)
    rows = []
    previous_line = 0
    try:
        for values in reader:
            rows.append((previous_line + 1, values))
            previous_line = reader.line_num
    except csv.Error:
        issue(preview, filename, previous_line + 1, "", "", "SCHEMA_ERROR", "CSV 格式错误")
        return []
    if not rows:
        issue(preview, filename, 1, "", "", "SCHEMA_ERROR", "文件缺少表头")
        return []
    headers = rows[0][1]
    if any(SECRET_KEY.search(header) for header in headers):
        issue(preview, filename, 1, "", "", "PRIVACY_RISK", "表头包含疑似隐私或凭证字段")
        return []
    if len(set(headers)) != len(headers) or set(headers) != set(expected):
        issue(preview, filename, 1, "", "", "SCHEMA_ERROR", "表头重复、缺失或含未知列")
        return []
    id_field = SPECS[item.kind][1]
    parsed_rows = []
    for row_number, values in rows[1:]:
        if len(values) != len(headers):
            issue(preview, filename, row_number, "", "", "SCHEMA_ERROR", "列数与表头不符")
            continue
        raw = dict(zip(headers, values, strict=True))
        record_id = raw[id_field].strip()
        key = (item.kind, record_id)
        if ID_PATTERN.fullmatch(record_id):
            if key in seen:
                issue(
                    preview,
                    filename,
                    row_number,
                    record_id,
                    id_field,
                    "DUPLICATE_ID_IN_BATCH",
                    "同一批次 ID 重复",
                )
                continue
            seen.add(key)
        parsed = {}
        previous_errors = len(preview.errors)
        for name in expected:
            try:
                value = parse_value(item.kind, name, raw[name], real_data)
            except PermissionError as exc:
                issue(preview, filename, row_number, record_id, name, "PRIVACY_RISK", str(exc))
            except (ValueError, OverflowError):
                issue(
                    preview,
                    filename,
                    row_number,
                    record_id,
                    name,
                    "TYPE_ERROR",
                    "字段类型或取值无效",
                )
            else:
                parsed[name + "_cents" if name in MONEY_FIELDS else name] = value
        if len(preview.errors) != previous_errors:
            continue
        if (
            item.kind == "quote"
            and parsed["sample_result"] != "unknown"
            and not parsed["sample_evidence_ref"]
        ):
            issue(
                preview,
                filename,
                row_number,
                record_id,
                "sample_evidence_ref",
                "TYPE_ERROR",
                "样品结论需要依据",
            )
            continue
        if item.kind == "research":
            if real_data and parsed["permission_basis"] == "synthetic":
                issue(
                    preview,
                    filename,
                    row_number,
                    record_id,
                    "permission_basis",
                    "PRIVACY_RISK",
                    "合成研究仅允许演示库",
                )
                continue
            if parsed["published_at"] and parsed["published_at"] > parsed["captured_at"]:
                issue(
                    preview,
                    filename,
                    row_number,
                    record_id,
                    "published_at",
                    "TIME_ORDER_ERROR",
                    "发布时间晚于采集时间",
                )
                continue
            if parsed["evidence_status"] == "verified" and (
                not parsed["evidence_ref"] or parsed["permission_basis"] == "unknown"
            ):
                issue(
                    preview,
                    filename,
                    row_number,
                    record_id,
                    "evidence_ref",
                    "TYPE_ERROR",
                    "已核验证据必须有依据及许可",
                )
                continue
        if (
            item.kind == "check"
            and parsed["result"] != "unknown"
            and (not parsed["checked_at"] or not parsed["evidence_ref"])
        ):
            issue(
                preview,
                filename,
                row_number,
                record_id,
                "evidence_ref",
                "TYPE_ERROR",
                "核验结论必须有时间和依据",
            )
            continue
        parsed_rows.append((parsed, filename, row_number))
    return parsed_rows


def preflight(
    engine,
    files,
    *,
    real_data,
    contract=CONTRACT,
    orders_are_mapped=False,
    settlements_cover_full_principal=False,
    costs_exclude_refunds=False,
):
    if contract != CONTRACT:
        raise ValueError("不支持的数据契约版本")
    if not files:
        raise ValueError("必须选择至少一个文件")
    with Session(engine) as session:
        session.exec(text("BEGIN"))
        current = snapshot(session)
    labels = [f"{index}. {safe_filename(item.name)}" for index, item in enumerate(files, start=1)]
    preview = Preview(
        str(uuid4()),
        now(),
        digest(current),
        str(engine.url.database),
        [
            {
                "kind": item.kind,
                "name": safe_filename(item.name),
                "label": label,
                "sha256": hashlib.sha256(item.content).hexdigest(),
                **({"order_mapping_confirmed": orders_are_mapped} if item.kind == "order" else {}),
                **(
                    {"full_principal_confirmed": settlements_cover_full_principal}
                    if item.kind == "settlement"
                    else {}
                ),
                **(
                    {"refunds_excluded_confirmed": costs_exclude_refunds}
                    if item.kind in {"expense", "adjustment"}
                    else {}
                ),
            }
            for item, label in zip(files, labels, strict=True)
        ],
    )
    candidate = {kind: dict(rows) for kind, rows in current.items()}
    seen = set()
    for item, label in zip(files, labels, strict=True):
        if item.kind in {"expense", "adjustment"} and not costs_exclude_refunds:
            issue(
                preview,
                label,
                1,
                "",
                "",
                "SCHEMA_ERROR",
                "须人工确认费用和调整不包含退款金额",
            )
            continue
        if item.kind == "order" and not orders_are_mapped:
            issue(
                preview,
                label,
                1,
                "",
                "paid_amount",
                "SCHEMA_ERROR",
                "须人工确认支付金额映射符合契约，不混入补贴、运费收入、税费或跨币种项目",
            )
            continue
        if item.kind == "settlement" and not settlements_cover_full_principal:
            issue(
                preview,
                label,
                1,
                "",
                "",
                "SCHEMA_ERROR",
                "须人工确认结算快照覆盖整条订单行本金；部分本金结算不支持",
            )
            continue
        for row, filename, row_number in read_file(preview, item, label, real_data, seen):
            record_id = row[SPECS[item.kind][1]]
            old = current[item.kind].get(record_id)
            if old is None:
                action = "insert"
            elif row["revision"] < old["revision"]:
                issue(
                    preview,
                    filename,
                    row_number,
                    record_id,
                    "revision",
                    "STALE_REVISION",
                    "修订版本过旧",
                )
                continue
            elif row["revision"] == old["revision"]:
                if digest(row) != digest(old):
                    issue(
                        preview,
                        filename,
                        row_number,
                        record_id,
                        "revision",
                        "REVISION_CONFLICT",
                        "相同修订内容不同",
                    )
                    continue
                action = "noop"
            else:
                if row["updated_at"] <= old["updated_at"]:
                    issue(
                        preview,
                        filename,
                        row_number,
                        record_id,
                        "updated_at",
                        "TIME_ORDER_ERROR",
                        "新修订时间必须更晚",
                    )
                    continue
                immutable = (
                    ("product_id",)
                    if item.kind == "quote"
                    else ("product_id", "sku_code")
                    if item.kind == "sku"
                    else ("product_id", "research_id")
                    if item.kind == "link"
                    else ("product_id", "check_type")
                    if item.kind == "check"
                    else ("store_id", "order_id", "line_item_id", "sku_id")
                    if item.kind == "order"
                    else ("category",)
                    if item.kind == "expense"
                    else ("expense_id", "order_line_id")
                    if item.kind in {"allocation", "adjustment"}
                    else ("order_line_id",)
                    if item.kind == "settlement"
                    else ()
                )
                reassigned = (
                    item.kind == "expense"
                    and old["allocation_mode"] != "unallocated"
                    and (
                        row["allocation_mode"] != old["allocation_mode"]
                        or row["order_line_id"] != old["order_line_id"]
                    )
                )
                if any(row[key] != old[key] for key in immutable) or reassigned:
                    issue(
                        preview,
                        filename,
                        row_number,
                        record_id,
                        "",
                        "BUSINESS_KEY_CONFLICT",
                        "不可修改所属对象或业务键",
                    )
                    continue
                if item.kind == "settlement" and row["cutoff_at"] < old["cutoff_at"]:
                    issue(
                        preview,
                        filename,
                        row_number,
                        record_id,
                        "cutoff_at",
                        "TIME_ORDER_ERROR",
                        "结算截止时间不能倒退",
                    )
                    continue
                if row["revision"] > old["revision"] + 1:
                    preview.warnings.append(f"{filename} 第 {row_number} 行修订号跳跃")
                action = "update"
            candidate[item.kind][record_id] = row
            preview.changes.append(
                Change(item.kind, record_id, action, old, row, filename, row_number)
            )
    changes_by_record = defaultdict(list)
    for change in preview.changes:
        changes_by_record[(change.kind, change.record_id)].append(change)
    sku_codes = {}
    for record_id, sku in candidate["sku"].items():
        if sku["sku_code"] in sku_codes:
            for change in changes_by_record[("sku", record_id)]:
                issue(
                    preview,
                    change.file,
                    change.row_number,
                    record_id,
                    "sku_code",
                    "BUSINESS_KEY_CONFLICT",
                    "SKU 编码已存在",
                )
        sku_codes[sku["sku_code"]] = record_id
    for kind, keys in (
        ("link", ("product_id", "research_id")),
        ("check", ("product_id", "check_type")),
    ):
        seen_keys = {}
        for record_id, row in candidate[kind].items():
            key = tuple(row[name] for name in keys)
            if key in seen_keys:
                for change in changes_by_record[(kind, record_id)]:
                    issue(
                        preview,
                        change.file,
                        change.row_number,
                        record_id,
                        "",
                        "BUSINESS_KEY_CONFLICT",
                        "业务键已存在",
                    )
            seen_keys[key] = record_id
    for change in preview.changes:
        row = change.new
        if change.kind in {"link", "check"} and row["product_id"] not in candidate["product"]:
            issue(
                preview,
                change.file,
                change.row_number,
                change.record_id,
                "product_id",
                "FK_NOT_FOUND",
                "候选不存在",
            )
        if change.kind == "link" and row["research_id"] not in candidate["research"]:
            issue(
                preview,
                change.file,
                change.row_number,
                change.record_id,
                "research_id",
                "FK_NOT_FOUND",
                "研究记录不存在",
            )
        if change.kind == "quote" and row["product_id"] not in candidate["product"]:
            issue(
                preview,
                change.file,
                change.row_number,
                change.record_id,
                "product_id",
                "FK_NOT_FOUND",
                "候选不存在",
            )
        if change.kind == "sku":
            quote = candidate["quote"].get(row["quote_id"])
            if (
                row["product_id"] not in candidate["product"]
                or quote is None
                or quote["product_id"] != row["product_id"]
            ):
                issue(
                    preview,
                    change.file,
                    change.row_number,
                    change.record_id,
                    "quote_id",
                    "FK_NOT_FOUND",
                    "候选或所属报价不存在",
                )
    from xhs_seller.services.p4_validation import validate_p4

    validate_p4(candidate, preview, issue)
    if preview.errors:
        with Session(engine) as session:
            session.add(
                ImportFailure(
                    batch_id=preview.batch_id,
                    contract_version=CONTRACT,
                    files_json=dumps(preview.files),
                    created_at=preview.created_at,
                    counts_json=dumps(preview.counts),
                    errors_json=dumps([asdict(item) for item in preview.errors]),
                )
            )
            session.commit()
    return preview


class BaselineChangedError(RuntimeError):
    pass


def commit(engine, preview, *, operator):
    if str(engine.url.database) != preview.database:
        raise ValueError("预检数据库与提交数据库不一致")
    if preview.errors:
        raise ValueError("存在预检错误，不能提交")
    if not ID_PATTERN.fullmatch(operator.strip()) or SENSITIVE_TEXT.search(operator):
        raise ValueError("必须提供不含隐私信息的本地操作者标识")
    with Session(engine) as session:
        session.exec(text("BEGIN IMMEDIATE"))
        if digest(snapshot(session)) != preview.baseline:
            raise BaselineChangedError("BASELINE_CHANGED：数据已变化，请重新预检")
        session.add(
            ImportBatch(
                batch_id=preview.batch_id,
                contract_version=CONTRACT,
                files_json=dumps(preview.files),
                created_at=preview.created_at,
                confirmed_at=now(),
                operator=operator.strip(),
                counts_json=dumps(preview.counts),
            )
        )
        session.flush()
        for kind in SPECS:
            for change in preview.changes:
                if change.kind != kind or change.action == "noop":
                    continue
                model = SPECS[kind][0]
                if change.action == "insert":
                    session.add(model(**change.new))
                else:
                    obj = session.get(model, change.record_id)
                    for name, value in change.new.items():
                        setattr(obj, name, value)
                session.add(
                    RevisionAudit(
                        audit_id=str(uuid4()),
                        batch_id=preview.batch_id,
                        entity_type=kind,
                        record_id=change.record_id,
                        old_revision=change.old["revision"] if change.old else None,
                        new_revision=change.new["revision"],
                        old_json=dumps(change.old) if change.old else None,
                        new_json=dumps(change.new),
                    )
                )
            session.flush()
        session.commit()
