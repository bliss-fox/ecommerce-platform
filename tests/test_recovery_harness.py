"""A44: the reproducible human acceptance entry is shipped and stays isolated."""

from pathlib import Path

import pytest
from navigation_helpers import navigate
from sqlmodel import Session, select
from streamlit.testing.v1 import AppTest

from xhs_seller import db
from xhs_seller.models import ActionProposal, DraftAudit, DraftFact, ProposalAudit, Sku

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/recovery_acceptance.py"


def item(app, kind, label):
    return next(element for element in getattr(app, kind) if element.label == label)


def test_a44_recovery_acceptance_materials_are_shipped():
    assert SCRIPT.is_file()
    assert (ROOT / "docs/RECOVERY_ACCEPTANCE.md").is_file()
    listed = (ROOT / "scripts/offline-files.txt").read_text(encoding="utf-8").splitlines()
    assert "scripts/recovery_acceptance.py" in listed
    assert "docs/RECOVERY_ACCEPTANCE.md" in listed
    assert "tests/test_recovery_harness.py" in listed


@pytest.fixture
def harness(monkeypatch, tmp_path):
    assert SCRIPT.is_file(), "The acceptance entry must be shipped"
    # Keep generated QA artifacts inside this test's temporary directory. Any
    # attempt to open the project's normal databases fails before opening them.
    import tempfile

    original_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(
        tempfile, "mkdtemp", lambda *args, **kwargs: original_mkdtemp(dir=tmp_path)
    )
    original_engine = db.make_engine
    opened = []

    def isolated_engine(path=None):
        assert path is not None and Path(path).is_relative_to(tmp_path)
        opened.append(Path(path))
        return original_engine(path)

    monkeypatch.setattr(db, "make_engine", isolated_engine)
    from streamlit import cache_resource

    cache_resource.clear()
    app = AppTest.from_file(SCRIPT, default_timeout=30).run()
    assert not app.exception and not app.error
    folder = Path(next(
        caption.value.removeprefix("验收临时目录：") for caption in app.caption
        if caption.value.startswith("验收临时目录：")
    ))
    assert folder.is_relative_to(tmp_path)
    assert opened and all(path.parent == folder for path in opened)
    engine = original_engine(folder / "demo.sqlite3.db")
    try:
        yield app, engine, folder
    finally:
        engine.dispose()
        cache_resource.clear()


def arm(app, choice):
    app.selectbox(key="manual_qa_fault").set_value(choice).run()
    app.button(key="manual_qa_arm").click().run()


def assert_fault(app):
    assert not app.exception
    assert any("数据库操作失败" in error.value for error in app.error)
    assert not app.get("download_button") and not app.text_input and not app.text_area
    assert all("SYNTHETIC_QA_FAILURE" not in error.value for error in app.error)


def test_a44_harness_read_failure_reload_and_one_shot(harness):
    app, _, _ = harness
    navigate(app, "离线草稿")
    app.selectbox(key="p3_sku_合成演示").set_value("S-02").run()
    arm(app, "下一次页面/预检读取失败")
    assert_fault(app)
    app.run()
    assert_fault(app)
    item(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert app.selectbox(key="p3_sku_合成演示").value == "S-02"
    app.run()
    assert not app.error


@pytest.mark.parametrize("phase,expected", [
    ("下一次保存或决定在提交前失败", 0),
    ("下一次保存或决定在提交后刷新失败", 1),
])
def test_a44_harness_real_transaction_and_no_duplicate_on_reload(harness, phase, expected):
    app, engine, _ = harness
    navigate(app, "离线草稿")
    arm(app, phase)
    app.text_input(key="p3_operator_合成演示").set_value("qa_manual")
    item(app, "text_input", "事实文本（单行）").set_value("合成验收事实")
    item(app, "button", "保存事实").click().run()
    assert_fault(app)
    for reload in (False, True):
        if reload:
            item(app, "button", "重新读取页面").click().run()
            assert not app.exception and not app.error
            assert item(app, "text_input", "事实文本（单行）").value == ""
            app.run()
        with Session(engine) as session:
            assert len(session.exec(select(DraftFact)).all()) == expected
            assert len(session.exec(select(DraftAudit)).all()) == expected


def test_a44_harness_terminal_proposal_is_not_repeated(harness):
    app, engine, _ = harness
    navigate(app, "本地提案与复盘")
    item(app, "text_input", "提案 ID（字母、数字、下划线或短横线）").set_value("P-MANUAL-01")
    for label, value in (
        ("变更前快照", "合成前"), ("建议后快照", "合成后"),
        ("建议原因", "合成验收"), ("证据索引（每行一个）", "fixture://manual/qa"),
    ):
        item(app, "text_area", label).set_value(value)
    item(app, "text_input", "创建人本地标识").set_value("qa_manual")
    item(app, "button", "创建待审提案").click().run()
    assert not app.exception and not app.error
    app.radio(key="p5_operation").set_value("审核与待办").run()
    arm(app, "下一次保存或决定在提交后刷新失败")
    item(app, "text_area", "决定依据").set_value("合成人工核对")
    item(app, "text_input", "决定人本地标识").set_value("qa_manual")
    item(app, "button", "记录本地决定").click().run()
    assert_fault(app)
    item(app, "button", "重新读取页面").click().run()
    assert not app.exception and not app.error
    assert not any(button.label == "记录本地决定" for button in app.button)
    assert app.get("download_button")
    with Session(engine) as session:
        assert session.get(ActionProposal, "P-MANUAL-01").status == "approved"
        assert len(session.exec(select(ProposalAudit)).all()) == 2


def test_a44_harness_preflight_error_requires_new_preview(harness):
    app, _, _ = harness
    navigate(app, "数据导入")
    item(app, "button", "预检合成研究示例").click().run()
    assert any(button.label == "确认并原子提交" for button in app.button)
    arm(app, "下一次页面/预检读取失败")
    item(app, "button", "预检合成研究示例").click().run()
    assert not app.exception and any("预检失败" in error.value for error in app.error)
    assert not any(button.label == "确认并原子提交" for button in app.button)
    item(app, "button", "预检合成研究示例").click().run()
    assert not app.exception and not app.error


def test_a44_harness_initialization_backup_empty_space_and_disarm(harness):
    app, _, folder = harness
    arm(app, "下一次初始化读取失败")
    assert_fault(app)
    navigate(app, "数据备份")
    item(app, "button", "生成本地备份").click().run()
    assert not app.exception and app.get("download_button")
    app.sidebar.selectbox(key="data_space").set_value("真实数据").run()
    assert not app.exception and not app.get("download_button")
    navigate(app, "离线草稿")
    assert not app.exception and not app.error
    with Session(db.make_engine(folder / "empty.sqlite3.db")) as session:
        assert not session.exec(select(Sku)).all()
    navigate(app, "数据导入")
    arm(app, "下一次页面/预检读取失败")
    app.button(key="manual_qa_disarm").click().run()
    navigate(app, "候选与报价")
    assert not app.exception and not app.error


def test_a44_normal_app_does_not_expose_acceptance_controls(harness, monkeypatch):
    _, engine, _ = harness
    monkeypatch.setattr(db, "make_engine", lambda path=None: engine)
    app = AppTest.from_file(ROOT / "app.py", default_timeout=30).run()
    assert not app.exception and not app.error
    assert not any(button.label == "注入一次故障" for button in app.button)
    assert not any(box.label == "验收故障" for box in app.selectbox)
