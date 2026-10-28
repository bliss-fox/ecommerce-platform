import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from xhs_seller.services.backup import BackupInspection, backup_database, inspect_backup


def snapshot_rows(data):
    with closing(sqlite3.connect(":memory:")) as connection:
        connection.deserialize(data)
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        return connection.execute("SELECT value FROM sample").fetchall()


def test_snapshot_contains_committed_wal_data_only(tmp_path):
    path = tmp_path / "data #.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE sample(value TEXT)")
        writer.execute("CREATE TABLE audit(action TEXT)")
        writer.execute("INSERT INTO sample VALUES ('committed')")
        writer.execute("INSERT INTO audit VALUES ('recorded')")
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("UPDATE sample SET value='pending'")
        first = backup_database(path)
        assert snapshot_rows(first) == [("committed",)]
        assert inspect_backup(first) == BackupInspection("ok", "ok")
        backup_file = tmp_path / "download.sqlite3"
        backup_file.write_bytes(first)
        with closing(
            sqlite3.connect(backup_file.resolve().as_uri() + "?mode=ro", uri=True)
        ) as file:
            assert file.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert file.execute("SELECT value FROM sample").fetchall() == [("committed",)]
            assert file.execute("SELECT action FROM audit").fetchall() == [("recorded",)]
        writer.execute("COMMIT")
        assert snapshot_rows(backup_database(path)) == [("pending",)]


def test_missing_database_is_not_created(tmp_path):
    path = tmp_path / "missing.db"
    with pytest.raises(sqlite3.OperationalError):
        backup_database(path)
    assert not path.exists()


def test_inspection_distinguishes_foreign_key_failure_and_unreadable_data(tmp_path):
    path = tmp_path / "bad-reference.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        writer.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        writer.execute("INSERT INTO child VALUES (42)")
        writer.commit()
    data = backup_database(path)
    assert inspect_backup(data) == BackupInspection("ok", "failed")
    assert inspect_backup(b"not a database") == BackupInspection("unavailable", "unavailable")


def test_inspection_reports_schema_mismatch_without_losing_snapshot(tmp_path):
    path = tmp_path / "bad-schema.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("CREATE TABLE parent (id INTEGER)")
        writer.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        writer.commit()
    data = backup_database(path)
    assert inspect_backup(data) == BackupInspection("ok", "unavailable")


def test_inspection_detects_structural_corruption(tmp_path):
    path = tmp_path / "damaged.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("CREATE TABLE sample(value TEXT)")
        writer.commit()
    data = bytearray(backup_database(path))
    # Point the freelist at a nonexistent page while retaining a readable header.
    data[32:36] = (999999).to_bytes(4, "big")
    data[36:40] = (1).to_bytes(4, "big")
    assert inspect_backup(bytes(data)) == BackupInspection("failed", "unavailable")


def test_exclusive_lock_times_out(tmp_path):
    path = tmp_path / "locked.db"
    with closing(sqlite3.connect(path, isolation_level=None)) as writer:
        writer.execute("CREATE TABLE sample(value TEXT)")
        writer.execute("BEGIN EXCLUSIVE")
        with pytest.raises(TimeoutError, match="占用"):
            backup_database(path, lock_timeout=0.2)
        writer.execute("ROLLBACK")


def test_ui_isolates_modes_and_clears_failed_snapshot(monkeypatch, tmp_path):
    import streamlit as st

    from xhs_seller import db, ui_backup

    demo = db.make_engine(tmp_path / "demo.db")
    real = db.make_engine(tmp_path / "real.db")
    monkeypatch.setattr(db, "make_engine", lambda path=None: demo if path is None else real)
    downloads = []
    monkeypatch.setattr(st, "download_button", lambda *args, **kwargs: downloads.append(kwargs))
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.sidebar.radio[0].set_value("数据备份").run()
    assert not app.exception and not downloads
    app.button(key="backup_create_合成演示").click().run()
    assert not app.exception and len(downloads) == 1
    with closing(sqlite3.connect(":memory:")) as snapshot:
        snapshot.deserialize(downloads[-1]["data"])
        assert snapshot.execute("SELECT COUNT(*) FROM product_candidate").fetchone()[0] > 0
    assert downloads[-1]["file_name"].startswith("demo-backup-")
    downloads.clear()
    app.sidebar.selectbox[0].set_value("真实数据").run()
    assert not app.exception and not downloads
    # The backup page does not create a missing database.
    app.button(key="backup_create_真实数据").click().run()
    assert app.error and not downloads
    db.init_db(real)
    app.button(key="backup_create_真实数据").click().run()
    assert not app.exception and len(downloads) == 1
    assert downloads[-1]["file_name"].startswith("local-backup-")
    with closing(sqlite3.connect(":memory:")) as snapshot:
        snapshot.deserialize(downloads[-1]["data"])
        assert snapshot.execute("SELECT COUNT(*) FROM product_candidate").fetchone() == (0,)
    downloads.clear()
    monkeypatch.setattr(
        ui_backup, "backup_database", lambda path: (_ for _ in ()).throw(sqlite3.OperationalError())
    )
    app.button(key="backup_create_真实数据").click().run()
    assert app.error and not downloads
    demo.dispose()
    real.dispose()


def test_incompatible_schema_can_still_be_backed_up(monkeypatch, tmp_path):
    from xhs_seller import db

    path = tmp_path / "old.db"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE product_candidate (product_id TEXT)")
        connection.commit()
    engine = db.make_engine(path)
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    assert app.error
    app.sidebar.radio[0].set_value("数据备份").run()
    app.button(key="backup_create_合成演示").click().run()
    assert not app.exception and not app.error
    assert app.get("download_button")
    engine.dispose()


def test_ui_warns_about_foreign_key_failure_but_offers_download(monkeypatch, tmp_path):
    from xhs_seller import db

    path = tmp_path / "demo.db"
    with closing(sqlite3.connect(path)) as writer:
        writer.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        writer.execute("CREATE TABLE child (parent_id INTEGER REFERENCES parent(id))")
        writer.execute("INSERT INTO child VALUES (42)")
        writer.commit()
    engine = db.make_engine(path)
    monkeypatch.setattr(db, "make_engine", lambda: engine)
    app = AppTest.from_file(Path(__file__).resolve().parents[1] / "app.py").run()
    app.sidebar.radio[0].set_value("数据备份").run()
    app.button(key="backup_create_合成演示").click().run()
    assert not app.exception and not app.error
    assert any("外键检查：发现问题" in item.value for item in app.warning)
    assert app.get("download_button")
    engine.dispose()
