"""Spawn a local Streamlit process without inheriting the launcher's pipe handles."""

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    app = args.app.resolve(strict=True)
    log_root = app.parent / "local_data"
    log_root.mkdir(exist_ok=True)
    with (
        (log_root / f"service-{args.port}.stdout.log").open("ab") as stdout,
        (log_root / f"service-{args.port}.stderr.log").open("ab") as stderr,
    ):
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "streamlit",
                "run",
                str(app),
                "--server.address",
                "127.0.0.1",
                "--server.port",
                str(args.port),
                "--server.headless",
                "true",
                "--browser.gatherUsageStats",
                "false",
            ],
            cwd=app.parent,
            stdin=subprocess.DEVNULL,
            stdout=stdout,
            stderr=stderr,
            close_fds=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    print(process.pid)


if __name__ == "__main__":
    main()
