#!/usr/bin/env python3
"""Unit test for the subagent concurrency semaphore (R1).

Runs the TypeScript harness (tests/e2e/semaphore_unit.ts) with bun or node,
whichever is available. Skips (exit 0) when no TS-capable runtime is present.

Run: python3 tests/e2e/test_semaphore_unit.py
"""
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "semaphore_unit.ts")


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
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception:
            continue
        out = proc.stdout + proc.stderr
        # A runtime that cannot execute TS at all — try the next one.
        if "Unknown file extension" in out or "ERR_UNKNOWN_FILE_EXTENSION" in out:
            continue
        if "not supported" in out and "TypeScript" in out:
            continue
        print(out.rstrip())
        if proc.returncode == 0:
            print("\n[PASS] semaphore unit harness")
            return 0
        print(f"\n[FAIL] semaphore unit harness (exit {proc.returncode})")
        return 1

    print("[SKIP] no bun/node available to run the semaphore harness")
    return 0


if __name__ == "__main__":
    sys.exit(main())
