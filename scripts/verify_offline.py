"""Reproduce the offline acceptance flow in disposable, synthetic databases only."""

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from decimal import Decimal
from itertools import count
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
AS_OF = datetime.fromisoformat("2026-10-27T12:00:00+08:00")
sys.path.insert(0, str(ROOT / "src"))

from sqlmodel import Session, select

from xhs_seller.agents.report import export_report
from xhs_seller.agents.runtime import Run
from xhs_seller.agents.snapshot import read_snapshot
from xhs_seller.db import init_db, make_engine
from xhs_seller.models import ModelBase, RevisionAudit
from xhs_seller.services.backup import BackupInspection, backup_database, inspect_backup
from xhs_seller.services.drafts import create_draft, render_markdown, save_draft, save_fact
from xhs_seller.services.imports import InputFile, commit, preflight
from xhs_seller.services.p4_report import build_report
from xhs_seller.services.product_queries import compare_quote, read_catalog
from xhs_seller.services.proposals import create_proposal, decide_proposal, export_todo, review
from xhs_seller.services.workbench_queries import order_alerts


def verify():
    checks = []

    def require(condition, label):
        if not condition:
            raise RuntimeError(f"Acceptance check failed: {label}")
        checks.append(label)

    def load(engine, group, names, **confirmations):
        files = [
            InputFile(kind, name, (ROOT / "examples" / group / name).read_bytes())
            for kind, name in names
        ]
        preview = preflight(engine, files, real_data=False, **confirmations)
        require(not preview.errors, f"{group} preflight")
        commit(engine, preview, operator="offline-acceptance")
        return files

    def dump(engine):
        with engine.connect() as connection:
            return {
                name: sorted(map(repr, connection.execute(table.select()).all()))
                for name, table in ModelBase.metadata.tables.items()
            }

    ticks = count()

    def fixture_time():
        return (AS_OF - timedelta(hours=1) + timedelta(seconds=next(ticks))).isoformat()

    # Synthetic time keeps fixture expiry deterministic, even years after delivery.
    # Patches are confined to this standalone verifier, never the running application.
    with (
        tempfile.TemporaryDirectory(prefix="xhs-offline-acceptance-") as directory,
        patch("xhs_seller.services.drafts.now", side_effect=fixture_time),
        patch("xhs_seller.services.proposals.now", side_effect=fixture_time),
        patch("xhs_seller.services.imports.now", side_effect=fixture_time),
    ):
        path = Path(directory) / "synthetic.db"
        engine = make_engine(path)
        try:
            init_db(engine)
            files = load(
                engine,
                "p1",
                [
                    ("product", "product_candidates.csv"),
                    ("quote", "supplier_quotes.csv"),
                    ("sku", "skus.csv"),
                ],
            )
            catalog = read_catalog(engine)
            require(
                (len(catalog.products), len(catalog.quotes), len(catalog.skus)) == (3, 6, 3),
                "P1 catalog",
            )
            with Session(engine) as session:
                audit_count = len(session.exec(select(RevisionAudit)).all())
            renamed = [
                InputFile(f.kind, f"renamed-{i}.csv", f.content)
                for i, f in enumerate(reversed(files))
            ]
            preview = preflight(engine, renamed, real_data=False)
            require(not preview.errors and preview.counts["noop"] == 12, "renamed no-op preflight")
            commit(engine, preview, operator="offline-acceptance")
            with Session(engine) as session:
                require(
                    len(session.exec(select(RevisionAudit)).all()) == audit_count,
                    "no duplicate revision audits",
                )
            economics = compare_quote(catalog.skus["S-01"], catalog.quotes["Q-01A"])
            require(economics.contribution == Decimal("8.40"), "unit contribution 8.40")
            require(economics.break_even_cac == Decimal("11.40"), "break-even CAC 11.40")
            unknown = compare_quote(catalog.skus["S-03"], catalog.quotes["Q-03A"])
            require(
                unknown.contribution is None and unknown.break_even_cac == Decimal("8.60"),
                "unknown marketing remains unknown",
            )
            load(
                engine,
                "p2",
                [
                    ("research", "research_notes.csv"),
                    ("link", "product_research_links.csv"),
                    ("check", "candidate_checks.csv"),
                ],
            )
            fact = save_fact(
                engine,
                sku_id="S-01",
                kind="product_fact",
                fact_text="颜色：蓝色（合成）",
                status="verified",
                evidence_ref="fixture://acceptance/fact",
                checked_at=(AS_OF - timedelta(hours=2)).isoformat(),
                operator="offline-acceptance",
                real_data=False,
            )
            draft = create_draft(engine, sku_id="S-01", operator="offline-acceptance")
            require(draft.body == fact.text, "draft uses verified fact verbatim")
            saved = save_draft(
                engine,
                draft_id=draft.draft_id,
                expected_revision=1,
                title=fact.text,
                body=fact.text,
                shot_list="拍摄整体",
                needs_verification="核实素材权利",
                status="reviewed",
                operator="offline-acceptance",
            )
            require(saved.revision == 2, "local content review")
            require(
                "fixture://acceptance/fact" in render_markdown(engine, draft.draft_id),
                "draft evidence export",
            )
            before = dump(engine)
            run = Run(read_snapshot(engine, "合成演示", ["S-01", "S-03"], AS_OF))
            try:
                deadline = time.monotonic() + 10
                while run.view()["status"] == "running" and time.monotonic() < deadline:
                    time.sleep(0.01)
                result = run.view()
                require(result["status"] == "completed", "independent role workflow")
                selection = json.loads(result["results"]["selection"].payload)
                require(selection["S-01"]["total"] == "95.00", "deterministic score 95.00")
                cost = json.loads(result["results"]["cost"].payload)
                require(
                    Decimal(cost["S-01"]["Q-01A"]["contribution"]) == Decimal("8.40"),
                    "role amount agrees with economics",
                )
                require(cost["S-03"]["Q-03A"]["contribution"] is None, "role preserves unknown")
                require(
                    "离线协作演示" in export_report(engine, "合成演示", run), "role report export"
                )
            finally:
                run.cancel()
            require(dump(engine) == before, "role run and export have no business writes")
            confirmations = {
                "orders_are_mapped": True,
                "costs_exclude_refunds": True,
                "settlements_cover_full_principal": True,
            }
            load(
                engine,
                "p4",
                [
                    ("order", "orders.csv"),
                    ("expense", "expenses.csv"),
                    ("allocation", "expense_allocations.csv"),
                    ("adjustment", "cost_adjustments.csv"),
                    ("settlement", "settlements.csv"),
                ],
                **confirmations,
            )
            as_of = AS_OF.isoformat()
            report = build_report(engine, as_of)
            rows = {r["order_line_id"]: r for r in report["rows"]}
            require(
                rows["OL-03"]["cost"].contribution_cents == -1800, "return recovery counted once"
            )
            require(
                rows["OL-04"]["cost"].contribution_cents is None
                and rows["OL-04"]["settlement"] is None,
                "unknown cost and unrecorded settlement",
            )
            require(
                rows["OL-02"]["settlement"].refund_not_covered_cents == 2000,
                "refund not covered by old settlement",
            )
            require(
                rows["OL-05"]["settlement"].difference_cents == -50, "settlement difference -0.50"
            )
            require(
                report["cost_subtotals"]["actual"]["contribution_cents"] == 9300,
                "actual subtotal excludes unknown",
            )
            alerts = order_alerts(engine, as_of)
            require(
                alerts["unknown"].total == 1 and alerts["settlement"].total == 3,
                "workbench reminders agree with report",
            )
            load(
                engine,
                "import_cases",
                [
                    ("expense", "expense_actual_revision2.csv"),
                    ("settlement", "settlement_after_refund_revision2.csv"),
                ],
                **confirmations,
            )
            as_of = AS_OF.isoformat()
            updated = build_report(engine, as_of)
            require(
                updated["cost_subtotals"]["actual"]["contribution_cents"] == 13450,
                "revised contribution 134.50",
            )
            require(
                updated["settlement_summary"]["actual_cents"] == 24550,
                "cumulative receipts replace old snapshot",
            )
            _, issues, retrospective = review(engine, as_of)
            require(
                any(i["type"] == "settlement_difference" for i in issues) and bool(retrospective),
                "retrospective preserves unresolved difference",
            )
            proposal = create_proposal(
                engine,
                proposal_id="ACCEPTANCE-01",
                action_type="other",
                target_type="order",
                target_id="OL-05",
                before="合成结算差异 -0.50",
                after="人工核对来源",
                reason="离线验收建议",
                evidence_refs=["fixture://acceptance/settlement"],
                estimated_cost="",
                operator="offline-acceptance",
                real_data=False,
            )
            decide_proposal(
                engine,
                proposal_id=proposal.proposal_id,
                expected_revision=1,
                decision="approved",
                note="仅记录人工待办",
                operator="offline-reviewer",
            )
            todo = export_todo(engine, proposal.proposal_id)
            require("未执行" in todo, "approved proposal remains manual todo")
            require(build_report(engine, as_of) == updated, "proposal does not alter economics")
            snapshot = backup_database(path)
            require(inspect_backup(snapshot) == BackupInspection("ok", "ok"), "backup integrity")
            copied = Path(directory) / "backup-copy.db"
            copied.write_bytes(snapshot)
            reopened = make_engine(copied)
            try:
                init_db(reopened)
                require(build_report(reopened, as_of) == updated, "reopened backup report matches")
                require(
                    export_todo(reopened, proposal.proposal_id) == todo,
                    "reopened proposal audits match",
                )
                require(dump(reopened) == dump(engine), "all backup tables match")
            finally:
                reopened.dispose()
        finally:
            engine.dispose()
    return {
        "status": "passed",
        "synthetic": True,
        "checks": len(checks),
        "order_contribution_cents": 13450,
        "recorded_received_cents": 24550,
        "backup_reopened": True,
        "completed_checks": checks,
        "fixture_as_of": AS_OF.isoformat(),
    }


if __name__ == "__main__":
    try:
        print(json.dumps(verify(), ensure_ascii=True, indent=2))
    except Exception as exc:  # noqa: BLE001 - standalone command reports failure without raw inputs
        # This command never reads user data; keep failures concise and avoid raw SQL/input dumps.
        print(json.dumps({"status": "failed", "synthetic": True, "error": type(exc).__name__}))
        if isinstance(exc, RuntimeError):
            print(str(exc), file=sys.stderr)
        sys.exit(1)
