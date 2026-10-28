from pathlib import Path

from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller.db import make_engine
from xhs_seller.models import ImportBatch, ProductCandidate, RevisionAudit


def test_manual_edit_requires_confirmation_and_audits(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    assert not app.exception
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    assert not app.exception
    app.text_area(key="field_合成演示_product_PC-01_v1_risk_notes").set_value("测试修订")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v1-预检编辑").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 1
        assert not session.exec(select(ImportBatch)).all()
    next(x for x in app.text_input if x.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        assert product.revision == 2 and product.risk_notes == "测试修订"
        assert len(session.exec(select(ImportBatch)).all()) == 1
        assert len(session.exec(select(RevisionAudit)).all()) == 1
    app.radio[0].set_value("修订历史").run()
    assert not app.exception
    assert any("PC-01" in item.value for item in app.markdown)
    app.radio[0].set_value("手工编辑").run()
    assert not any("记录已变化" in item.value for item in app.warning)


def test_manual_edit_preserves_and_edits_multiline_imported_text(monkeypatch, tmp_path):
    import csv
    import io

    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "multiline.db")
    seed(engine)
    sample = Path(__file__).resolve().parents[1] / "examples/p1/product_candidates.csv"
    with sample.open(encoding="utf-8-sig", newline="") as source:
        row = next(csv.DictReader(source))
    row.update(
        revision="2",
        updated_at="2026-10-27T01:00:00+00:00",
        risk_notes="第一行风险\n第二行风险",
    )
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(row))
    writer.writeheader()
    writer.writerow(row)
    preview = preflight(
        engine, [InputFile("product", "multiline.csv", output.getvalue().encode())], real_data=False
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    note_key = "field_合成演示_product_PC-01_v2_risk_notes"
    name_key = "field_合成演示_product_PC-01_v2_product_name"
    assert app.text_area(key=note_key).value == "第一行风险\n第二行风险"
    app.text_area(key=name_key).set_value("修改名称")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v2-预检编辑").click().run()
    assert app.session_state["p1_preview"].changes[0].new["risk_notes"] == "第一行风险\n第二行风险"
    next(button for button in app.button if button.label == "修改并重新预检").click().run()
    assert app.text_area(key=note_key).value == "第一行风险\n第二行风险"
    app.text_area(key=note_key).set_value("第一行风险\n第二行已更新")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v2-预检编辑").click().run()
    next(field for field in app.text_input if field.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        assert product.revision == 3
        assert product.product_name == "修改名称"
        assert product.risk_notes == "第一行风险\n第二行已更新"


def test_a08_quote_comparison_visible(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("SKU 经济性").run()
    assert not app.exception
    assert any("Q-02B" in item.value for item in app.markdown)
    assert any("Q-03B" in item.value for item in app.markdown)
    assert any("场景不匹配" in item.value for item in app.warning)
    assert any("¥8.00" in item.value for item in app.markdown)
    assert any("贡献利润不可完整计算" in item.value for item in app.markdown)


def test_quote_selection_is_saved_with_revision(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.models import Sku

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_kind").set_value("sku").run()
    app.selectbox(key="edit_record_合成演示_sku").set_value("S-01").run()
    app.text_input(key="field_合成演示_sku_S-01_v1_quote_id").set_value("Q-01B")
    app.button(key="FormSubmitter:edit_form_合成演示_sku_S-01_v1-预检编辑").click().run()
    assert not app.exception
    app.text_input[-1].set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        sku = session.get(Sku, "S-01")
        assert sku.revision == 2 and sku.quote_id == "Q-01B"
        audit = session.exec(select(RevisionAudit)).one()
        assert "Q-01A" in audit.old_json and "Q-01B" in audit.new_json
    app.sidebar.radio[0].set_value("SKU 经济性").run()
    assert not app.exception
    assert any("¥8.00" in item.value for item in app.markdown)


def test_real_space_is_empty_and_distinct(monkeypatch, tmp_path):
    from xhs_seller import db

    demo = make_engine(tmp_path / "demo.db")
    real = make_engine(tmp_path / "real.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert not app.exception
    assert any("0 个候选" in item.value for item in app.markdown)
    with Session(real) as session:
        assert not session.exec(select(ProductCandidate)).all()
    with Session(demo) as session:
        assert len(session.exec(select(ProductCandidate)).all()) == 3


def test_csv_preflight_and_confirmation_in_ui(monkeypatch, tmp_path):
    import io

    import streamlit as st

    from xhs_seller import db

    engine = make_engine(tmp_path / "demo.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    fixture = Path(__file__).resolve().parents[1] / "examples/import_cases/product_revision2.csv"

    class Upload(io.BytesIO):
        name = "product_revision2.csv"

    data = fixture.read_bytes()
    monkeypatch.setattr(
        st, "file_uploader", lambda label, **kwargs: Upload(data) if label == "候选 CSV" else None
    )
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("CSV 导入").run()
    app.button[0].click().run()
    assert not app.exception
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 1
    app.text_input[-1].set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 2
        assert len(session.exec(select(RevisionAudit)).all()) == 1


def test_error_report_warns_for_formula_like_record_id(monkeypatch, tmp_path):
    import io

    import streamlit as st

    from xhs_seller import db

    engine = make_engine(tmp_path / "csv-warning.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    data = (
        b"product_id,revision,updated_at,product_name,category,status,risk_notes\n"
        b"-1-1,1,2026-10-27T08:00:00+08:00,fixture,fixture,invalid,\n"
    )

    class Upload(io.BytesIO):
        name = "product.csv"

    monkeypatch.setattr(
        st, "file_uploader", lambda label, **kwargs: Upload(data) if label == "候选 CSV" else None
    )
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("CSV 导入").run()
    next(button for button in app.button if button.label == "预检所选文件").click().run()
    assert not app.exception
    assert any("错误报告 CSV" in item.value and "记录 ID" in item.value for item in app.warning)
    assert not any(item.label == "确认并原子提交" for item in app.button)


def test_history_paginates_revisions_and_keeps_failed_batches_accessible(monkeypatch, tmp_path):
    import csv
    import io

    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "history.db")
    seed(engine)
    sample = Path(__file__).resolve().parents[1] / "examples/p1/product_candidates.csv"
    with sample.open(encoding="utf-8-sig", newline="") as stream:
        original = next(csv.DictReader(stream))
    rows = [dict(original, product_id=f"P-HIST-{index:03}") for index in range(65)]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(original))
    writer.writeheader()
    writer.writerows(rows)
    preview = preflight(
        engine, [InputFile("product", "batch.csv", output.getvalue().encode())], real_data=False
    )
    assert not preview.errors
    commit(engine, preview, operator="local")
    invalid = dict(original, product_id="P-INVALID", status="invalid")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=list(original))
    writer.writeheader()
    writer.writerow(invalid)
    failed = preflight(
        engine, [InputFile("product", "bad.csv", output.getvalue().encode())], real_data=False
    )
    assert failed.errors
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("修订历史").run()
    assert not app.exception
    assert any("P-HIST-000" in item.value for item in app.markdown)
    assert not any("P-HIST-064" in item.value for item in app.markdown)
    assert len(app.json) == 51
    app.number_input(key=f"p1_history_page_合成演示_{preview.batch_id}").set_value(2).run()
    assert any("P-HIST-064" in item.value for item in app.markdown)
    assert not any("P-HIST-000" in item.value for item in app.markdown)
    app.radio(key="p1_history_kind_合成演示").set_value("失败预检").run()
    assert not app.exception
    assert any("P-INVALID" in item.value for item in app.json)


def test_history_keeps_noop_and_older_successful_batches_accessible(monkeypatch, tmp_path):
    from xhs_seller import db
    from xhs_seller.seed_demo import seed
    from xhs_seller.services.imports import InputFile, commit, preflight

    engine = make_engine(tmp_path / "history-batches.db")
    seed(engine)
    sample = Path(__file__).resolve().parents[1] / "examples/import_cases/product_revision2.csv"
    item = InputFile("product", sample.name, sample.read_bytes())
    changed = preflight(engine, [item], real_data=False)
    assert not changed.errors
    commit(engine, changed, operator="local")
    noop = preflight(engine, [item], real_data=False)
    assert noop.counts["noop"] == 1
    commit(engine, noop, operator="local")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("修订历史").run()
    assert any("没有业务修订" in item.value for item in app.info)
    app.selectbox(key="p1_history_batch_合成演示_成功批次").set_value(changed.batch_id).run()
    assert not app.exception
    assert any("PC-01" in item.value for item in app.markdown)


def test_manual_preview_is_bound_to_selected_record(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "preview-target.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    app.text_area(key="field_合成演示_product_PC-01_v1_risk_notes").set_value("仅适用于 PC-01")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v1-预检编辑").click().run()
    assert any(b.label == "确认并原子提交" for b in app.button)
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-02").run()
    assert not any(b.label == "确认并原子提交" for b in app.button)
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 1
        assert not session.exec(select(ImportBatch)).all()


def test_successful_manual_preview_locks_fields_until_reopened(monkeypatch, tmp_path, caplog):
    from streamlit.elements.lib import policies

    from xhs_seller import db

    monkeypatch.setattr(policies, "_shown_default_value_warning", False)
    caplog.set_level("WARNING", logger="streamlit.elements.lib.policies")
    engine = make_engine(tmp_path / "preview-lock.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    app.text_area(key="field_合成演示_product_PC-01_v1_risk_notes").set_value("预检内容")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v1-预检编辑").click().run()
    assert not any(x.label == "风险备注" for x in app.text_area)
    next(b for b in app.button if b.label == "修改并重新预检").click().run()
    field = app.text_area(key="field_合成演示_product_PC-01_v1_risk_notes")
    assert not field.disabled and field.value == "预检内容"
    assert not any(
        "was created with a default value" in record.message for record in caplog.records
    )
    assert not any(b.label == "确认并原子提交" for b in app.button)
    with Session(engine) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 1
    field.set_value("重新预检的内容")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v1-预检编辑").click().run()
    next(x for x in app.text_input if x.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        assert product.revision == 2 and product.risk_notes == "重新预检的内容"


def test_reopening_new_record_preserves_entered_fields(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "new-record.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    prefix = "field_合成演示_product_新增_"
    app.text_input(key=prefix + "product_id").set_value("PC-NEW")
    app.text_area(key=prefix + "product_name").set_value("新候选")
    app.text_area(key=prefix + "category").set_value("合成类目")
    app.text_input(key=prefix + "status").set_value("researching")
    app.text_area(key=prefix + "risk_notes").set_value("第一行\n第二行")
    app.button(key="FormSubmitter:edit_form_合成演示_product_新增-预检编辑").click().run()
    assert not app.error and not app.exception
    next(button for button in app.button if button.label == "修改并重新预检").click().run()
    assert app.text_input(key=prefix + "product_id").value == "PC-NEW"
    assert app.text_area(key=prefix + "product_name").value == "新候选"
    assert app.text_area(key=prefix + "risk_notes").value == "第一行\n第二行"
    app.text_area(key=prefix + "risk_notes").set_value("第一行\n重新核对")
    app.button(key="FormSubmitter:edit_form_合成演示_product_新增-预检编辑").click().run()
    next(field for field in app.text_input if field.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    assert not app.exception
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-NEW")
        assert product.product_name == "新候选"
        assert product.risk_notes == "第一行\n重新核对"


def test_csv_upload_and_preview_do_not_cross_data_spaces(monkeypatch, tmp_path):
    import io

    import streamlit as st

    from xhs_seller import db

    demo = make_engine(tmp_path / "demo-upload.db")
    real = make_engine(tmp_path / "real-upload.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    data = (
        Path(__file__).resolve().parents[1] / "examples/import_cases/product_revision2.csv"
    ).read_bytes()

    class Upload(io.BytesIO):
        name = "example.csv"

    def simulated_upload(label, *, type, key):
        selected = st.text_input(label, key=key)
        return Upload(data) if label == "候选 CSV" and selected else None

    monkeypatch.setattr(st, "file_uploader", simulated_upload)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("CSV 导入").run()
    next(x for x in app.text_input if x.label == "候选 CSV").set_value("已选择").run()
    next(b for b in app.button if b.label == "预检所选文件").click().run()
    assert any(b.label == "确认并原子提交" for b in app.button)
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert next(x for x in app.text_input if x.label == "候选 CSV").value == ""
    assert next(b for b in app.button if b.label == "预检所选文件").disabled
    assert not any(b.label == "确认并原子提交" for b in app.button)
    app.sidebar.selectbox[0].set_value("合成演示").run()
    assert next(x for x in app.text_input if x.label == "候选 CSV").value == ""
    assert not any(b.label == "确认并原子提交" for b in app.button)
    with Session(demo) as session:
        assert session.get(ProductCandidate, "PC-01").revision == 1
        assert not session.exec(select(ImportBatch)).all()
    with Session(real) as session:
        assert not session.exec(select(ProductCandidate)).all()


def test_sample_preview_is_cleared_when_switching_spaces(monkeypatch, tmp_path):
    from xhs_seller import db

    demo = make_engine(tmp_path / "demo-sample.db")
    real = make_engine(tmp_path / "real-sample.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.radio[0].set_value("CSV 导入").run()
    next(b for b in app.button if b.label == "预检合成研究示例").click().run()
    assert any(b.label == "确认并原子提交" for b in app.button)
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert not any(b.label == "确认并原子提交" for b in app.button)
    app.sidebar.selectbox[0].set_value("合成演示").run()
    assert not any(b.label == "确认并原子提交" for b in app.button)


def test_stale_manual_form_does_not_preview_over_concurrent_revision(monkeypatch, tmp_path):
    from xhs_seller import db

    engine = make_engine(tmp_path / "edit-race.db")
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    app.text_area(key="field_合成演示_product_PC-01_v1_risk_notes").set_value("旧表单输入")
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        product.product_name = "并发修改"
        product.revision = 2
        product.updated_at = "2026-10-27T01:00:00+00:00"
        session.commit()
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v1-预检编辑").click().run()
    assert not any(b.label == "确认并原子提交" for b in app.button)
    assert any("已变化" in warning.value for warning in app.warning)
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        assert product.revision == 2 and product.product_name == "并发修改"
        assert "旧表单输入" != product.risk_notes
    assert app.text_area(key="field_合成演示_product_PC-01_v2_product_name").value == "并发修改"
    assert app.text_area(key="field_合成演示_product_PC-01_v2_risk_notes").value != "旧表单输入"
    app.text_area(key="field_合成演示_product_PC-01_v2_risk_notes").set_value("重新核对后的内容")
    app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v2-预检编辑").click().run()
    assert not app.exception and not app.error
    next(x for x in app.text_input if x.label == "本地操作者标识").set_value("local")
    app.button(key="FormSubmitter:confirm_import-确认并原子提交").click().run()
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        assert product.revision == 3 and product.product_name == "并发修改"
        assert product.risk_notes == "重新核对后的内容"


def test_manual_preflight_rejects_change_between_form_read_and_snapshot(monkeypatch, tmp_path):
    import sqlite3

    from sqlalchemy import event

    from xhs_seller import db

    path = tmp_path / "preflight-race.db"
    engine = make_engine(path)
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app.py", default_timeout=20
    ).run()
    app.sidebar.radio[0].set_value("编辑与导入").run()
    app.selectbox(key="edit_record_合成演示_product").set_value("PC-01").run()
    app.text_input(key="field_合成演示_product_PC-01_v1_revision").set_value("3")
    app.text_area(key="field_合成演示_product_PC-01_v1_risk_notes").set_value("旧内容")
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
    reads = []

    def concurrent_write(conn, cursor, statement, parameters, context, executemany):
        query = statement.lstrip().upper()
        if query.startswith("SELECT PRODUCT_CANDIDATE.") and "WHERE" not in query:
            reads.append(True)
            if len(reads) == 2:
                with sqlite3.connect(path) as connection:
                    connection.execute(
                        "UPDATE product_candidate SET product_name='other', revision=2, "
                        "updated_at='2026-10-27T01:00:00+00:00' WHERE product_id='PC-01'"
                    )

    event.listen(engine, "before_cursor_execute", concurrent_write)
    try:
        app.button(key="FormSubmitter:edit_form_合成演示_product_PC-01_v1-预检编辑").click().run()
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_write)
    assert len(reads) >= 2 and not app.exception
    assert any("已变化" in warning.value for warning in app.warning)
    assert not any(button.label == "确认并原子提交" for button in app.button)
    with Session(engine) as session:
        product = session.get(ProductCandidate, "PC-01")
        assert product.revision == 2 and product.product_name == "other"
