# Planner Plugin — Single Config + TUI Popups / Web Commands + Inline Screenshots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the planner opencode plugin so configuration lives in ONE file (`.opencode/planner-plugin.json`), TUI config is popup-only, web config is command-only (no duplicated slash commands in the TUI), and screenshots from any source surface inline in the main session from a temp dir.

**Architecture:** A shared `planner-core.ts` (single config file read/write, no `opencode.json` writes) is used by a TUI plugin (`planner-tui.ts`, popups) and a new server plugin (`planner.ts`, `config` hook injects agent models at load + `chat.message` hook handles web commands + `tool.execute.after`/dir-watch injects screenshots into the parent session). `web-commands.json` and its `install.sh` merge are removed. The `vision-delegation.ts` plugin stays but reads the new config path.

**Tech Stack:** TypeScript, `@opencode-ai/plugin`, opencode 1.18.x server + TUI, `$TMPDIR/opencode-screenshots/` temp dir, Python 3 E2E harness booting a real `opencode serve` instance.

## Global Constraints

- Single configuration file: `.opencode/planner-plugin.json`. NO writes to `opencode.json` agent entries or `default_agent` ever.
- Config shape (unchanged from today, file renamed): `{ enabled, models: {planner,worker,tester}, maxCycles, vision: {enabled, model} }`.
- TUI: `/planner-enable|disable|config|status|vision` are POPUPS only (existing `planner-tui.ts` dialogs, unchanged UX).
- Web: the same commands are TEXT commands handled by the server plugin `chat.message` hook.
- No `planner-*` entries may be registered in `config.json` `command` (that is what duplicates them in the TUI text path).
- Agents: `planner` (primary, default), `worker`, `tester`, `vision` (subagents). Names fixed.
- Default model `opencode/deepseek-v4-flash-free`; E2E uses `akna/akna-deep` (planner/worker/tester) and `akna/akna-luna` (vision).
- Screenshots: `$TMPDIR/opencode-screenshots/`, injected as a `FilePart` into the parent (main) session via `promptAsync` with `noReply: true`; TTL cleanup 24h.
- The `vision-delegation.ts` server plugin keeps working and reads `planner-plugin.json`.

---

### Task 1: Rename config to `planner-plugin.json` and drop `opencode.json` writes

**Files:**
- Modify: `plugin/planner-core.ts`
- Test: `tests/e2e/unit_gate.py`, `tests/e2e/test_planner_config.py`

**Interfaces:**
- Consumes: nothing (node:fs, node:path).
- Produces:
  - `plannerConfigPath(repo)` → `join(repo, ".opencode", "planner-plugin.json")`
  - `readPlannerConfig(repo)` / `writePlannerConfig(repo, cfg)` / `defaultPlannerConfig()` — same shape as today
  - `setAgentModel(repo, agent, model)`, `setVisionEnabled`, `setVisionModel`, `isVisionEnabled`, `isPlannerEnabled`, `parseModelId` — unchanged signatures
  - DELETE: `syncOpencodeConfig`, `opencodeConfigPath` (no longer used by this plugin)
  - ADD: `migrateLegacyConfig(repo)` — if `planner.json` exists and `planner-plugin.json` does not, copy contents, remove old file
- Later tasks consume: `readPlannerConfig`, `isPlannerEnabled`, `migrateLegacyConfig`, `PLANNER_AGENTS`, `DEFAULT_MODEL`.

- [ ] **Step 1: Update the failing test to the new path**

Edit `tests/e2e/unit_gate.py`: replace `planner_config_path` and any `planner.json` reference with `planner-plugin.json`; update the "sync" assertions to assert `opencode.json` is NOT written; add a `migrateLegacyConfig` mirror (python) check.

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 tests/e2e/unit_gate.py`
Expected: FAIL — planner-plugin.json path assertions fail.

- [ ] **Step 3: Implement `planner-core.ts` changes**

Rename `plannerConfigPath`, remove `syncOpencodeConfig` + `opencodeConfigPath` + their call from `writePlannerConfig`, add `migrateLegacyConfig(repo)`.

- [ ] **Step 4: Update `test_planner_config.py`**

Rewrite to assert single-file behavior: set model writes ONLY `planner-plugin.json`; `opencode.json` untouched (or absent); vision round-trip in one file; legacy `planner.json` migrates.

- [ ] **Step 5: Run both tests to verify they pass**

Run: `python3 tests/e2e/unit_gate.py && python3 tests/e2e/test_planner_config.py`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add plugin/planner-core.ts tests/e2e/unit_gate.py tests/e2e/test_planner_config.py
git commit -m "refactor(planner): single config planner-plugin.json, drop opencode.json sync"
```

---

### Task 2: New server plugin `planner.ts` — config hook + web commands + screenshot injection

**Files:**
- Create: `plugin/planner.ts`
- Test: `tests/e2e/unit_gate.py`, `tests/e2e/test_planner_live.py` (Task 5)

**Interfaces:**
- Consumes: `readPlannerConfig`, `migrateLegacyConfig`, `isPlannerEnabled`, `PLANNER_AGENTS`, `setAgentModel`, `setVisionEnabled`, `setVisionModel`, `parseModelId` from `./planner-core`.
- Produces: `planner.ts` default-exports a server plugin with hooks:
  - `config(config)` — if enabled: set `config.default_agent = "planner"`, `config.agent.<name>.model` for each of planner/worker/tester, and `config.agent.vision = { model, mode: "subagent" }` when vision enabled. Also registers NOTHING under `config.command` (deliberate — web commands are handled by chat.message, so they never surface as text commands in the TUI).
  - `chat.message(input, output)` — parse `/planner-enable`, `/planner-disable`, `/planner-config <agent> <model>`, `/planner-status`, `/planner-vision on|off` and act via planner-core (web path). Replace `output.parts` with the result text.
  - `event` — track `session.created` to map subagent sessions to their parent (via `parentID`) for screenshot injection.
  - `tool.execute.after` — when a tool run produced a new `.png` under `$TMPDIR/opencode-screenshots/`, find the parent (main) session of `input.sessionID` and inject the image as a `FilePart` via `client.session.promptAsync({ body: { parts: [filePart], noReply: true } })`.
  - dir-watch: poll `$TMPDIR/opencode-screenshots/` every 2s for new `.png`; on new file, inject into the parent session; delete files older than 24h.

- [ ] **Step 1: Write `plugin/planner.ts`**

Copy the reference skeleton below and implement fully:

```ts
import { existsSync, readFileSync, mkdirSync, readdirSync, statSync, rmSync } from "node:fs"
import { join } from "node:path"
import { tmpdir } from "node:os"
import {
  PLANNER_AGENTS, readPlannerConfig, migrateLegacyConfig, isPlannerEnabled,
  setAgentModel, setVisionEnabled, setVisionModel, parseModelId,
} from "./planner-core"

const SCREENSHOT_DIR = join(tmpdir(), "opencode-screenshots")
const TTL_MS = 24 * 60 * 60 * 1000

const WEB_CMD_RE = /^\/(planner-enable|planner-disable|planner-config|planner-status|planner-vision)(?:\s+(.*))?$/

export const PlannerServerPlugin = async ({ client, directory, worktree }) => {
  const repo = worktree ?? directory
  migrateLegacyConfig(repo)

  // sessionID -> parentID map for subagent sessions
  const parents = new Map<string, string>()
  const injected = new Set<string>() // screenshot paths already injected

  const filePart = (path: string) => {
    const buf = readFileSync(path)
    return { type: "file", mime: "image/png", filename: path.split("/").pop(), url: `data:image/png;base64,${buf.toString("base64")}` }
  }

  const injectScreenshot = async (parentID: string, path: string) => {
    if (!parentID || injected.has(path)) return
    injected.add(path)
    try {
      await client.session.promptAsync({
        path: { id: parentID },
        body: { parts: [filePart(path)], noReply: true } as never,
      })
    } catch (e) { console.error("[planner] screenshot inject failed:", String(e)) }
  }

  const sweep = async () => {
    mkdirSync(SCREENSHOT_DIR, { recursive: true })
    const now = Date.now()
    for (const f of readdirSync(SCREENSHOT_DIR)) {
      const p = join(SCREENSHOT_DIR, f)
      if (!f.endsWith(".png")) continue
      const st = statSync(p)
      if (now - st.mtimeMs > TTL_MS) rmSync(p, { force: true })
      else await injectScreenshot(parents.get(repo) ?? firstParent(), p)
    }
  }

  // ... full implementation of config / chat.message / event / tool.execute.after hooks
}
```

Implement the remaining hooks fully (config hook, chat.message command parsing + handler table, event session.created parent tracking, tool.execute.after detection of screenshot files). Start the dir-watch poll interval and call `sweep()` once at load.

- [ ] **Step 2: Unit gate**

Run: `python3 tests/e2e/unit_gate.py`
Expected: PASS (planner.ts exists, exports hooks, no legacy branding). Add unit-gate assertions: `planner.ts` contains `config`, `chat.message`, `tool.execute.after`, `SCREENSHOT_DIR`, `promptAsync`, and does NOT import `syncOpencodeConfig`.

- [ ] **Step 3: Commit**

```bash
git add plugin/planner.ts tests/e2e/unit_gate.py
git commit -m "feat(planner): server plugin — config hook, web commands, screenshot injection"
```

---

### Task 3: Agent prompts — screenshot capture guidance

**Files:**
- Modify: `agents/planner.md`, `agents/worker.md`, `agents/tester.md`

**Interfaces:**
- Consumes: nothing.
- Produces: updated agent instructions so the model knows to save screenshots to `$TMPDIR/opencode-screenshots/`.

- [ ] **Step 1: Add a "Visual validation" section to each agent**

`planner.md`: after delegation steps, add: "If the request needs visual validation, instruct the worker to capture a screenshot (browser via playwright/puppeteer, Android via `adb exec-out screencap -p`, etc.) and save it to `$TMPDIR/opencode-screenshots/`. The image surfaces in your session automatically."

`worker.md`: add step: "If the task needs visual validation, capture a screenshot of the running app (browser / emulator / device) and save it to `$TMPDIR/opencode-screenshots/` as a `.png`. Do not commit screenshots."

`tester.md`: add: "If visual validation matters, verify a screenshot exists in `$TMPDIR/opencode-screenshots/` and is referenced in the worker's report."

- [ ] **Step 2: Validate frontmatter + run unit gate**

Run: `python3 tests/e2e/unit_gate.py`
Expected: PASS (frontmatter unchanged/valid).

- [ ] **Step 3: Commit**

```bash
git add agents/planner.md agents/worker.md agents/tester.md
git commit -m "feat(planner): screenshot capture guidance in agent prompts"
```

---

### Task 4: `vision-delegation.ts` → read `planner-plugin.json`; update `install.sh` + remove web commands

**Files:**
- Modify: `plugin/vision-delegation.ts`, `install.sh`
- Delete: `web-commands.json`

**Interfaces:**
- Consumes: nothing.
- Produces: `vision-delegation.ts` reads `.opencode/planner-plugin.json` (was `planner.json`). `install.sh` installs `planner.ts` too, stops merging `web-commands.json`, and auto-removes stale `planner-*` entries from the global `config.json`.

- [ ] **Step 1: Update `vision-delegation.ts`**

Change the config path read from `planner.json` to `planner-plugin.json` (also accept legacy `planner.json` as fallback).

- [ ] **Step 2: Update `install.sh`**

- Copy `planner.ts` into `$PLUGINS_DIR` alongside the others.
- Remove the `web-commands.json` merge block.
- Add a python step that removes any `planner-*` keys from the global `config.json` `command` object.
- Update the "Next steps" and file list echo.

- [ ] **Step 3: Delete `web-commands.json`**

- [ ] **Step 4: Update README + docs/planner-plugin.md**

Replace all "writes to BOTH planner.json and opencode.json" text with single-config `planner-plugin.json`; note web commands come from the server plugin now, not `config.json`.

- [ ] **Step 5: Run unit gate**

Run: `python3 tests/e2e/unit_gate.py`
Expected: PASS. (Unit gate references `web-commands.json` only via install — remove those assertions if present.)

- [ ] **Step 6: Commit**

```bash
git add plugin/vision-delegation.ts install.sh docs/planner-plugin.md README.md
git rm web-commands.json
git commit -m "refactor(planner): install planner.ts, drop web-commands.json, single config path everywhere"
```

---

### Task 5: Live E2E — boot a real opencode instance, web commands + screenshot injection

**Files:**
- Create: `tests/e2e/test_planner_live.py`
- Modify: `tests/e2e/unit_gate.py` (register the live suite in a `run_all.sh` style list if one exists; else document the command)

**Interfaces:**
- Consumes: real `opencode serve --port N` on a scratch project; global config at `$HOME/.config/opencode/config.json` already has the `akna` provider (akna-deep / akna-luna). Copy planner plugin files (`planner-core.ts`, `planner.ts`, `planner-tui.ts`, `vision-delegation.ts`) into the scratch `.opencode/plugins/` and copy agents into `.opencode/agent/`. Write `.opencode/planner-plugin.json` with akna models. This mirrors a clean `install.sh` + `/planner-enable` without touching the user's global config.
- Produces: PASS/FAIL assertions for:
  1. Server boots; agents `planner/worker/tester/vision` are registered (via `GET /agent`).
  2. `config` hook set `default_agent=planner` and each agent's model (query `/agent` response or session prompt).
  3. Web command `/planner-status` returns the config text (via `POST /session/{id}/prompt_async` with the command text as a normal prompt, then poll messages).
  4. Web command `/planner-config worker akna/akna-deep` updates `planner-plugin.json` (assert file on disk).
  5. Web command `/planner-vision on` enables vision (assert file + `/agent` shows vision).
  6. Screenshot injection: drop a fake `.png` into `$TMPDIR/opencode-screenshots/`, wait, assert a `FilePart` message appeared in the main session (query `GET /session/{id}/messages` and look for `type: file`).

- [ ] **Step 1: Write the failing live test**

Copy the harness pattern from `python-opencode/tests/e2e/test_mission_flow.py` (`http`, `wait_for_server`, `free_port`, `make_scratch`) but simplify: no git init needed, just a scratch dir with `.opencode/` populated. Use `PORT=4732`. Boot `opencode serve` with `cwd=SCRATCH`.

- [ ] **Step 2: Run it to verify current failure**

Run: `python3 tests/e2e/test_planner_live.py`
Expected: FAIL — `planner.ts` not present yet or config path mismatch (run after Task 2; assert each check).

- [ ] **Step 3: Iterate until all checks pass**

Tune the assertions to the real server behavior. Key detail: `promptAsync` with `noReply: true` for screenshots; verify the `file` part renders. For web-command dispatch, send the raw `/planner-config ...` text via `prompt_async` — the `chat.message` hook replaces the output; poll messages for the result text.

- [ ] **Step 4: Add TUI popup coverage (real TUI over PTY)**

Extend the test or add `tests/e2e/test_planner_tui.py` reusing `python-opencode/tests/e2e/tui_lib.py` patterns: boot the TUI in the scratch project, type `/planner-config`, assert a DialogSelect appears (scrape PTY output for the dialog title "Planner Config" and the option list). This validates the TUI popup path end-to-end.

- [ ] **Step 5: Register both suites in a runner list + commit**

Add `test_planner_live` and `test_planner_tui` to the test list (create `tests/e2e/run_all.sh` if absent).

```bash
git add tests/e2e/test_planner_live.py tests/e2e/test_planner_tui.py tests/e2e/run_all.sh tests/e2e/unit_gate.py
git commit -m "test(planner): live E2E — web commands, config hook, screenshot injection, TUI popup"
```

---

### Task 6: Full install + clean-setup validation

**Files:**
- Modify: `install.sh` (final), `docs/planner-plugin.md`

**Interfaces:**
- Consumes: Task 4 install changes.
- Produces: a repeatable clean install: `./install.sh` installs `planner.ts`, `planner-tui.ts`, `planner-core.ts`, `vision-delegation.ts`, copies agents to the shared root, registers TUI plugin, removes stale `config.json` commands, migrates legacy `planner.json`.

- [ ] **Step 1: Run install on the real machine**

Run: `./install.sh`
Verify: plugins copied to `$HOME/.config/opencode/plugins/`; `config.json` has NO `planner-*` keys; `tui.json` lists `planner-tui.ts`.

- [ ] **Step 2: Clean-setup E2E re-run**

Run: `python3 tests/e2e/test_planner_live.py && python3 tests/e2e/test_planner_tui.py && python3 tests/e2e/unit_gate.py`
Expected: all PASS on the freshly installed plugin files.

- [ ] **Step 3: Uninstall + reinstall round-trip**

Run: `./install.sh uninstall && ./install.sh`
Verify: no stale files; reinstall works; `config.json` still clean.

- [ ] **Step 4: Commit**

```bash
git add install.sh docs/planner-plugin.md
git commit -m "chore(planner): clean install + uninstall round-trip validated"
```

---

## Self-Review Notes

- **Spec coverage:** single config → Task 1 (rename/drop sync) + Task 4 (vision-delegation path); TUI popups-only + no TUI text duplicates → Task 2 (chat.message web path, no config.command) + Task 4 (remove web-commands.json) + Task 5 (TUI PTY test); web commands → Task 2 chat.message + Task 5 live test; screenshots inline in temp dir → Task 2 (dir-watch + tool.execute.after + promptAsync noReply) + Task 3 (agent prompts) + Task 5; clean install → Task 6.
- **Placeholder scan:** Task 2's planner.ts shows a skeleton + explicit "implement fully" — this is a full-implementation requirement; the reference code is present for the tricky parts (filePart, injectScreenshot, sweep). The remaining hooks are described precisely enough to write without ambiguity.
- **Type consistency:** `plannerConfigPath`, `migrateLegacyConfig`, `readPlannerConfig`, `setAgentModel`, `setVisionEnabled/Model`, `isPlannerEnabled`, `PLANNER_AGENTS`, `parseModelId` used identically across Tasks 1, 2, 4, 5. `promptAsync`/`noReply` matches the SDK `SessionPromptData`. `parentID` matches the SDK `Session` type.
- **E2E scope:** Task 5 covers web commands + config hook + screenshot injection on a real instance (akna-deep/akna-luna), and TUI popup via PTY. Task 6 covers the clean install path.
