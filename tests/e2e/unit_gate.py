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
    check("version bumped to 0.5.x", str(pkg.get("version", "")).startswith("0.5"))

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
    check("TUI uses dialog.select/keymap.layer/ui.slot",
          all(needle in tui for needle in ["ui.dialog.select", "keymap.layer", "ui.slot"]))
    check("two TUI slash commands (config + status)", len(re.findall(r"slash:\s*\{", tui)) == 2)
    check("the slash commands are /planner-config and /planner-status",
          'slash: { name: "planner-config" }' in tui and 'slash: { name: "planner-status" }' in tui)
    check("no /planner-vision slash in the TUI", 'slash: { name: "planner-vision"' not in tui)
    check("palette entries exist for config/parallel/vision",
          all(f"planner.{x}" in tui for x in ["config", "parallel", "vision"]))
    check("config dialog includes max parallel subagents", '"maxParallel"' in tui or "maxParallel" in tui)
    check("config menu stays open after each action (loops back to the first page)",
          "for (;;)" in tui and "Done — close" in tui)
    for gone in ["planner.help", "planner.menu", "openMenu", "statusText", "helpText"]:
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
    check("brainstormer questions use the NEEDS_INPUT protocol (no legacy round format)",
          "QUESTIONS:" in brainstormer and "CURRENT_UNDERSTANDING" not in brainstormer)
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

    print("\n[9] Concurrency enforcement (R1)")
    check("semaphore module exists", exists("src/semaphore.ts"))
    check("semaphore unit harness exists", exists("tests/e2e/semaphore_unit.ts"))
    check("semaphore unit runner exists", exists("tests/e2e/test_semaphore_unit.py"))
    check("live concurrency-gate suite exists", exists("tests/e2e/test_subagent_limit_live.py"))
    idx = read("src/index.ts")
    check("delegation tool resolved by name (subagent, task-shaped builds)",
          'editor.get("subagent")' in idx and 'editor.get("task")' in idx and "delegationToolName" in idx)
    check("wrapper is idempotent on transform replays", "__plannerGate" in idx)
    check("permission-hook fallback cap present", 'permission.hook("evaluate"' in idx)
    check("cap is scoped to planner-originated calls", 'caller !== "planner"' in idx)
    check("debug logging gated by PLANNER_DEBUG", "PLANNER_DEBUG" in idx)
    check("serial prompt block states the rule is enforced", "plugin-enforced" in idx)
    check("raising the cap wakes queued tasks", "subagentGate.wake()" in idx)
    sem = read("src/semaphore.ts")
    check("semaphore limit is read dynamically", "limitFn" in sem)
    check("semaphore releases on throw", "finally" in sem)

    print("\n[10] Delegation watchdog (R2)")
    planner = read("agents-v2/planner.md")
    check("planner.md no longer allows skipping the brainstormer", "Skip ONLY for trivial" not in planner)
    check("brainstormer step is unconditional (ALWAYS, first)", "ALWAYS, and always FIRST" in planner)
    check("planner.md states you are not a builder", "You are not a builder" in planner)
    idx = read("src/index.ts")
    check("watchdog nudge marker + bound", "planner-delegation-check" in idx and "MAX_NUDGES_PER_TURN" in idx)
    check("watchdog tracks subagent calls", "recordSubagentCall" in idx)
    check("watchdog detects READY spec without worker/tester", "spec READY but no worker/tester" in idx)
    check("watchdog resets on a new user prompt", 'ctx.session.hook("prompt"' in idx)
    check("watchdog listens for turn-end events", 'session.execution.succeeded' in idx and "event.subscribe" in idx)
    check("nudge is a resuming synthetic message", "session.synthetic" in idx and "resume: true" in idx)
    check("context block states delegation is mandatory", "Delegation is mandatory" in idx)
    check("watchdog skips nudging when subagents are missing", 'agentFileExists(repo, "brainstormer")' in idx)
    core = read("src/core.ts")
    check("enforceDelegation defaults to true", "enforceDelegation: true" in core)
    check("enforceDelegation setter exists", "setEnforceDelegation" in core)
    tui = read("src/tui.ts")
    check("TUI exposes the delegation toggle", "Delegation enforcement" in tui and "setEnforceDelegation" in tui)
    check("live delegation suite exists", exists("tests/e2e/test_delegation_live.py"))

    print("\n[11] Config defaults + snapshot (R3)")
    core = read("src/core.ts")
    check("global defaults file helper exists", "planner-defaults.json" in core and "defaultsConfigPath" in core)
    check("config dir resolution matches the installer", "OPENCODE_CONFIG_DIR" in core and "XDG_CONFIG_HOME" in core)
    check("defaults file auto-created when missing", "ensureDefaultsFile" in core)
    check("effective read = project over defaults", "readDefaultsConfig" in core and "configFromData" in core)
    check("first planner use snapshots the defaults", "snapshotProjectConfig" in core)
    check("re-sync action exists", "resyncProjectConfig" in core)
    check("defaults writes carry no project metadata", "delete merged.plannerVersion" in core)
    check("project writes stamp the plugin version", "merged.plannerVersion = pluginVersion()" in core)
    check("variants are override-if-present (not resurrected from defaults)",
          '"variants" in data' in core)
    idx = read("src/index.ts")
    check("plugin creates the defaults file on load", "ensureDefaultsFile()" in idx)
    check("snapshot on first planner use (context hook + /plan)", idx.count("ensureProjectSnapshot()") >= 3)
    tui = read("src/tui.ts")
    check("TUI exposes re-sync + global defaults + cycles",
          "Re-sync from defaults" in tui and "Global defaults" in tui and "Max worker→tester cycles" in tui)
    sh = read("install.sh")
    check("installer creates planner-defaults.json", "planner-defaults.json" in sh and "DEFAULTS_FILE" in sh)
    check("installer never overwrites existing defaults", '[ ! -f "$DEFAULTS_FILE" ]' in sh)
    check("uninstall keeps the defaults file", "keeps the defaults" in sh or "survive an uninstall" in sh)

    print("\n[12] Versioning + update flow (R4)")
    core = read("src/core.ts")
    check("install marker reader (version + sourceRoot)", "installMarkerPath" in core and "sourceRoot" in core)
    check("version comparison helper", "export function compareVersions" in core)
    check("version status detection", "detectVersionStatus" in core and "projectConfigVersion" in core)
    check("project-local plugin copy detected", "localPluginVersion" in core)
    check("GitHub latest check with 24h cache", "raw.githubusercontent.com" in core and "UPDATE_CHECK_MS" in core)
    check("update execution (git pull + install.sh)", '"--ff-only"' in core and "install.sh" in core)
    check("update aborts when the source is not a git clone", "is not a git clone" in core)
    check("manual check bypasses a fresh cache", "force ? undefined : readCachedLatest()" in core)
    check("failed pull does not reinstall a stale tree", "not updated (see" in core)
    check("agent refresh keeps user edits (hash marker)", "refreshAgentDefinitions" in core and "sha256" in core)
    idx = read("src/index.ts")
    check("plugin version stamped into project config", "pluginVersion()" in core)
    tui = read("src/tui.ts")
    check("status dialog lists versions + actions",
          all(x in tui for x in ["Running v", "Check for updates", "Update now", "Refresh agent definitions"]))
    check("one-time startup update notice", "maybeNotifyUpdate" in tui and "PLANNER_NO_UPDATE_CHECK" in tui)
    sh = read("install.sh")
    check("installer records sourceRoot", '"sourceRoot"' in sh)
    check("installer writes the global agent marker", ".planner-agents.json" in sh and "hash_file" in sh)
    check("uninstall removes the agent marker", 'rm -f "$AGENTS_DIR/.planner-agents.json"' in sh)
    check("update unit harness exists", exists("tests/e2e/update_unit.ts"))
    check("update unit runner exists", exists("tests/e2e/test_update_unit.py"))

    print("\n[13] Pipeline discipline + legacy agent upgrade (R6)")
    planner = read("agents-v2/planner.md")
    check("planner.md forbids overthinking", "Keep it short — do not overthink" in planner)
    check("planner.md handles goal continuations", "Automatic continuation messages are not new requests" in planner)
    check("planner.md names the requirements/spec file", "requirements/spec file" in planner)
    idx = read("src/index.ts")
    check("injected block carries the short-steps rule", "Keep it short — do not overthink" in idx)
    check("injected block handles goal continuations", "NOT new requests" in idx)
    core = read("src/core.ts")
    check("agent refresh supports force + backups", "backedUp" in core and "`${dest}.bak`" in core)
    tui = read("src/tui.ts")
    check("legacy install offers overwrite with backups", "legacy install" in tui and "Overwrite with backups" in tui)

    print("\n[14] Per-agent model pins (regression)")
    idx = read("src/index.ts")
    check("model pins are not gated on project agent files",
          "if (!agentFileExists(repo, name)) continue" not in idx)
    check("live agent-model pinning suite exists", exists("tests/e2e/test_agent_models_live.py"))
    core = read("src/core.ts")
    check("agentFileExists still used by the watchdog", "agentFileExists" in idx)

    print("\n[15] Brainstormer spec write + questions (R7)")
    bs = read("agents-v2/brainstormer.md")
    check("brainstormer uses the superpowers skills on the first try", "very first try" in bs)
    check("brainstormer writes the RELATIVE spec path", "RELATIVE path" in bs and "NEVER invent absolute paths" in bs)
    check("brainstormer verifies the file on disk before READY", "Verify the file on disk" in bs and "STATUS: FAILED" in bs)
    check("brainstormer may ask questions", "Ask questions while creating" in bs and "STATUS: NEEDS_INPUT" in bs)
    check("brainstormer question permission allowed", "- action: question" in bs and "effect: allow" in bs)
    check("brainstormer no longer claims full autonomy", "Never ask questions" not in bs)
    planner = read("agents-v2/planner.md")
    check("planner relays NEEDS_INPUT questions", "NEEDS_INPUT" in planner and "question` tool" in planner)
    idx = read("src/index.ts")
    check("injected block relays questions", "relay them to the user" in idx and "NEEDS_INPUT" in idx)
    check("specs dir is created at brainstormer delegation time, not at load",
          "ensureSpecsDir(repo)" in idx and 'subagentTarget(e.input) === "brainstormer"' in idx)
    check("core exports ensureSpecsDir", "export function ensureSpecsDir" in read("src/core.ts"))

    print("\n[16] Subagent model-override guard (regression)")
    idx = read("src/index.ts")
    check("subagent calls get model overrides stripped", "dropped model override" in idx)
    planner = read("agents-v2/planner.md")
    check("planner.md forbids per-call model overrides", "Never pass a `model` to the `subagent` tool" in planner)

    print("\n[17] Tool availability + question options (0.5.0 hardening)")
    idx = read("src/index.ts")
    check("hooks accept task-named delegation tools", 'DELEGATION_TOOLS' in idx and '"task"' in idx)
    check("planner file writes denied server-side",
          "planner must not run commands or write files" in idx and 'event.action === "edit"' in idx)
    check("watchdog tracks the planner writing files itself", "wroteFiles" in idx)
    check("watchdog treats a relayed question as a legitimate wait", "askedUser" in idx)
    check("watchdog nudges on an unusable brainstormer result",
          "brainstormer did not return READY or TRIVIAL" in idx)
    check("injected block demands real answer options",
          "never reduce an open decision to yes/no" in idx)
    check("alias plugin shipped in extras", exists("extras/tool-name-aliases.js"))
    check("alias plugin unit harness exists",
          exists("tests/e2e/alias_unit.ts") and exists("tests/e2e/test_alias_unit.py"))
    bs = read("agents-v2/brainstormer.md")
    check("brainstormer question guidance demands real options",
          "never flatten an" in bs and '"header"' in bs and "(Recommended)" in bs)
    planner = read("agents-v2/planner.md")
    check("planner relay passes real options through",
          "never flatten" in planner and "2-4 concrete alternatives" in planner)
    check("planner.md states it has no write tools", "You have NO file-writing tools" in planner)
    check("planner.md documents tool-name error recovery",
          "No tool named" in planner and "mcp__" in planner)
    check("brainstormer.md documents tool-name error recovery",
          "No tool named" in bs and "mcp__" in bs)
    check("worker.md documents tool-name error recovery",
          "No tool named" in read("agents-v2/worker.md"))
    check("injected block forbids planner write attempts", "You have NO file-writing tools" in idx)
    extras = read("extras/tool-name-aliases.js")
    check("alias plugin registers mcp-style names",
          "MCP_HALLUCINATED_SERVERS" in extras and "MCP_BASE_ALIASES" in extras)
    idx = read("src/index.ts")
    check("watchdog distinguishes NEEDS_INPUT from unknown",
          '"needs_input"' in idx and "brainstormer asked questions that were not relayed" in idx)
    check("watchdog stops tracking sessions that switch agents", "plannerSessions.delete" in idx)
    check("loop-breaker nudges identical repeated calls mid-turn",
          "loopCounts" in idx and "loopNudges" in idx and "SAME tool call" in idx)
    core = read("src/core.ts")
    check("agentFileExists falls back to the global agents dir",
          "join(globalAgentsDir(), `${name}.md`)" in core)
    install_sh = read("install.sh")
    check("install.sh backs up differing agent files on --update-agents",
          "differed from the template" in install_sh)
    check("brainstormer batches questions into one call", "Ask at most ONCE per spec" in bs)
    check("templates demand relative repo paths",
          "Use RELATIVE paths" in bs and "Use RELATIVE paths" in read("agents-v2/worker.md"))

    passed = sum(1 for _, ok in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
