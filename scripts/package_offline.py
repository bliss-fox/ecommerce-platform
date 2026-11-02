"""Build and verify a closed-list offline source package using only the stdlib."""

import argparse
import hashlib
import io
import json
import stat
import sys
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LISTING = "scripts/offline-files.txt"
MANIFEST = "OFFLINE-MANIFEST.json"
FORMAT = "xhs-offline-package-v1"
FORBIDDEN = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "user_data", "local_data"}


def validate_name(name):
    if (
        not isinstance(name, str) or not name or "\\" in name or ":" in name
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(part.casefold() in FORBIDDEN for part in name.split("/"))
    ):
        raise ValueError("INVALID_PATH")
    leaf = name.rsplit("/", 1)[-1].casefold()
    if (leaf.startswith(".env") and leaf != ".env.example") or leaf.endswith(
        (".db", ".sqlite", ".sqlite3", ".log", ".zip", ".pyc")
    ):
        raise ValueError("PRIVATE_OR_GENERATED_FILE")


def read_source(root, name):
    validate_name(name)
    root = root.resolve(strict=True)
    path = root
    for part in name.split("/"):
        path = path / part
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or (
            getattr(metadata, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError("LINKED_SOURCE")
    if not path.resolve(strict=True).is_relative_to(root) or not path.is_file():
        raise ValueError("INVALID_SOURCE")
    return path.read_bytes()


def parse_listing(data):
    names = []
    for line in data.decode("utf-8-sig").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        validate_name(name)
        names.append(name)
    if (
        LISTING not in names or MANIFEST.casefold() in {name.casefold() for name in names}
        or len(names) != len({name.casefold() for name in names})
    ):
        raise ValueError("INVALID_LISTING")
    return sorted(names)


def listed_files(root):
    return parse_listing(read_source(root, LISTING))


def checksum_path(archive):
    return archive.with_suffix(archive.suffix + ".sha256")


def checksum_text(archive, digest):
    return f"{digest}  {archive.name}\n"


def build_package(root, output):
    root, output = Path(root), Path(output)
    checksum = checksum_path(output)
    if output.suffix.lower() != ".zip":
        raise ValueError("ZIP_OUTPUT_REQUIRED")
    if any(path.exists() or path.is_symlink() for path in (output, checksum)):
        raise FileExistsError("OUTPUT_EXISTS")
    sources = {name: read_source(root, name) for name in listed_files(root)}
    manifest = {
        "format": FORMAT,
        "dependencies_included": False,
        "files": {
            name: {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in sources.items()
        },
    }
    entries = sources | {
        MANIFEST: (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    created = []
    try:
        for path, data in (
            (output, payload), (checksum, checksum_text(output, digest).encode("utf-8")),
        ):
            with path.open("xb") as stream:
                created.append(path)
                stream.write(data)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return {
        "status": "passed", "files": len(sources), "sha256": digest,
        "archive": str(output.resolve()), "checksum": str(checksum.resolve()),
    }


def unique_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_MANIFEST_KEY")
        result[key] = value
    return result


def verify_package(path, root):
    path, root = Path(path), Path(root)
    expected = set(listed_files(root))
    payload = path.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if checksum_path(path).read_text(encoding="utf-8") != checksum_text(path, digest):
        raise ValueError("ARCHIVE_CHECKSUM_MISMATCH")
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            for info in infos:
                validate_name(info.filename)
                if info.is_dir() or info.flag_bits & 1 or stat.S_IFMT(info.external_attr >> 16) not in (
                    0, stat.S_IFREG,
                ):
                    raise ValueError("INVALID_MEMBER")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise ValueError("UNSUPPORTED_COMPRESSION")
            if len(names) != len(set(names)) or set(names) != expected | {MANIFEST}:
                raise ValueError("PACKAGE_MEMBERS_MISMATCH")
            if set(parse_listing(archive.read(LISTING))) != expected:
                raise ValueError("PACKED_LISTING_MISMATCH")
            try:
                manifest = json.loads(archive.read(MANIFEST), object_pairs_hook=unique_json_pairs)
            except RecursionError:
                raise ValueError("INVALID_MANIFEST") from None
            if (
                not isinstance(manifest, dict)
                or set(manifest) != {"format", "dependencies_included", "files"}
                or manifest["format"] != FORMAT or manifest["dependencies_included"] is not False
                or not isinstance(manifest["files"], dict) or set(manifest["files"]) != expected
            ):
                raise ValueError("INVALID_MANIFEST")
            for name in expected:
                data = archive.read(name)
                entry = manifest["files"][name]
                if (
                    not isinstance(entry, dict) or set(entry) != {"size", "sha256"}
                    or type(entry["size"]) is not int
                    or entry != {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                ):
                    raise ValueError("FILE_CHECKSUM_MISMATCH")
    except (zipfile.BadZipFile, NotImplementedError, EOFError, zlib.error):
        raise ValueError("INVALID_ARCHIVE") from None
    return {"status": "passed", "files": len(expected), "sha256": digest}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--output", required=True, type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    args = parser.parse_args()
    try:
        result = (
            build_package(ROOT, args.output)
            if args.command == "build" else verify_package(args.archive, ROOT)
        )
    except FileExistsError:
        result = {"status": "failed", "error": "OUTPUT_EXISTS", "message": "目标已存在，请换新文件名。"}
    except FileNotFoundError:
        result = {
            "status": "failed", "error": "REQUIRED_FILE_MISSING",
            "message": "缺少清单文件、必需源码、ZIP 或同名 SHA256 文件，请核对后重试。",
        }
    except OSError:
        result = {
            "status": "failed", "error": "FILE_IO_FAILED",
            "message": "文件读取或写入失败，请核对路径、权限及磁盘空间。",
        }
    except ValueError:
        result = {
            "status": "failed", "error": "PACKAGE_CHECK_FAILED",
            "message": "清单、文件路径或包完整性检查失败，请核对源码与交付文件。",
        }
    # JSON stays parseable from pipes regardless of the Windows console codepage.
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    sys.exit(main())
