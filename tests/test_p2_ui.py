from pathlib import Path

import pytest
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import CandidateCheck, ProductResearchLink, ResearchNote


def test_demo_import_confirmation_and_scoring_ui(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("CSV 导入").run()
    app.button[1].click().run()
    assert not app.exception
    with Session(engine) as session:
        assert not session.exec(select(ResearchNote)).all()
    app.text_input[-1].set_value("operator-1")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert len(session.exec(select(ResearchNote)).all()) == 7
        assert len(session.exec(select(ProductResearchLink)).all()) == 7
        assert len(session.exec(select(CandidateCheck)).all()) == 15
    app.sidebar.radio[0].set_value("研究与评分").run()
    assert not app.exception
    app.text_input(key="p2_asof_合成演示").set_value("2026-10-27T12:00:00+08:00").run()
    assert not app.exception
    assert any("95.00" in item.value for item in app.markdown)
    assert any("100%" in item.value for item in app.markdown)
    assert any("可进入人工评审" in item.value for item in app.subheader)
    assert any("合成数据评分" in item.value for item in app.warning)


def test_edit_research_check_uses_same_revision_audit(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.models import RevisionAudit
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "demo.db")
    seed(engine)
    directory = Path(__file__).resolve().parents[1] / "examples" / "p2"
    inputs = [
        InputFile(kind, name, (directory / name).read_bytes())
        for kind, name in (
            ("research", "research_notes.csv"),
            ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )
    ]
    commit(engine, preflight(engine, inputs, real_data=False), operator="test")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_kind").set_value("check").run()
    app.selectbox(key="edit_record_合成演示_check").set_value("C-02-media_rights").run()
    app.text_input(key="field_合成演示_check_C-02-media_rights_v1_result").set_value("pass")
    app.text_input(key="field_合成演示_check_C-02-media_rights_v1_checked_at").set_value(
        "2026-10-27T11:00:00+08:00"
    )
    app.text_area(key="field_合成演示_check_C-02-media_rights_v1_evidence_ref").set_value(
        "fixture://new-media"
    )
    app.button(
        key="FormSubmitter:edit_form_合成演示_check_C-02-media_rights_v1-预检编辑"
    ).click().run()
    assert not app.exception
    with Session(engine) as session:
        assert session.get(CandidateCheck, "C-02-media_rights").result == "unknown"
    app.text_input[-1].set_value("test")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        check = session.get(CandidateCheck, "C-02-media_rights")
        assert check.result == "pass" and check.revision == 2
        revisions = session.exec(
            select(RevisionAudit).where(RevisionAudit.record_id == check.check_id)
        ).all()
        assert len(revisions) == 2 and any(
            item.old_revision == 1 and item.new_revision == 2 for item in revisions
        )


def test_scoring_page_reads_one_snapshot_during_concurrent_commit(monkeypatch, tmp_path):
    import sqlite3

    from sqlalchemy import event

    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    path = tmp_path / "snapshot.db"
    engine = make_engine(path)
    seed(engine)
    directory = Path(__file__).resolve().parents[1] / "examples" / "p2"
    files = [
        InputFile(kind, name, (directory / name).read_bytes())
        for kind, name in (
            ("research", "research_notes.csv"),
            ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )
    ]
    commit(engine, preflight(engine, files, real_data=False), operator="test")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    writes = []

    @event.listens_for(engine, "before_cursor_execute")
    def concurrent_commit(conn, cursor, statement, parameters, context, executemany):
        if not writes and statement.lstrip().upper().startswith("SELECT CANDIDATE_CHECK."):
            writes.append(True)
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE supplier_quote SET sample_result='fail', revision=2 WHERE quote_id='Q-01A'"
                )
                connection.execute(
                    "UPDATE candidate_check SET result='fail', revision=2 WHERE check_id='C-01-media_rights'"
                )

    try:
        app.sidebar.radio[0].set_value("研究与评分").run()
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_commit)
    assert writes and not app.exception
    assert any("95.00" in item.value for item in app.markdown)
    assert any("可进入人工评审" in item.value for item in app.subheader)
    with Session(engine) as session:
        from xhs_seller.models import SupplierQuote

        assert session.get(SupplierQuote, "Q-01A").sample_result == "fail"
        assert session.get(CandidateCheck, "C-01-media_rights").result == "fail"
    app.run()
    assert not app.exception
    assert any("阻断" in item.value for item in app.subheader)


def test_archived_candidate_is_visible_but_not_ranked(monkeypatch, tmp_path):
    import csv
    import io

    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "archive.db")
    seed(engine)
    examples = Path(__file__).resolve().parents[1] / "examples"
    inputs = [
        InputFile(kind, name, (examples / "p2" / name).read_bytes())
        for kind, name in (
            ("research", "research_notes.csv"),
            ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )
    ]
    commit(engine, preflight(engine, inputs, real_data=False), operator="test")
    with (examples / "p1" / "product_candidates.csv").open(encoding="utf-8", newline="") as source:
        row = next(csv.DictReader(source))
    row.update(revision="2", updated_at="2026-10-27T09:00:00+08:00", status="archived")
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    preview = preflight(
        engine, [InputFile("product", "archive.csv", buf.getvalue().encode())], real_data=False
    )
    assert not preview.errors
    commit(engine, preview, operator="test")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("研究与评分").run()
    assert not app.exception
    assert any("已归档或拒绝" in item.value for item in app.subheader)
    assert any("候选已归档" in item.value for item in app.warning)
    assert any("95.00" in item.value and "总分" in item.value for item in app.markdown)
    assert not any("PC-01 / S-01" in item.value for item in app.markdown)
    assert any("score-v0.3" in item.value for item in app.markdown)


def test_unscorable_selected_sku_does_not_hide_other_candidates(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.models import Sku
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "ranking.db")
    seed(engine)
    directory = Path(__file__).resolve().parents[1] / "examples" / "p2"
    inputs = [
        InputFile(kind, name, (directory / name).read_bytes())
        for kind, name in (
            ("research", "research_notes.csv"),
            ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )
    ]
    commit(engine, preflight(engine, inputs, real_data=False), operator="test")
    with Session(engine) as session:
        sku = session.get(Sku, "S-02")
        sku.updated_at = "2026-10-28T00:00:00+00:00"
        session.commit()
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("研究与评分").run()
    app.text_input(key="p2_asof_合成演示").set_value("2026-10-27T12:00:00+08:00").run()
    app.selectbox(key="p2_sku_合成演示").set_value("S-02").run()
    assert not app.exception
    assert any("晚于报告时间" in item.value for item in app.error)
    assert any("可进入人工评审的排序" in item.value for item in app.subheader)
    assert any("PC-01 / S-01" in item.value and "95.00" in item.value for item in app.markdown)
    assert any("SKU-02" in item.value and "无法评分" in item.value for item in app.warning)


@pytest.mark.parametrize("kind", ["quote", "check"])
def test_scoring_page_loads_relevant_history_and_preserves_failure_gate(
    monkeypatch, tmp_path, kind
):
    import csv
    import io

    from xhs_seller import db, ui_p2
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "history.db")
    seed(engine)
    root = Path(__file__).resolve().parents[1] / "examples"
    inputs = [
        InputFile(item, name, (root / "p2" / name).read_bytes())
        for item, name in (
            ("research", "research_notes.csv"),
            ("link", "product_research_links.csv"),
            ("check", "candidate_checks.csv"),
        )
    ]
    commit(engine, preflight(engine, inputs, real_data=False), operator="local")
    source = root / ("p1/supplier_quotes.csv" if kind == "quote" else "p2/candidate_checks.csv")
    id_field = "quote_id" if kind == "quote" else "check_id"
    target = "Q-01A" if kind == "quote" else "C-01-sales_eligibility"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        original = next(row for row in csv.DictReader(stream) if row[id_field] == target)
    for revision, state in ((2, "fail"), (3, "unknown")):
        row = dict(
            original, revision=str(revision), updated_at=f"2026-10-27T08:0{revision}:00+08:00"
        )
        if kind == "quote":
            row.update(
                sample_result=state,
                sample_evidence_ref="fixture://failure" if state == "fail" else "",
            )
        else:
            row.update(result=state, evidence_ref="fixture://failure" if state == "fail" else "")
        buf = io.StringIO(newline="")
        writer = csv.DictWriter(buf, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
        preview = preflight(
            engine, [InputFile(kind, "update.csv", buf.getvalue().encode())], real_data=False
        )
        assert not preview.errors
        commit(engine, preview, operator="local")
    observed = []
    original_score = ui_p2.score
    from xhs_seller.models import RevisionAudit

    with Session(engine) as session:
        all_notes = session.exec(select(ResearchNote)).all()
        all_links = session.exec(select(ProductResearchLink)).all()
        all_checks = session.exec(select(CandidateCheck)).all()
        all_history = session.exec(select(RevisionAudit)).all()

    def inspect_score(product, sku, quote, notes, links, checks, history, as_of, *, demo):
        observed.append({item.entity_type for item in history})
        assert all(link.product_id == product.product_id for link in links)
        assert {note.research_id for note in notes} == {link.research_id for link in links}
        assert all(check.product_id == product.product_id for check in checks)
        relevant_ids = {("quote", quote.quote_id)} | {("check", item.check_id) for item in checks}
        assert all((item.entity_type, item.record_id) in relevant_ids for item in history)
        try:
            actual = original_score(
                product, sku, quote, notes, links, checks, history, as_of, demo=demo
            )
        except ValueError as exc:
            with pytest.raises(ValueError) as reference_error:
                original_score(
                    product,
                    sku,
                    quote,
                    all_notes,
                    all_links,
                    all_checks,
                    all_history,
                    as_of,
                    demo=demo,
                )
            assert str(reference_error.value) == str(exc)
            raise
        expected = original_score(
            product, sku, quote, all_notes, all_links, all_checks, all_history, as_of, demo=demo
        )
        assert actual == expected
        return actual

    monkeypatch.setattr(db, "make_engine", lambda: engine)
    monkeypatch.setattr(ui_p2, "score", inspect_score)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=30
    ).run()
    app.sidebar.radio[0].set_value("研究与评分").run()
    app.text_input(key="p2_asof_合成演示").set_value("2026-10-27T12:00:00+08:00").run()
    assert not app.exception
    assert any("阻断" in item.value for item in app.subheader)
    assert observed and all(types <= {"quote", "check"} for types in observed)
    app.selectbox(key="p2_quote_合成演示_S-01").set_value("Q-01B").run()
    assert not app.exception
