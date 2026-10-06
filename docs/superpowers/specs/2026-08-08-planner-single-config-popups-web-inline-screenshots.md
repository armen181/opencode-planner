# Planner Plugin — Single Config, TUI Popups / Web Commands, Inline Screenshots

> Date: 2026-08-08
> Project: `/home/armen/IdeaProjects/opencode-planner`
> Status: Approved design (pending implementation plan)

## Problem

The planner plugin (planner → worker → tester → vision) works, but has three issues:

1. **Config duplication.** `planner-core.ts` writes per-agent models to BOTH
   `.opencode/planner.json` AND `opencode.json` agent entries
   (`syncOpencodeConfig`). Two files must stay in sync; they can diverge.

2. **TUI command duplication.** The `planner-*` commands exist in TWO registries:
   - `web-commands.json` → merged into global `~/.config/opencode/config.json`
     as **text commands**. The TUI also loads these (`sync.data.command`), so
     typing `/planner-config` in the TUI runs the text-template command, not the
     popup.
   - `planner-tui.ts` `api.command.register` → the **popup** commands.
   Net: in the TUI the same slash name triggers two different behaviors.

3. **Screenshots are hidden in subagent sessions.** When the worker validates a
   UI change by taking a screenshot, the image renders inside the worker's
   subagent session (inside the card). The user must enter the subagent session
   or download the file to see it — they want it inline in the main session,
   clickable to enlarge.

## Goals

- ONE configuration source of truth for the planner plugin.
- In the TUI: configuration via **popup dialogs** (select, don't type).
- In the web UI: configuration via **slash/text commands** (no popups exist there).
- No duplicate slash-command behavior in any single surface.
- Screenshots (from anywhere: browser, Android emulator, app) surface **inline in
  the main session**, clickable to enlarge, stored in the OS temp dir with TTL cleanup.

## Non-goals

- No missions, no task-store, no server loop, no worktrees/gates/mirror/ledger
  (unchanged from current planner model — planner is the default agent of the main
  session and delegates via the native `task` tool).
- No changes to the four agent names or the delegation flow.

---

## Section 1 — Single config source: `planner-plugin.json` only

- Rename `.opencode/planner.json` → `.opencode/planner-plugin.json`.
- It is the ONLY config file. Shape (unchanged from today):
  ```json
  {
    "enabled": true,
    "models": { "planner": "akna/akna-deep", "worker": "akna/akna-deep", "tester": "akna/akna-deep" },
    "maxCycles": 3,
    "vision": { "enabled": true, "model": "akna/akna-luna" }
  }
  ```
- **Delete** `syncOpencodeConfig` and every write to `opencode.json` from the
  plugin (enable/disable/config no longer touch `opencode.json` agent entries or
  `default_agent`).
- **New server plugin `planner.ts`** whose `config` hook reads
  `planner-plugin.json` and injects the model + default agent at load time:
  - `config.default_agent = "planner"`
  - `config.agent.planner.model = <models.planner>`
  - `config.agent.worker.model = <models.worker>`
  - `config.agent.tester.model = <models.tester>`
  - if `vision.enabled`: `config.agent.vision = { model: <vision.model>, mode: "subagent" }`
  - This is the same mechanism the orchestrator uses to register `/team`
    (`plugin/orchestrator.ts` `config` hook). One authoritative file, applied at
    load, works in BOTH web and TUI. The `.md` agent files still need to exist in
    `.opencode/agent/` (enable/disable copies them) so opencode can load the
    agents; the config hook only sets models + default agent.
- `planner-core.ts` helpers updated: paths renamed, `syncOpencodeConfig` removed,
  `enablePlanner`/`disablePlanner`/`setAgentModel`/`setVision*` write only
  `planner-plugin.json`.

## Section 2 — TUI popups vs web commands (no duplication)

### TUI = popups

- `planner-tui.ts` keeps `/planner-enable`, `/planner-disable`, `/planner-config`,
  `/planner-status`, `/planner-vision` as **popup dialogs** (agent select →
  model select from discovered models → confirm). Unchanged UX.

### Remove text-command duplication

- **Stop** merging `web-commands.json` into global `config.json` (`install.sh`
  step removed).
- **Remove** the existing `planner-*` entries from the installed global
  `~/.config/opencode/config.json`.
- This makes the names disappear from the TUI's text-command path — the popup is
  the only behavior in the TUI.

### Web = text commands

- The new server plugin `planner.ts` gets a `chat.message` hook (same pattern as
  the orchestrator's `/team`) handling:
  - `/planner-enable`
  - `/planner-disable`
  - `/planner-config <agent> <model>` (or show current models with no args)
  - `/planner-status`
  - `/planner-vision on|off`
- In web there are no popups, so these are the config interface there.
- In the TUI the popup intercepts those names locally, so the server hook only
  fires in web (and in headless/CLI sessions).

### Single code path

- Both the TUI popups and the web commands call the SAME `planner-core.ts`
  functions (`setAgentModel`, `enablePlanner`, `disablePlanner`, `setVisionEnabled`,
  `setVisionModel`, `readPlannerConfig`). One config file, no divergent logic.

## Section 3 — Screenshots surfaced inline, stored in temp

- **Agents know where to screenshot.** The agent `.md` files (planner/worker/tester)
  get an instruction: when a task needs visual validation, capture a screenshot of
  the target (browser via playwright/puppeteer, Android via `adb exec-out
  screencap`, etc.) and save it to the shared screenshot dir. The main session
  (planner) decides the source; the worker just captures and saves.
- **Shared temp screenshot dir.** `$TMPDIR/opencode-screenshots/` (OS temp — wiped
  on reboot, never project storage). Workers save captures there as `.png`.
- **Plugin watches the dir.** `planner.ts` (server) watches/polls the temp dir for
  new `.png` files. On a new capture it injects a `FilePart` into the **parent
  (main) session**:
  ```ts
  { type: "file", mime: "image/png", filename, url: "data:image/png;base64,..." }
  ```
  posted via `session.promptAsync({ path: { id: mainSessionID }, body: { parts: [filePart], noReply: true } })`
  — a non-interactive part that renders the image inline (clickable to enlarge)
  in both TUI and web, without triggering a new model turn.
- **Parent-session resolution.** Because worker/tester are subagents of the main
  session (`parent_id` = main session), the plugin resolves the parent session id
  (e.g. from the `session.created` event / subagent relationship) and injects there.
- **Storage hygiene.** Temp dir by nature; the plugin deletes captures older than
  a TTL (e.g. 24h) on each new capture, so it never fills disk.

---

## Data flow

```
                 ┌────────────────────────────────────────────┐
                 │  planner.ts (SERVER plugin, global)        │
   load ───────▶ │  config hook: read planner-plugin.json →   │
                 │  inject agent models + default_agent       │
                 │                                            │
   web:          │  chat.message hook: /planner-* text cmds   │
                 │  → planner-core.ts functions               │
                 │                                            │
   screenshot:   │  dir-watch $TMPDIR/opencode-screenshots/   │
                 │  → inject FilePart into parent session     │
                 └────────────────────────────────────────────┘

                 ┌────────────────────────────────────────────┐
                 │  planner-tui.ts (TUI plugin, global)       │
   TUI:          │  api.command.register: /planner-* popups   │
                 │  → planner-core.ts functions               │
                 └────────────────────────────────────────────┘

                 ┌────────────────────────────────────────────┐
                 │  planner-core.ts (shared)                  │
                 │  read/write .opencode/planner-plugin.json  │
                 └────────────────────────────────────────────┘
```

## Files touched

- `plugin/planner-core.ts` — rename config path, drop `syncOpencodeConfig`
  + `opencodeConfigPath`, keep all setters writing only `planner-plugin.json`.
- `plugin/planner-tui.ts` — no functional change (already popups); remove any
  residual text-command path if present.
- `plugin/planner.ts` — NEW server plugin: `config` hook (model injection) +
  `chat.message` hook (web commands) + screenshot dir watch → parent-session
  `FilePart` injection.
- `agents/planner.md`, `agents/worker.md`, `agents/tester.md` — add
  "capture a screenshot to `$TMPDIR/opencode-screenshots/`" guidance.
- `install.sh` — remove the `web-commands.json` → `config.json` merge step;
  install `planner.ts` too.
- `web-commands.json` — delete (no longer deployed; web commands now come from the
  `planner.ts` `chat.message` hook).
- `docs/planner-plugin.md` — document single config, popups-in-TUI,
  commands-in-web, inline screenshots.
- `tests/e2e/test_planner_config.py` — update to single-file assertions; add a
  case for the `config` hook injecting models (mirror TS logic in Python).
- `.gitignore` — add `/tests/e2e/__pycache__/` if needed.

## Migration

- On first run after upgrade, if `.opencode/planner.json` exists but
  `planner-plugin.json` does not, the plugin migrates it: copy contents to the new
  name, then remove the old file. Reads always prefer the new name.
- `install.sh` automatically removes any pre-installed `planner-*` entries from the
  global `~/.config/opencode/config.json` `command` object (same python merge
  helper it already uses, inverted), so stale text commands never persist after
  upgrade.

## Testing

- `tests/e2e/unit_gate.py` — passes (plugin files compile/load).
- `tests/e2e/test_planner_config.py` — asserts only `planner-plugin.json` exists,
  model set writes one file, `opencode.json` untouched.
- Manual TUI: `/planner-config` opens popup; `/planner-status` popup; no text
  template runs.
- Manual web: `/planner-config worker akna/akna-deep` works as a text command.
- Screenshot: run a worker task that saves a `.png` to the temp dir → image
  appears inline in the main session; stale files cleaned after TTL.
