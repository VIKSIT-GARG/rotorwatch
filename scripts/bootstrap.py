#!/usr/bin/env python3
"""Local bootstrap for Linux, macOS and Windows; no shell activation required."""

import argparse
import os
import subprocess
import sys
import venv
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    if sys.version_info < (3, 11):  # noqa: UP036 - bootstrap must check older system Pythons.
        raise SystemExit("Install Python 3.11 or newer, then run this script again.")
    root = Path(__file__).resolve().parents[1]
    environment = root / ".venv"
    executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.exists():
        venv.EnvBuilder(with_pip=True).create(environment)
    target = str(root) + ("[browser]" if args.browser else "")
    subprocess.run([str(executable), "-m", "pip", "install", "-e", target], check=True)
    if args.browser:
        subprocess.run(
            [str(executable), "-m", "playwright", "install", "--only-shell", "chromium"], check=True
        )
    if args.demo:
        subprocess.run([str(executable), "-m", "drone_assistant", "demo"], cwd=root, check=True)
    print(f"Open the dashboard: {executable} -m drone_assistant serve --demo")
    print("Then visit http://127.0.0.1:8000")
    print(
        "For live operation, copy .env.example to .env, set API_TOKEN and provider credentials, then use 'serve'."
    )


if __name__ == "__main__":
    main()
