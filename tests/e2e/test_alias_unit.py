#!/usr/bin/env python3
"""Unit test for the tool-name-alias input normalization (question options).

Runs the TypeScript harness (tests/e2e/alias_unit.ts) with bun or node,
whichever is available. Skips (exit 0) when no TS-capable runtime is present.

Run: python3 tests/e2e/test_alias_unit.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "alias_unit.ts")


def main():
    runners = []
    bun = shutil.which("bun")
    if bun:
        runners.append([bun, "run", HARNESS])
    node = shutil.which("node")
    if node:
        runners.append([node, "--experimental-strip-types", HARNESS])
        runners.append([node, HARNESS])
    for cmd in runners:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        except Exception:
            continue
        out = proc.stdout + proc.stderr
        if "Unknown file extension" in out or "ERR_UNKNOWN_FILE_EXTENSION" in out:
            continue
        if "not supported" in out and "TypeScript" in out:
            continue
        print(out.rstrip())
        if proc.returncode == 0:
            print("\n[PASS] alias-input unit harness")
            return 0
    if runners:
        print("[FAIL] alias-input unit harness failed on every runner")
        return 1
    print("[SKIP] No bun/node runtime available")
    return 0


if __name__ == "__main__":
    sys.exit(main())
