"""A41: native Windows lifecycle checks; no production database is opened."""

import concurrent.futures
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher")


@pytest.fixture
def process_inspection():
    probe = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            (
                "Get-NetTCPConnection -State Listen -ErrorAction Stop | Out-Null; "
                'Get-CimInstance Win32_Process -Filter "ProcessId = $PID" -ErrorAction Stop | Out-Null'
            ),
        ],
        capture_output=True,
        check=False,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if probe.returncode:
        pytest.skip("OS process inspection is restricted; native launcher lifecycle is unverified")


@pytest.fixture
def checkout(tmp_path, process_inspection):
    root = tmp_path / "离线交付 with spaces"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/local-app.ps1", root / "scripts/local-app.ps1")
    shutil.copy2(ROOT / "scripts/start-server.py", root / "scripts/start-server.py")
    (root / "app.py").write_text("import streamlit as st\nst.title('offline')\n", encoding="utf-8")
    return root


def available_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def invoke(root, port, *args, python=sys.executable):
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts/local-app.ps1"),
            "-Port",
            str(port),
            "-PythonPath",
            str(python),
            "-NoBrowser",
            *args,
        ],
        cwd=root.parent,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=55,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


def test_a41_cmd_uses_shipped_script():
    for name in ("start.cmd", "stop.cmd"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "scripts\\local-app.ps1" in text
        assert "local_data\\deployment.ps1" not in text
    assert (ROOT / "scripts/local-app.ps1").is_file()


def test_a41_concurrent_start_reuses_listener_and_stop_is_idempotent(checkout):
    port = available_port()
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            starts = list(pool.map(lambda _: invoke(checkout, port), range(2)))
        for result in starts:
            assert result.returncode == 0, result.stdout
            assert b"ready" in result.stdout
        pids = [r.stdout.split(b"PID: ")[1].split()[0] for r in starts]
        assert pids[0] == pids[1]
        again = invoke(checkout, port)
        assert again.returncode == 0 and b"PID: " + pids[0] in again.stdout
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=3) as page:
            assert page.read() == b"ok"
        assert not list(checkout.glob("*.db"))
        assert (checkout / f"local_data/service-{port}.stderr.log").is_file()
        stopped = invoke(checkout, port, "-Stop")
        assert stopped.returncode == 0, stopped.stdout
        with socket.socket() as client:
            assert client.connect_ex(("127.0.0.1", port)) != 0
        stopped = invoke(checkout, port, "-Stop")
        assert stopped.returncode == 0 and b"already stopped" in stopped.stdout
    finally:
        invoke(checkout, port, "-Stop")


def test_a41_foreign_listener_is_never_stopped(checkout):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(5)
        port = listener.getsockname()[1]
        for args in ((), ("-Stop",)):
            result = invoke(checkout, port, *args)
            assert result.returncode != 0
            assert b"another application" in result.stdout
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass


def test_a41_other_checkout_is_not_owned(checkout, tmp_path):
    other = tmp_path / "other"
    shutil.copytree(checkout, other)
    port = available_port()
    try:
        started = invoke(checkout, port)
        assert started.returncode == 0, started.stdout
        for args in ((), ("-Stop",)):
            rejected = invoke(other, port, *args)
            assert rejected.returncode != 0 and b"another application" in rejected.stdout
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=3) as page:
            assert page.read() == b"ok"
    finally:
        invoke(checkout, port, "-Stop")


@pytest.mark.parametrize("missing", ["python", "app"])
def test_a41_missing_files_fail_before_start(checkout, missing):
    python = sys.executable
    if missing == "python":
        python = checkout / "missing-python.exe"
    else:
        (checkout / "app.py").unlink()
    result = invoke(checkout, available_port(), python=python)
    assert result.returncode != 0
    assert b"missing" in result.stdout
