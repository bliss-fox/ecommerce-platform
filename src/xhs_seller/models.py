from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint
from sqlalchemy.orm import registry
from sqlmodel import Field, SQLModel


class ModelBase(SQLModel, registry=registry()):
    pass


class ProductCandidate(ModelBase, table=True):
    __tablename__ = "product_candidate"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("status IN ('researching', 'shortlisted', 'rejected', 'archived')"),
    )
    product_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    product_name: str
    category: str
    status: str
    risk_notes: str | None = None


class SupplierQuote(ModelBase, table=True):
    __tablename__ = "supplier_quote"
    __table_args__ = (
        UniqueConstraint("quote_id", "product_id"),
        CheckConstraint("revision > 0"),
        CheckConstraint("unit_cost_cents IS NULL OR unit_cost_cents >= 0"),
        CheckConstraint("shipping_per_order_cents IS NULL OR shipping_per_order_cents >= 0"),
        CheckConstraint("min_qty IS NULL OR min_qty >= 1"),
        CheckConstraint("lead_time_hours IS NULL OR lead_time_hours >= 0"),
        CheckConstraint("sample_result IN ('unknown', 'pass', 'fail')"),
        CheckConstraint("sample_result = 'unknown' OR sample_evidence_ref IS NOT NULL"),
    )
    quote_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    product_id: str = Field(foreign_key="product_candidate.product_id")
    supplier_name: str
    source_ref: str
    quoted_at: str
    unit_cost_cents: int | None
    shipping_per_order_cents: int | None
    min_qty: int | None
    dropship_supported: bool | None
    lead_time_hours: int | None
    return_terms: str | None
    sample_result: str
    sample_evidence_ref: str | None


class Sku(ModelBase, table=True):
    __tablename__ = "sku"
    __table_args__ = (
        ForeignKeyConstraint(
            ["quote_id", "product_id"], ["supplier_quote.quote_id", "supplier_quote.product_id"]
        ),
        CheckConstraint("revision > 0"),
        CheckConstraint("sale_price_cents > 0"),
        CheckConstraint("packaging_per_order_cents IS NULL OR packaging_per_order_cents >= 0"),
        CheckConstraint(
            "platform_fee_per_order_cents IS NULL OR platform_fee_per_order_cents >= 0"
        ),
        CheckConstraint("marketing_per_order_cents IS NULL OR marketing_per_order_cents >= 0"),
        CheckConstraint(
            "after_sales_loss_per_order_cents IS NULL OR after_sales_loss_per_order_cents >= 0"
        ),
        CheckConstraint("status IN ('draft', 'ready_for_review', 'archived')"),
    )
    sku_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    sku_code: str = Field(unique=True)
    product_id: str = Field(foreign_key="product_candidate.product_id")
    quote_id: str
    title: str
    sale_price_cents: int
    packaging_per_order_cents: int | None
    platform_fee_per_order_cents: int | None
    marketing_per_order_cents: int | None
    after_sales_loss_per_order_cents: int | None
    assumption_ref: str
    assumptions_as_of: str
    status: str


class ImportBatch(ModelBase, table=True):
    __tablename__ = "import_batch"
    batch_id: str = Field(primary_key=True)
    contract_version: str
    files_json: str
    created_at: str
    confirmed_at: str
    operator: str
    counts_json: str


class RevisionAudit(ModelBase, table=True):
    __tablename__ = "revision_audit"
    audit_id: str = Field(primary_key=True)
    batch_id: str = Field(foreign_key="import_batch.batch_id")
    entity_type: str
    record_id: str
    old_revision: int | None
    new_revision: int
    old_json: str | None
    new_json: str


class ImportFailure(ModelBase, table=True):
    __tablename__ = "import_failure"
    batch_id: str = Field(primary_key=True)
    contract_version: str
    files_json: str
    created_at: str
    counts_json: str
    errors_json: str


class ResearchNote(ModelBase, table=True):
    __tablename__ = "research_note"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("evidence_status IN ('unverified', 'verified', 'rejected')"),
        CheckConstraint(
            "permission_basis IN ('manual_owned', 'authorized_export', 'synthetic', 'unknown')"
        ),
        CheckConstraint(
            "evidence_status != 'verified' OR (evidence_ref IS NOT NULL AND permission_basis != 'unknown')"
        ),
    )
    research_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    keyword: str
    source_ref: str
    captured_at: str
    published_at: str | None
    pain_point: str
    evidence_summary: str
    evidence_status: str
    permission_basis: str
    dedupe_key: str
    evidence_ref: str | None


class ProductResearchLink(ModelBase, table=True):
    __tablename__ = "product_research_link"
    __table_args__ = (
        UniqueConstraint("product_id", "research_id"),
        CheckConstraint("revision > 0"),
        CheckConstraint("relationship IN ('supports', 'contradicts', 'context')"),
    )
    link_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    product_id: str = Field(foreign_key="product_candidate.product_id")
    research_id: str = Field(foreign_key="research_note.research_id")
    relationship: str
    active: bool


class CandidateCheck(ModelBase, table=True):
    __tablename__ = "candidate_check"
    __table_args__ = (
        UniqueConstraint("product_id", "check_type"),
        CheckConstraint("revision > 0"),
        CheckConstraint(
            "check_type IN ('visual_demo', 'fact_sheet', 'media_rights', 'sales_eligibility', 'claims_review')"
        ),
        CheckConstraint("result IN ('pass', 'fail', 'unknown')"),
        CheckConstraint(
            "result = 'unknown' OR (checked_at IS NOT NULL AND evidence_ref IS NOT NULL)"
        ),
    )
    check_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    product_id: str = Field(foreign_key="product_candidate.product_id")
    check_type: str
    result: str
    checked_at: str | None
    evidence_ref: str | None
    note: str | None


class DraftFact(ModelBase, table=True):
    __tablename__ = "draft_fact"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("kind IN ('product_fact', 'usage_experience')"),
        CheckConstraint("status IN ('unverified', 'verified')"),
        CheckConstraint(
            "status != 'verified' OR (evidence_ref IS NOT NULL AND checked_at IS NOT NULL)"
        ),
    )
    fact_id: str = Field(primary_key=True)
    sku_id: str = Field(foreign_key="sku.sku_id")
    revision: int
    updated_at: str
    kind: str
    text: str
    status: str
    evidence_ref: str | None
    checked_at: str | None


class DraftAsset(ModelBase, table=True):
    __tablename__ = "draft_asset"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("rights_basis IN ('unknown', 'manual_owned', 'authorized')"),
        CheckConstraint("rights_basis = 'unknown' OR rights_evidence_ref IS NOT NULL"),
    )
    asset_id: str = Field(primary_key=True)
    sku_id: str = Field(foreign_key="sku.sku_id")
    revision: int
    updated_at: str
    local_path: str
    rights_basis: str
    rights_evidence_ref: str | None


class ContentDraft(ModelBase, table=True):
    __tablename__ = "content_draft"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint("status IN ('draft', 'reviewed', 'rejected')"),
    )
    draft_id: str = Field(primary_key=True)
    sku_id: str = Field(foreign_key="sku.sku_id")
    revision: int
    created_at: str
    updated_at: str
    facts_json: str
    assets_json: str
    title: str
    body: str
    shot_list: str
    needs_verification: str
    status: str


class DraftAudit(ModelBase, table=True):
    __tablename__ = "draft_audit"
    audit_id: str = Field(primary_key=True)
    entity_type: str
    record_id: str
    operator: str
    updated_at: str
    old_json: str | None
    new_json: str


class OrderLine(ModelBase, table=True):
    __tablename__ = "order_line"
    __table_args__ = (
        UniqueConstraint("store_id", "order_id", "line_item_id"),
        CheckConstraint("revision > 0 AND quantity > 0 AND returned_units BETWEEN 0 AND quantity"),
        CheckConstraint(
            "paid_amount_cents >= 0 AND refunded_amount_cents BETWEEN 0 AND paid_amount_cents"
        ),
        CheckConstraint("refunded_amount_cents = 0 OR refund_updated_at IS NOT NULL"),
        CheckConstraint("status IN ('paid', 'shipped', 'completed', 'cancelled')"),
    )
    order_line_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    store_id: str
    order_id: str
    line_item_id: str
    sku_id: str = Field(foreign_key="sku.sku_id")
    quantity: int
    paid_amount_cents: int
    refunded_amount_cents: int
    returned_units: int
    status: str
    paid_at: str
    refund_updated_at: str | None


class Expense(ModelBase, table=True):
    __tablename__ = "expense"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint(
            "category IN ('goods', 'outbound_shipping', 'return_shipping', 'packaging', 'platform_fee', 'marketing', 'other', 'overhead')"
        ),
        CheckConstraint("basis IN ('actual', 'estimated', 'unknown')"),
        CheckConstraint("allocation_mode IN ('direct', 'pool', 'unallocated', 'overhead')"),
        CheckConstraint(
            "(basis = 'unknown' AND amount_cents IS NULL AND allocation_mode = 'direct') OR (basis IN ('actual', 'estimated') AND amount_cents IS NOT NULL AND amount_cents >= 0)"
        ),
        CheckConstraint(
            "(allocation_mode = 'direct' AND order_line_id IS NOT NULL) OR (allocation_mode != 'direct' AND order_line_id IS NULL)"
        ),
        CheckConstraint(
            "(category = 'overhead' AND allocation_mode = 'overhead') OR (category != 'overhead' AND allocation_mode != 'overhead')"
        ),
    )
    expense_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    expense_date: str
    category: str
    amount_cents: int | None
    basis: str
    allocation_mode: str
    order_line_id: str | None = Field(foreign_key="order_line.order_line_id")
    source_ref: str = Field(unique=True)
    remark: str | None


class ExpenseAllocation(ModelBase, table=True):
    __tablename__ = "expense_allocation"
    __table_args__ = (
        UniqueConstraint("expense_id", "order_line_id"),
        CheckConstraint("revision > 0 AND amount_cents >= 0"),
    )
    allocation_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    expense_id: str = Field(foreign_key="expense.expense_id")
    order_line_id: str = Field(foreign_key="order_line.order_line_id")
    amount_cents: int


class CostAdjustment(ModelBase, table=True):
    __tablename__ = "cost_adjustment"
    __table_args__ = (
        CheckConstraint("revision > 0 AND basis IN ('actual', 'estimated')"),
        CheckConstraint(
            "(kind = 'correction' AND recovered_units IS NULL) OR (kind = 'recovery' AND amount_cents <= 0 AND recovered_units IS NOT NULL AND recovered_units > 0)"
        ),
    )
    adjustment_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    expense_id: str = Field(foreign_key="expense.expense_id")
    order_line_id: str = Field(foreign_key="order_line.order_line_id")
    kind: str
    amount_cents: int
    basis: str
    occurred_at: str
    recovered_units: int | None
    source_ref: str = Field(unique=True)
    reason: str


class SettlementSnapshot(ModelBase, table=True):
    __tablename__ = "settlement_snapshot"
    __table_args__ = (
        CheckConstraint(
            "revision > 0 AND covered_refunded_amount_cents >= 0 AND platform_fee_deducted_cents >= 0 AND other_withheld_cents >= 0"
        ),
    )
    settlement_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    order_line_id: str = Field(foreign_key="order_line.order_line_id", unique=True)
    cutoff_at: str
    covered_refunded_amount_cents: int
    platform_fee_deducted_cents: int
    other_withheld_cents: int
    settlement_adjustments_cents: int
    actual_received_cents: int
    source_ref: str


class ActionProposal(ModelBase, table=True):
    __tablename__ = "action_proposal"
    __table_args__ = (
        CheckConstraint("revision > 0"),
        CheckConstraint(
            "action_type IN ('list', 'reprice', 'purchase', 'refund', 'advertise', 'other')"
        ),
        CheckConstraint("target_type IN ('product', 'sku', 'order')"),
        CheckConstraint("status IN ('proposed', 'approved', 'rejected', 'cancelled', 'expired')"),
        CheckConstraint("estimated_cost_cents IS NULL OR estimated_cost_cents >= 0"),
        CheckConstraint(
            "(status = 'proposed' AND decided_at IS NULL AND decided_by IS NULL) OR (status != 'proposed' AND decided_at IS NOT NULL AND decided_by IS NOT NULL)"
        ),
    )
    proposal_id: str = Field(primary_key=True)
    revision: int
    updated_at: str
    action_type: str
    target_type: str
    target_id: str
    before_snapshot: str
    after_snapshot: str
    reason: str
    evidence_refs_json: str
    estimated_cost_cents: int | None
    status: str
    created_at: str
    decided_at: str | None
    decided_by: str | None


class ProposalAudit(ModelBase, table=True):
    __tablename__ = "proposal_audit"
    audit_id: str = Field(primary_key=True)
    proposal_id: str = Field(foreign_key="action_proposal.proposal_id")
    operator: str
    recorded_at: str
    decision_note: str
    old_json: str | None
    new_json: str
