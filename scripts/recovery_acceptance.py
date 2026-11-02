"""Disposable synthetic acceptance wrapper; production source stays unchanged."""

import runpy
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import streamlit as st
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from xhs_seller import db, ui_navigation

CHOICES = {
    "下一次页面/预检读取失败": "read",
    "下一次保存或决定在提交前失败": "before_commit",
    "下一次保存或决定在提交后刷新失败": "after_commit",
    "下一次初始化读取失败": "initialization",
}


class Faults:
    def __init__(self):
        self.lock = threading.RLock()
        self.arm(None)

    def arm(self, phase):
        with self.lock:
            self.phase = phase
            self.written = self.committed = False

    def before(self, conn, cursor, statement, parameters, context, many):
        sql = statement.lstrip().upper()
        with self.lock:
            reading = sql.startswith("SELECT")
            collection = reading and "WHERE" not in sql and "SQLITE_" not in sql
            initialization = sql == "BEGIN IMMEDIATE"
            fire = (
                (self.phase == "read" and collection)
                or (self.phase == "initialization" and initialization)
                or (self.phase == "after_commit" and self.committed and reading)
            )
            if fire:
                self.arm(None)
                raise OperationalError(statement, parameters, Exception("SYNTHETIC_QA_FAILURE"))

    def after(self, conn, cursor, statement, parameters, context, many):
        sql = statement.lstrip().upper()
        if not sql.startswith(("INSERT INTO DRAFT_AUDIT", "INSERT INTO PROPOSAL_AUDIT")):
            return
        with self.lock:
            if self.phase == "before_commit":
                self.arm(None)
                raise OperationalError(statement, parameters, Exception("SYNTHETIC_QA_FAILURE"))
            if self.phase == "after_commit":
                self.written = True

    def commit(self, conn):
        with self.lock:
            if self.phase == "after_commit" and self.written:
                self.committed = True


@st.cache_resource
def resources(root_path):
    runs = Path(root_path) / "user_data" / "recovery-acceptance-runs"
    runs.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="synthetic-", dir=runs))
    demo = db.make_engine(folder / "demo.sqlite3.db")
    real = db.make_engine(folder / "empty.sqlite3.db")
    faults = Faults()
    for engine in (demo, real):
        event.listen(engine, "before_cursor_execute", faults.before)
        event.listen(engine, "after_cursor_execute", faults.after)
        event.listen(engine, "commit", faults.commit)
    return folder, demo, real, faults


def main():
    if not (ROOT / "src/xhs_seller").is_dir() or not (ROOT / "app.py").is_file():
        st.error("验收入口缺少项目源码，请先复制完整源码包，再按验收清单启动。")
        st.stop()
    folder, demo, real, faults = resources(str(ROOT))
    original_navigation = ui_navigation.render_navigation

    def navigation():
        # The application clears action state before navigation on space/recovery
        # changes. Create these QA controls afterwards so they remain usable.
        st.sidebar.subheader("合成验收控制")
        st.sidebar.caption("仅控制本验收服务；不使用项目现有业务库。每次注入只触发一次。")
        st.sidebar.caption("本验收服务的标签共享故障控制和合成库，请只使用一个验收标签。")
        st.sidebar.caption(f"验收临时目录：{folder}")
        selected = st.sidebar.selectbox("验收故障", list(CHOICES), key="manual_qa_fault")
        if st.sidebar.button("注入一次故障", key="manual_qa_arm"):
            faults.arm(CHOICES[selected])
        if st.sidebar.button("取消未触发的注入", key="manual_qa_disarm"):
            faults.arm(None)
        st.sidebar.divider()
        return original_navigation()

    with (
        patch.object(db, "make_engine", lambda path=None: demo if path is None else real),
        patch.object(ui_navigation, "render_navigation", navigation),
    ):
        runpy.run_path(str(ROOT / "app.py"), run_name="__main__")


main()
