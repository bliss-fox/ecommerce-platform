import sqlite3
from datetime import datetime
from pathlib import Path

import streamlit as st

from xhs_seller.services.backup import backup_database, inspect_backup


def render(engine, mode):
    st.header("数据备份")
    st.caption("备份包含当前数据空间的全部表和审计记录，可能包含私人数据，请妥善保管。")
    st.caption("快照仅包含生成时已提交的数据；修改数据后需重新生成。备份文件由您选择保存位置。")
    if st.session_state.get("backup_mode") != mode:
        st.session_state.pop("backup_snapshot", None)
        st.session_state["backup_mode"] = mode
    if st.button("生成本地备份", key=f"backup_create_{mode}"):
        st.session_state.pop("backup_snapshot", None)
        try:
            data = backup_database(Path(engine.url.database))
        except (sqlite3.Error, TimeoutError) as exc:
            st.error(f"备份失败：{exc}")
        else:
            timestamp = datetime.now().astimezone()
            prefix = "demo" if mode == "合成演示" else "local"
            st.session_state["backup_snapshot"] = (
                data,
                timestamp.strftime("%Y-%m-%d %H:%M:%S %z"),
                f"{prefix}-backup-{timestamp:%Y%m%d-%H%M%S}.sqlite3",
                inspect_backup(data),
            )
    if snapshot := st.session_state.get("backup_snapshot"):
        data, timestamp, filename, inspection = snapshot
        st.write(f"快照生成时间：{timestamp}")
        if inspection.quick_check == inspection.foreign_keys == "ok":
            st.success(
                "SQLite 快速检查和外键检查通过。此结果不代表业务数据已核验或恢复过程已验证。"
            )
        else:
            labels = {"ok": "通过", "failed": "发现问题", "unavailable": "无法检查"}
            st.warning(
                f"SQLite 快速检查：{labels[inspection.quick_check]}；"
                f"外键检查：{labels[inspection.foreign_keys]}。"
                "仍可下载快照用于排查，请保留原数据库。"
            )
        st.download_button(
            "下载 SQLite 备份", data=data, file_name=filename, mime="application/octet-stream"
        )
