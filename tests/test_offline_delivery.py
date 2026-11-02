"""A42: run the delivered acceptance command with no network and no existing DB writes."""

import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_verifier(root, cwd):
    # The subprocess cannot inherit pytest's monkeypatch; deny its network explicitly.
    bootstrap = (
        "import runpy,socket,sys; "
        "deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); "
        "socket.socket.connect=deny; socket.create_connection=deny; "
        "runpy.run_path(sys.argv[1],run_name='__main__')"
    )
    return subprocess.run(
        [sys.executable, "-c", bootstrap, str(root / "scripts/verify_offline.py")],
        cwd=cwd,
        check=False,
        capture_output=True,
        timeout=30,
    )


def test_a42_command_passes_without_touching_existing_databases(tmp_path):
    def fingerprints():
        return {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ("demo.sqlite3.db", "local.sqlite3.db")
            if (ROOT / name).exists()
        }

    before = fingerprints()
    result = run_verifier(ROOT, tmp_path)
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")
    report = json.loads(result.stdout)
    assert report["status"] == "passed" and report["synthetic"] is True
    assert report["checks"] >= 20
    assert report["order_contribution_cents"] == 13450
    assert report["recorded_received_cents"] == 24550
    assert report["backup_reopened"] is True
    assert fingerprints() == before
    assert not list(tmp_path.iterdir())


def test_a42_missing_fixture_returns_nonzero_instead_of_pass(tmp_path):
    root = tmp_path / "incomplete checkout"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/verify_offline.py", root / "scripts/verify_offline.py")
    shutil.copytree(ROOT / "src", root / "src", ignore=shutil.ignore_patterns("__pycache__"))
    result = run_verifier(root, tmp_path)
    assert result.returncode != 0
    assert json.loads(result.stdout)["status"] == "failed"
    assert b"Traceback" not in result.stderr
