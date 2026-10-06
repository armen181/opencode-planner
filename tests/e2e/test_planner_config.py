#!/usr/bin/env python3
"""Installer + config-contract suite (no opencode binary needed).

Boots the V2 installer into a throwaway OPENCODE_CONFIG_DIR and verifies the
plugin-directory layout, the global agent install (missing-only, never
overwrites), the version marker, and a clean uninstall. Also pins the config
file path/schema contract the server plugin and TUI dialogs share.

Run: python3 tests/e2e/test_planner_config.py
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

SANDBOX = "/tmp/planner-install-sandbox"

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    suffix = "" if ok else f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{suffix}")


def run_install(*args, config_dir=SANDBOX):
    env = dict(os.environ, OPENCODE_CONFIG_DIR=config_dir)
    return subprocess.run(
        ["bash", os.path.join(ROOT, "install.sh"), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


def main():
    shutil.rmtree(SANDBOX, ignore_errors=True)

    print("[1] Fresh install")
    out = run_install("--yes")
    check("installer exits 0", out.returncode == 0, out.stderr[-400:])
    plugin_dir = os.path.join(SANDBOX, "plugins", "opencode-planner")
    for rel in ["index.ts", "tui.ts", "package.json", "src/index.ts", "src/core.ts", "src/commands.ts", "src/tui.ts"]:
        check(f"plugin dir has {rel}", os.path.exists(os.path.join(plugin_dir, rel)))
    for agent in ["planner", "brainstormer", "worker", "tester", "vision"]:
        check(f"global agent {agent}.md installed", os.path.exists(os.path.join(SANDBOX, "agents", f"{agent}.md")))
    marker = os.path.join(plugin_dir, ".planner-install.json")
    check("version marker written", os.path.exists(marker))
    if os.path.exists(marker):
        data = json.load(open(marker))
        check("marker names the plugin", data.get("name") == "opencode-planner")
        check("marker has a version", bool(data.get("version")))

    print("\n[2] Reinstall keeps user-edited agents")
    edited = os.path.join(SANDBOX, "agents", "worker.md")
    with open(edited, "a") as f:
        f.write("\n<!-- user edit -->\n")
    out = run_install("--yes")
    check("reinstall exits 0", out.returncode == 0, out.stderr[-400:])
    check("user edit survived reinstall", "user edit" in open(edited).read())

    print("\n[3] Legacy single-file copy is removed by install")
    legacy = os.path.join(SANDBOX, "plugins", "planner.ts")
    with open(legacy, "w") as f:
        f.write('import { Plugin } from "@opencode/plugin"\nexport default Plugin.define({ id: "planner", setup() {} })\n')
    out = run_install("--yes")
    check("legacy planner.ts removed", not os.path.exists(legacy))
    check("plugin directory still installed", os.path.exists(os.path.join(plugin_dir, "index.ts")))

    print("\n[4] Uninstall")
    out = run_install("uninstall", "--yes")
    check("uninstall exits 0", out.returncode == 0, out.stderr[-400:])
    check("plugin dir removed", not os.path.exists(plugin_dir))
    check("edited agent kept", os.path.exists(edited) and "user edit" in open(edited).read())
    check("untouched agents removed", not os.path.exists(os.path.join(SANDBOX, "agents", "planner.md")))

    print("\n[5] Config contract")
    core = open(os.path.join(ROOT, "src", "core.ts")).read()
    check("config path is <repo>/.opencode/planner-plugin.json",
          '".opencode", "planner-plugin.json"' in core)
    check("planner model is not configurable in commands",
          "planner uses opencode's main model" in open(os.path.join(ROOT, "src", "commands.ts")).read())
    check("no MCP config key (native /mcps owns MCPs)", "setMcpEnabled" not in core)
    check("unknown config keys are preserved on write", "...base," in core)

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    if passed == len(RESULTS):
        shutil.rmtree(SANDBOX, ignore_errors=True)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
