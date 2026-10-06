#!/usr/bin/env python3
"""Static gate for the V2-only planner plugin.

Checks the repository layout, package exports, the opt-in behavior contract,
the MCP-off default, the TUI dialog surface, the agent definitions, and the
installer — without needing an opencode binary or a running server.

Run: python3 tests/e2e/unit_gate.py
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    suffix = "" if ok else f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}: {name}{suffix}")


def exists(rel):
    return os.path.exists(os.path.join(ROOT, rel))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def main():
    print("[1] Layout")
    for rel in [
        "src/index.ts",
        "src/core.ts",
        "src/commands.ts",
        "src/tui.ts",
        "index.ts",
        "tui.ts",
        "install.sh",
        "package.json",
        "tsconfig.json",
        "agents-v2/planner.md",
        "agents-v2/brainstormer.md",
        "agents-v2/worker.md",
        "agents-v2/tester.md",
        "agents-v2/vision.md",
    ]:
        check(f"{rel} exists", exists(rel))
    for stale in ["src/v2/setup.ts", "src/commands.ts.bak", "plugin", "agents"]:
        check(f"stale path removed: {stale}", not exists(stale))

    print("\n[2] package.json")
    pkg = json.loads(read("package.json"))
    exports = pkg.get("exports", {})
    check('exports "." (server entry)', exports.get(".") == "./src/index.ts")
    check('exports "./tui" (CLI entry)', exports.get("./tui") == "./src/tui.ts")
    check("no runtime dependencies (zero-dep discovered plugin)", not pkg.get("dependencies"))
    dev = pkg.get("devDependencies", {})
    check("@opencode/plugin is a devDependency (types only)", "@opencode/plugin" in dev)
    files = pkg.get("files", [])
    check("files ships src/ and agents-v2/", "src" in files and "agents-v2" in files)
    check("version bumped to 0.4.x", str(pkg.get("version", "")).startswith("0.4"))

    print("\n[3] Server plugin contract (src/index.ts)")
    idx = read("src/index.ts")
    check(
        "runtime import of @opencode/plugin removed",
        'import type { Plugin as PluginApi } from "@opencode/plugin"' in idx
        and re.search(r'import\s*\{[^}]*Plugin[^}]*\}\s*from\s*"@opencode/plugin"', idx) is None,
    )
    check("never sets a default agent", "editor.default(" not in idx)
    check("context hook gated on the planner agent", 'event.agent !== "planner"' in idx)
    check("installs agent definitions", "ensureAgentFiles(repo)" in idx)
    check("no-op unless planner: no other hooks inject prompts", idx.count(".hook(") >= 3)
    check("does not touch MCP servers (native /mcps owns them)", "ctx.mcp" not in idx and "planner-mcp" not in idx)
    check("config watcher present", "mtimeOf(cfgPath)" in idx and "setInterval(" in idx)
    check("screenshot env hook present", "OPENCODE_SCREENSHOT_DIR" in idx)
    check("cycle limit injected only for planner", "Worker/tester cycle limit" in idx)
    check("parallelism rule injected", "maxParallelSubagents" in idx)
    check("vision instructions wired", "VISION_INSTRUCTION" in idx and "PROXY_VISION_INSTRUCTION" in idx)
    check("only /plan server command (switches agent)", 'name: "plan"' in idx and "switchAgent" in idx)
    for gone in ["planner-config", "planner-vision", "planner-status", "planner-help", "planner-mcp"]:
        check(f"server command /{gone} removed", f'name: "{gone}"' not in idx)
    check("no command reply/retry machinery left", "deliverReply" not in idx and "pendingReplies" not in idx)

    print("\n[4] TUI plugin contract (src/tui.ts)")
    tui = read("src/tui.ts")
    check("no runtime @opencode/plugin import", re.search(r'import\s*\{[^}]*Plugin[^}]*\}\s*from\s*"@opencode/plugin', tui) is None)
    for needle in ["ui.dialog.select", "keymap.layer", "ui.slot"]:
        check(f"TUI uses {needle}", needle in tui)
    check("exactly one TUI slash command", len(re.findall(r"slash:\s*\{", tui)) == 1)
    check("the slash command is /planner-config (opens the dialog)", 'slash: { name: "planner-config" }' in tui)
    check("no /planner-vision slash in the TUI", 'slash: { name: "planner-vision"' not in tui)
    check("palette entries exist for config/parallel/vision",
          all(f"planner.{x}" in tui for x in ["config", "parallel", "vision"]))
    check("config dialog includes max parallel subagents", '"maxParallel"' in tui or "maxParallel" in tui)
    for gone in ["planner.status", "planner.help", "planner.menu", "openMenu", "statusText", "helpText"]:
        check(f"TUI no longer registers {gone}", gone not in tui)
    check("TUI has no MCP dialog", "configureMcp" not in tui and "applyMcpConfig" not in tui)
    check("TUI config dialog covers brainstormer/worker/tester/vision",
          all(a in tui for a in ["Brainstormer", "Worker", "Tester", "Vision"]))

    print("\n[5] Core defaults (src/core.ts)")
    core = read("src/core.ts")
    check("no MCP config key (native /mcps owns MCPs)", "setMcpEnabled" not in core and "mcp: { enabled" not in core)
    commands = read("src/commands.ts")
    check("commands.ts has no status/help/config texts", not any(x in commands for x in ["statusText", "helpText", "configText"]))
    check("commands.ts keeps the dialog operations",
          "applyAgentConfig" in commands and "applyVisionConfig" in commands)
    check("no dead runPlannerCommand", "runPlannerCommand" not in commands)
    check("vision disabled by default", re.search(r"vision:\s*\{[^}]*enabled:\s*false", core) is not None)
    check("config path is .opencode/planner-plugin.json", '".opencode", "planner-plugin.json"' in core)
    check("atomic config write (tmp + rename)", "renameSync(tmp, path)" in core)
    check("agent templates install to .opencode/agents", '".opencode", "agents"' in core)
    check("legacy config migration kept", "migrateLegacyConfig" in core)
    check("model spec supports #variant", "parseModelSpec" in core and "modelRefFromSpec" in core)

    print("\n[6] Agent definitions (agents-v2)")
    planner = read("agents-v2/planner.md")
    check("planner is a primary agent", re.search(r"^mode:\s*primary", planner, re.M) is not None)
    check("planner uses the subagent tool", "subagent" in planner)
    check("planner references brainstormer/worker/tester", all(a in planner for a in ["brainstormer", "worker", "tester"]))
    check("planner documents the cycle limit", "maxCycles" in planner)
    for name in ["brainstormer", "worker", "tester", "vision"]:
        body = read(f"agents-v2/{name}.md")
        check(f"{name} is a subagent", re.search(r"^mode:\s*subagent", body, re.M) is not None)
        check(f"{name} has permissions rules", "permissions:" in body)
        check(f"{name} cannot spawn subagents", "action: subagent" in body and "deny" in body)
    brainstormer = read("agents-v2/brainstormer.md")
    check("brainstormer spec-write exception comes AFTER the broad edit deny (last match wins)",
          brainstormer.index('resource: "docs/superpowers/specs/**"')
          > brainstormer.index('action: edit\n    resource: "*"'))
    check("brainstormer writes specs", "docs/superpowers/specs/" in brainstormer)
    check("brainstormer never asks questions (no NEEDS_INPUT reply format)",
          "QUESTIONS:" not in brainstormer and "CURRENT_UNDERSTANDING" not in brainstormer)
    planner_body = read("agents-v2/planner.md")
    check("planner checks the spec itself before the worker",
          "check the spec" in planner_body.lower())
    check("planner checks the worker's result before the tester",
          "check the worker's result" in planner_body.lower())
    check("planner no longer relays question rounds to the user",
          "relays these to the user" not in planner_body and "up to 5 rounds" not in planner_body)

    print("\n[7] Installer")
    proc = subprocess.run(["bash", "-n", os.path.join(ROOT, "install.sh")], capture_output=True, text=True)
    check("install.sh syntax valid", proc.returncode == 0, proc.stderr.strip())
    sh = read("install.sh")
    check("installs the plugin directory layout", "plugins/opencode-planner" in sh)
    check("installs global V2 agents", "agents" in sh and "AGENT_NAMES" in sh)
    check("removes legacy single-file plugin copies", "planner.ts" in sh and "LEGACY_TOP_FILES" in sh)
    check("supports uninstall", "uninstall" in sh and "uninstall_internal" in sh)

    print("\n[8] No stale references in src")
    for rel in ["src/index.ts", "src/core.ts", "src/commands.ts", "src/tui.ts"]:
        body = read(rel)
        check(f"{rel} has no planner-enable/disable", "planner-enable" not in body and "planner-disable" not in body)
        check(f"{rel} has no plugin/ imports", "../plugin/" not in body)

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
