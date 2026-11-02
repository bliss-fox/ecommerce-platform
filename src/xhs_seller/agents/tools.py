"""Capabilities expose only deterministic products of scoped, copied records."""

import json
from dataclasses import asdict, dataclass

from xhs_seller.agents.snapshot import MODELS, canonical, references
from xhs_seller.services.drafts import preview_draft
from xhs_seller.services.economics import calculate
from xhs_seller.services.scoring import score

SPECIALISTS = ("selection", "cost", "content")
TOOLS = {"selection": "score", "cost": "economics", "content": "preview"}
COLLECTIONS = {
    "selection": ("sku", "product", "quote", "check", "link", "research", "audit"),
    "cost": ("sku", "quote"),
    "content": ("fact", "asset"),
}


@dataclass(frozen=True)
class RoleResult:
    role: str
    payload: str
    refs: tuple[str, ...]


class RoleTools:
    """No engine, paths-as-capabilities, arbitrary dispatch, or mutable shared inputs.

    Trusted Python role functions are not a sandbox for arbitrary Python plugins.
    M1 accepts no user-defined code; input strings never enter an evaluator.
    """

    def __init__(self, snapshot, role, on_call=None):
        if role not in (*SPECIALISTS, "review", "summary"):
            raise PermissionError("角色无效")
        self.role = role
        self.sku_ids = snapshot.sku_ids
        self.as_of = snapshot.as_of
        self.demo = snapshot.space.startswith("demo:")
        data = snapshot.rows()
        allowed = {k: data[k] for k in COLLECTIONS.get(role, ())}
        self._data = canonical(allowed)
        self.refs = references(allowed)
        if role == "content":
            self.refs = tuple(sorted((*self.refs, *references({"sku": data["sku"]}))))
        self._on_call = on_call

    def call(self, tool):
        if TOOLS.get(self.role) != tool:
            raise PermissionError("TOOL_DENIED")
        if self._on_call:
            self._on_call(tool)
        data = json.loads(self._data)
        objects = {kind: [MODELS[kind][0](**row) for row in rows] for kind, rows in data.items()}
        if tool == "preview":
            return {
                sku_id: preview_draft(
                    [f for f in objects["fact"] if f.sku_id == sku_id],
                    [a for a in objects["asset"] if a.sku_id == sku_id],
                )
                for sku_id in self.sku_ids
            }
        if tool == "score":
            products = {p.product_id: p for p in objects["product"]}
            quotes = {q.quote_id: q for q in objects["quote"]}
            return {
                s.sku_id: score(
                    products[s.product_id],
                    s,
                    quotes[s.quote_id],
                    objects["research"],
                    objects["link"],
                    objects["check"],
                    objects["audit"],
                    self.as_of,
                    demo=self.demo,
                )
                for s in objects["sku"]
            }
        result = {}
        for sku in objects["sku"]:
            comparisons = {}
            for q in objects["quote"]:
                if q.product_id != sku.product_id:
                    continue
                inputs = {
                    "unit_cost": q.unit_cost_cents,
                    "shipping": q.shipping_per_order_cents,
                    "packaging": sku.packaging_per_order_cents,
                    "platform": sku.platform_fee_per_order_cents,
                    "marketing": sku.marketing_per_order_cents,
                    "after_sales": sku.after_sales_loss_per_order_cents,
                }
                scenario = (
                    "mismatch"
                    if (q.min_qty is not None and q.min_qty > 1) or q.dropship_supported is False
                    else "unknown"
                    if q.min_qty is None or q.dropship_supported is None
                    else "matches"
                )
                comparisons[q.quote_id] = {
                    **asdict(calculate(sku.sale_price_cents, inputs)),
                    "basis": "hypothesis",
                    "scenario": scenario,
                    "saved_quote": q.quote_id == sku.quote_id,
                    "sale_price_cents": sku.sale_price_cents,
                    "inputs_cents": inputs,
                    "quote_source_ref": q.source_ref,
                    "assumption_ref": sku.assumption_ref,
                    "assumptions_as_of": sku.assumptions_as_of,
                }
            result[sku.sku_id] = comparisons
        return result


def expected_result(role, tools, inputs):
    if tools.role != role:
        raise PermissionError("ROLE_DENIED")
    if role in SPECIALISTS:
        return RoleResult(role, canonical(tools.call(TOOLS[role])), tools.refs)
    if role == "review":
        missing = sorted(set(SPECIALISTS) - inputs.keys())
        selection = json.loads(inputs["selection"].payload) if "selection" in inputs else {}
        payload = {
            "missing_roles": missing,
            "assessment": "partial" if missing else "complete",
            "business": {
                sku: {k: report[k] for k in ("status", "blocked", "missing", "contradiction_keys")}
                for sku, report in selection.items()
            },
            "note": "运行完成不等于可销售；假设金额不是实际利润，内容仍需人工核实。",
            "next_steps": [
                "按缺失项补录证据或费用假设，再重新评估。",
                "内容预览未保存；在离线草稿页人工核对。",
            ],
            "hypotheses": json.loads(inputs["cost"].payload) if "cost" in inputs else None,
            "content_ready": {
                sku_id: {
                    "verified_facts": sum(f["status"] == "verified" for f in preview["facts"]),
                    "needs_verification": preview["needs_verification"],
                }
                for sku_id, preview in (
                    json.loads(inputs["content"].payload).items() if "content" in inputs else ()
                )
            },
        }
        refs = tuple(sorted({ref for r in inputs.values() for ref in r.refs}))
    elif role == "summary":
        if "review" not in inputs:
            raise ValueError("缺少复核")
        payload = json.loads(inputs["review"].payload)
        refs = inputs["review"].refs
    else:
        raise PermissionError("ROLE_DENIED")
    return RoleResult(role, canonical(payload), refs)


def execute_role(role, tools, inputs):
    return expected_result(role, tools, inputs)


def validate_result(result, tools, inputs):
    """No open-ended assertions in M1: compare against trusted canonical output."""
    if not isinstance(result, RoleResult) or result.role != tools.role:
        raise ValueError("INVALID_OUTPUT")
    expected = expected_result(tools.role, tools, inputs)
    if result != expected:
        raise ValueError("INVALID_OUTPUT")
    return result
