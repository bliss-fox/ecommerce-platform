"""A45: reproducible, closed-list source packages and independent integrity checks."""

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/package_offline.py"
LISTING = "scripts/offline-files.txt"


def test_a45_builder_is_shipped():
    assert SCRIPT.is_file()
    assert (ROOT / LISTING).is_file()


@pytest.fixture
def packer():
    spec = importlib.util.spec_from_file_location("offline_package", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "合成交付 with spaces"
    files = {
        "app.py": b"print('offline')\n",
        "README.md": "纯合成测试\n".encode(),
        ".env.example": b"# No credentials required\n",
        ".streamlit/config.toml": b"[browser]\ngatherUsageStats=false\n",
        "scripts/package_offline.py": SCRIPT.read_bytes(),
        "examples/p1/product_candidates.csv": b"synthetic_fixture\n",
    }
    files[LISTING] = ("\n".join(sorted([*files, LISTING])) + "\n").encode()
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    for name in (
        ".env", "local.sqlite3.db", "demo.sqlite3.db", "user_data/private.py",
        "local_data/private.log", "src/private.csv", ".git/config", ".venv/secret.txt",
        "examples/p1/private.csv",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic_private_marker", encoding="utf-8")
    return root, files


def test_a45_closed_list_determinism_and_manifest(packer, source, tmp_path):
    root, files = source
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    result = packer.build_package(root, first)
    packer.build_package(root, second)
    assert first.read_bytes() == second.read_bytes()
    assert result["status"] == "passed" and result["files"] == len(files)
    checksum = hashlib.sha256(first.read_bytes()).hexdigest()
    assert result["sha256"] == checksum
    assert first.with_suffix(".zip.sha256").read_text(encoding="utf-8") == (
        f"{checksum}  first.zip\n"
    )
    assert packer.verify_package(first, root)["files"] == len(files)
    with zipfile.ZipFile(first) as archive:
        assert set(archive.namelist()) == {*files, packer.MANIFEST}
        manifest = json.loads(archive.read(packer.MANIFEST))
        assert set(manifest["files"]) == set(files)
        for name, data in files.items():
            assert archive.read(name) == data
            assert manifest["files"][name] == {
                "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            }
        assert not any(b"synthetic_private_marker" in archive.read(n) for n in archive.namelist())


@pytest.mark.parametrize("existing", ["archive", "checksum"])
def test_a45_existing_outputs_are_not_overwritten(packer, source, tmp_path, existing):
    root, _ = source
    archive = tmp_path / "keep.zip"
    checksum = archive.with_suffix(".zip.sha256")
    kept = archive if existing == "archive" else checksum
    kept.write_bytes(b"preserve-existing")
    with pytest.raises(FileExistsError):
        packer.build_package(root, archive)
    assert kept.read_bytes() == b"preserve-existing"
    assert not (checksum if existing == "archive" else archive).exists()


def test_a45_missing_source_does_not_create_a_partial_package(packer, source, tmp_path):
    root, _ = source
    (root / "README.md").unlink()
    output = tmp_path / "missing.zip"
    with pytest.raises((ValueError, FileNotFoundError)):
        packer.build_package(root, output)
    assert not output.exists() and not output.with_suffix(".zip.sha256").exists()


@pytest.mark.parametrize("entry", [
    "../private.txt", "/absolute.txt", "C:/private.txt", "src\\private.py",
    "src/./private.py", "src/../private.py", "app.py", "APP.PY", "OFFLINE-MANIFEST.json",
    "user_data/private.py", "local_data/private.txt", ".env.local", "old.zip",
])
def test_a45_unsafe_or_duplicate_listing_is_rejected(packer, source, tmp_path, entry):
    root, _ = source
    with (root / LISTING).open("a", encoding="utf-8") as stream:
        stream.write(entry + "\n")
    with pytest.raises(ValueError):
        packer.build_package(root, tmp_path / "invalid.zip")
    assert not (tmp_path / "invalid.zip").exists()


def test_a45_symlinked_source_is_rejected(packer, source, tmp_path):
    root, _ = source
    outside = tmp_path / "outside.txt"
    outside.write_text("synthetic_private_marker", encoding="utf-8")
    (root / "README.md").unlink()
    try:
        (root / "README.md").symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit creating test symlinks")
    with pytest.raises(ValueError):
        packer.build_package(root, tmp_path / "linked.zip")


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction")
def test_a45_directory_junction_is_rejected(packer, source, tmp_path):
    root, _ = source
    target = tmp_path / "outside directory"
    target.mkdir()
    (target / "secret.py").write_text("synthetic_private_marker", encoding="utf-8")
    link = root / "linked directory"
    environment = os.environ | {
        "XHS_PACKAGE_QA_LINK": str(link), "XHS_PACKAGE_QA_TARGET": str(target),
    }
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
         ("New-Item -ItemType Junction -Path $env:XHS_PACKAGE_QA_LINK "
          "-Target $env:XHS_PACKAGE_QA_TARGET -ErrorAction Stop | Out-Null")],
        env=environment, capture_output=True, check=False, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode:
        pytest.skip("OS does not permit creating test junctions")
    try:
        with (root / LISTING).open("a", encoding="utf-8") as stream:
            stream.write("linked directory/secret.py\n")
        with pytest.raises(ValueError, match="LINKED_SOURCE"):
            packer.build_package(root, tmp_path / "junction.zip")
    finally:
        link.rmdir()  # Remove the junction itself, without recursing into its target.
    assert (target / "secret.py").read_text(encoding="utf-8") == "synthetic_private_marker"


@pytest.mark.parametrize("tamper", ["changed", "missing", "extra", "traversal", "duplicate"])
def test_a45_integrity_check_rejects_changed_archive_without_extracting(
    packer, source, tmp_path, tamper
):
    root, _ = source
    original = tmp_path / "original.zip"
    packer.build_package(root, original)
    with zipfile.ZipFile(original) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if tamper == "changed":
        entries["app.py"] = b"changed\n"
    elif tamper == "missing":
        del entries["README.md"]
    elif tamper == "extra":
        entries["private.txt"] = b"unexpected"
    elif tamper == "traversal":
        entries["../outside.txt"] = b"unexpected"
    altered = tmp_path / "altered.zip"
    with zipfile.ZipFile(altered, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        if tamper == "duplicate":
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr("app.py", b"another version")
    # Recompute the outer checksum so this tests the contents, not just the sidecar.
    altered.with_suffix(".zip.sha256").write_text(
        f"{hashlib.sha256(altered.read_bytes()).hexdigest()}  altered.zip\n", encoding="utf-8",
    )
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    with pytest.raises(ValueError):
        packer.verify_package(altered, root)
    assert sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("failure", ["missing", "mismatch"])
def test_a45_archive_checksum_is_required_and_checked(packer, source, tmp_path, failure):
    root, _ = source
    output = tmp_path / "checksum.zip"
    packer.build_package(root, output)
    checksum = output.with_suffix(".zip.sha256")
    if failure == "missing":
        checksum.unlink()
    else:
        checksum.write_text("0" * 64 + "  checksum.zip\n", encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        packer.verify_package(output, root)


def test_a45_sidecar_write_failure_removes_only_new_output(packer, source, tmp_path, monkeypatch):
    root, _ = source
    output = tmp_path / "partial.zip"
    original = Path.open

    def fail(path, *args, **kwargs):
        if path == output.with_suffix(".zip.sha256"):
            raise OSError("synthetic write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail)
    with pytest.raises(OSError):
        packer.build_package(root, output)
    assert not output.exists() and not output.with_suffix(".zip.sha256").exists()


@pytest.mark.parametrize("tamper", ["size_bool", "duplicate_key", "listing"])
def test_a45_invalid_manifest_and_inconsistent_packed_listing_are_rejected(
    packer, source, tmp_path, tamper
):
    root, _ = source
    original = tmp_path / "original.zip"
    packer.build_package(root, original)
    with zipfile.ZipFile(original) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries[packer.MANIFEST])
    if tamper == "size_bool":
        entries["app.py"] = b"x"
        manifest["files"]["app.py"] = {"size": True, "sha256": hashlib.sha256(b"x").hexdigest()}
    elif tamper == "listing":
        entries[LISTING] = b"scripts/offline-files.txt\n"
        manifest["files"][LISTING] = {
            "size": len(entries[LISTING]), "sha256": hashlib.sha256(entries[LISTING]).hexdigest(),
        }
    text = json.dumps(manifest)
    if tamper == "duplicate_key":
        text = text.replace('"format":', '"format": "duplicate", "format":', 1)
    entries[packer.MANIFEST] = text.encode()
    altered = tmp_path / "invalid-manifest.zip"
    with zipfile.ZipFile(altered, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    altered.with_suffix(".zip.sha256").write_text(
        f"{hashlib.sha256(altered.read_bytes()).hexdigest()}  invalid-manifest.zip\n", encoding="utf-8",
    )
    with pytest.raises(ValueError):
        packer.verify_package(altered, root)


def run_cli(script, cwd, *args):
    bootstrap = (
        "import runpy,socket,sys; "
        "deny=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); "
        "socket.socket.connect=deny; socket.create_connection=deny; "
        "sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name='__main__')"
    )
    return subprocess.run(
        [sys.executable, "-c", bootstrap, str(script), *map(str, args)],
        cwd=cwd, capture_output=True, check=False, timeout=20,
    )


@pytest.mark.parametrize("damage", ["compression_method", "compressed_data", "manifest_depth"])
def test_a45_unreadable_members_fail_cleanly_without_writing(
    packer, source, tmp_path, damage
):
    root, files = source
    output = tmp_path / "unreadable.zip"
    packer.build_package(root, output)
    payload = bytearray(output.read_bytes())
    with zipfile.ZipFile(output) as archive:
        first = archive.getinfo("app.py")
        member = archive.infolist()[0]
        central_offset = archive.start_dir
        entries = {name: archive.read(name) for name in archive.namelist()}
    if damage == "compression_method":
        # An intact ZIP with a method that zipfile cannot decode; patch both headers.
        assert payload[central_offset:central_offset + 4] == b"PK\x01\x02"
        struct.pack_into("<H", payload, member.header_offset + 8, 99)
        struct.pack_into("<H", payload, central_offset + 10, 99)
        output.write_bytes(payload)
    elif damage == "compressed_data":
        name_size, extra_size = struct.unpack_from("<HH", payload, first.header_offset + 26)
        start = first.header_offset + 30 + name_size + extra_size
        # A reserved DEFLATE block type fails in the decompressor, before CRC validation.
        payload[start] = 0x07
        output.write_bytes(payload)
    else:
        entries[packer.MANIFEST] = ("[" * 2000 + "]" * 2000).encode()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    checksum = output.with_suffix(".zip.sha256")
    checksum.write_text(
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n", encoding="utf-8",
    )
    before = {path: path.read_bytes() for path in (output, checksum)}
    before_files = {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()}
    with pytest.raises(ValueError):
        packer.verify_package(output, root)
    result = run_cli(root / "scripts/package_offline.py", tmp_path, "verify", output)
    assert result.returncode == 1, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["status"] == "failed"
    assert json.loads(result.stdout)["error"] == "PACKAGE_CHECK_FAILED"
    assert b"Traceback" not in result.stderr
    assert {path: path.read_bytes() for path in before} == before
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*") if path.is_file()} == before_files
    assert all((root / name).read_bytes() == data for name, data in files.items())


def test_a45_cli_from_another_directory_is_offline_and_reports_failure(source, tmp_path):
    root, _ = source
    output = tmp_path / "交付文件 with spaces" / "cli.zip"
    script = root / "scripts/package_offline.py"
    result = run_cli(script, tmp_path, "build", "--output", output)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert json.loads(result.stdout)["status"] == "passed"
    result = run_cli(script, tmp_path, "verify", output)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "passed"
    result = run_cli(script, tmp_path, "build", "--output", output)
    assert result.returncode != 0 and json.loads(result.stdout)["status"] == "failed"
    assert json.loads(result.stdout)["message"] == "目标已存在，请换新文件名。"
    result.stdout.decode("ascii")
    assert b"Traceback" not in result.stderr
    output.write_bytes(b"not a zip")
    result = run_cli(script, tmp_path, "verify", output)
    assert result.returncode != 0 and json.loads(result.stdout)["status"] == "failed"
    assert b"Traceback" not in result.stderr


def test_a45_current_source_package_covers_all_shipped_code_and_fixtures(packer, tmp_path):
    output = tmp_path / "current.zip"
    result = packer.build_package(ROOT, output)
    assert packer.verify_package(output, ROOT)["sha256"] == result["sha256"]
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    required = {
        path.relative_to(ROOT).as_posix()
        for directory in ("src", "tests", "scripts")
        for path in (ROOT / directory).rglob("*")
        if path.suffix in {".py", ".ps1"} and "__pycache__" not in path.parts
    }
    required |= {
        line[2:] for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
        if line.startswith(("!/examples/", "!/data_templates/")) and line.endswith(".csv")
    }
    required |= {
        ".streamlit/config.toml", ".env.example", "start.cmd", "stop.cmd", "app.py",
        "pyproject.toml", "requirements-lock.txt", "README.md", "AGENTS.md", "DATA_CONTRACT.md",
        "SCORING_RULES.md", "ACCEPTANCE_CASES.md", "TASKS.md", "CHANGELOG.md",
        "PRD.md", "MULTI_AGENT_DESIGN.md", "CODEX_START_PROMPT.md",
    }
    assert required <= names
    assert not any(name.startswith(("user_data/", "local_data/", ".git/", ".venv/")) for name in names)
