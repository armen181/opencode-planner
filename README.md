# planner

**planner** is a free, open-source (MIT) multi-agent plugin for **OpenCode 2**
— an **opt-in** planner agent that coordinates four subagents in one session:

- **planner** — a primary agent (reachable with **Tab** or `/plan`). It controls
  the whole flow — brainstorm → spec check → worker → result check → tester —
  and writes no code itself.
- **brainstormer** — subagent that runs BEFORE the work on every request: it uses
  the superpowers `brainstorming`/`writing-plans` skills on the first try, writes
  an implementation-ready spec to `docs/superpowers/specs/<date>-<slug>.md` (and
  verifies the file on disk — a spec that is not on disk is reported as failed,
  never READY). It may ask clarifying questions: they are relayed to you by the
  planner (and answered back to it, max 3 rounds); if you do not answer, the
  ambiguity is resolved with documented assumptions.
- **worker** — subagent that implements the task (and reads the spec first, when
  one exists).
- **tester** — subagent that verifies the implementation and reports
  `STATUS: PASS|FAIL`.
- **vision** — optional subagent that processes image requests (enable + pick a
  vision-capable model).

The planner is **opt-in**: normal sessions (Build/Plan/…) are completely
untouched — no planner prompt is injected and no default agent is changed.
MCP servers are managed by OpenCode itself (the native `/mcps` command and the
`mcp` section of `opencode.json(c)`); this plugin never touches them.

The workflow is **enforced, not advisory**: the brainstormer runs FIRST for
every request (a turn that skips delegation is resumed by the plugin, max 2
nudges), the `maxParallelSubagents` cap is a real semaphore around the
`subagent` tool, and every project gets its own configuration snapshot seeded
from global defaults. New versions are detected (GitHub, cached 24 h) and can
be applied with one click from `/planner-status` (`git pull` + `install.sh`).

Each subagent can use a **different LLM model** with an optional per-agent
**effort variant** (`provider/model#high`). Images are handled three ways: by a
dedicated vision subagent (**agent**), by an upstream proxy vision bridge
(**proxy**), or by the agents' own multimodal models (**native**).

Requires OpenCode 2.x. (OpenCode V1 support ended with v0.3.0; it remains in
git history.)

---

## Configuration model

- **Global defaults** live in `<opencode config dir>/planner-defaults.json`
  (`OPENCODE_CONFIG_DIR` or `~/.config/opencode`). Created on first load and by
  `install.sh`; never overwritten afterwards. Hand-editable, and editable
  through `/planner-config → Global defaults (new projects)`.
- **Per-project snapshot**: the first time the planner is used in a project,
  the current defaults are copied into `<project>/.opencode/planner-plugin.json`
  with `plannerVersion`, `createdAt` and `source` metadata. Later default
  edits do not touch existing projects; `/planner-config → Re-sync from
  defaults` re-pulls them on demand.
- **Effective config** = project file over global defaults over built-ins.
  Unknown keys are always preserved.

## Versioning & updates

- Project configs and agent definitions carry version stamps; a project-local
  plugin copy (`.opencode/plugins/opencode-planner`) is detected too.
- **`/planner-status`** (TUI dialog; palette entry "Planner: status & updates")
  lists the running/install/config/agent/local-copy versions, the latest
  version on GitHub (read-only check, cached 24 h, silent offline) and any
  conflicts. Actions: **Check for updates**, **Refresh agent definitions**
  (updates only files the plugin wrote — user-edited files are kept), and
  **Update now** (confirmation → `git pull --ff-only` + `install.sh --yes`
  from the source clone recorded at install time → restart notice; a failed
  pull aborts instead of reinstalling a stale tree).
- A one-time startup toast warns about an available update or version
  conflicts. Set `PLANNER_NO_UPDATE_CHECK=1` to disable the automatic startup
  check (the manual "Check for updates" button still works).

---

## Install

### Global (recommended)

```bash
./install.sh          # interactive; --yes for non-interactive
```

The installer:

- copies the plugin as a **directory** into
  `~/.config/opencode/plugins/opencode-planner/` (`index.ts`, `tui.ts`,
  `package.json`, `src/`, `agents-v2/`) — a plugin directory keeps the server
  and TUI entrypoints together, so OpenCode loads **both** the agent behavior
  and the planner dialogs;
- installs the five global V2 agent definitions into
  `~/.config/opencode/agents/` (only missing files — your edits are kept);
- removes legacy single-file copies (`plugins/planner.ts`), which can only load
  the server half and therefore caused "no dialogs";
- supports `uninstall` and prints the next steps.

Restart OpenCode once afterwards.

### Per project

Copy the package into the project (or add the path to `opencode.json(c)`):

```bash
mkdir -p .opencode/plugins/opencode-planner
cp -R src agents-v2 index.ts tui.ts package.json .opencode/plugins/opencode-planner/
```

```jsonc
// opencode.jsonc
{ "plugins": ["./.opencode/plugins/opencode-planner"] }
```

The plugin has **zero runtime dependencies** — nothing to `npm install`; the
per-project agent definitions are installed automatically on first load.

### npm package

Once published, add the package to `opencode.json(c)`:

```jsonc
{ "plugins": ["@armen181/opencode-planner"] }
```

The CLI loads the package's `./tui` entrypoint automatically. Add the same
package to the global `cli.json` `plugins` array only if you want the dialogs
when connected to a **remote** server (CLI-only plugins list).

---

## Usage

- Press **Tab** to switch to the **Planner** agent in a session (it is not the
  default), or type `/plan <request>` to switch and send in one step.
- Type a request; the planner controls the flow step by step:
  **brainstormer** (ALWAYS first — enforced) → the planner **reads and checks
  the spec itself** → **worker** (implementation) → the planner **checks the
  changed files** → **tester** (verification). On `STATUS: FAIL` it re-delegates
  to the worker with the feedback, up to `maxCycles` times. A turn that ends
  without delegation is resumed by the plugin's watchdog.
- The brainstormer writes and verifies its spec file itself; if something is
  genuinely ambiguous it asks you through the planner (max 3 question rounds) —
  every decision question comes with **2-4 concrete options to pick from**
  (the recommended one first, plus a free-text answer field), never a
  yes/no flattening of an open choice. No answer means the assumption is
  documented in the spec instead.
- The planner never builds the request itself: file writes by the planner are
  denied server-side, and the watchdog also resumes turns where the
  brainstormer came back without a usable spec (previously the planner would
  "helpfully" write the spec itself).
- Small local models that hallucinate tool names (`bash`, `ask_user`, `task`,
  `read_file`, `list_dir`, …) are covered by the globally installed
  `tool-name-aliases.js` helper (shipped in `extras/`, installed by
  `install.sh`): aliases map onto the canonical OpenCode tools, arguments are
  normalized, and an alias is never registered for a tool that does not exist.
- Configuration is per project: `.opencode/planner-plugin.json`, snapshotted
  from the global `planner-defaults.json` on first use.

### Commands

- **`/plan <request>`** — switches the session to Planner and sends the request.
- **`/planner-config`** — **opens the configuration dialog in the TUI**:
  brainstormer / worker / tester / vision model + effort variant, max parallel
  subagents, max worker→tester cycles, delegation enforcement, **Re-sync from
  defaults** and **Global defaults (new projects)**.
- **`/planner-status`** — version/conflict overview with **Check for updates**,
  **Refresh agent definitions** and **Update now** (pull + reinstall + restart
  notice).
- Vision and parallelism also have `ctrl+p` palette entries
  (`Planner: configure vision`, `Planner: subagent parallelism`,
  `Planner: status & updates`).

The server registers no planner command besides `/plan`. Outside the TUI,
configuration lives in `.opencode/planner-plugin.json` (seeded from
`planner-defaults.json`; see below).

### Example config

```json
{
  "models": { "worker": "goproxy/glm-5.3-flash", "tester": "goproxy/deepseek-v4-flash" },
  "variants": { "worker": "high" },
  "maxCycles": 3,
  "maxParallelSubagents": 1,
  "enforceDelegation": true,
  "vision": { "enabled": true, "model": "goproxy/deepseek-v4-flash-vision-exp", "mode": "agent" },
  "plannerVersion": "0.5.0",
  "createdAt": "2026-10-10T12:00:00.000Z",
  "source": "defaults"
}
```

- `maxParallelSubagents` (default **1 — strictly serial**): **enforced** — a
  semaphore around the `subagent` tool queues extra calls; 1 means one subagent
  task at a time. Raise it only if your server handles concurrency.
- `enforceDelegation` (default **true**): the watchdog resumes planner turns
  that skip the subagents (max 2 nudges per turn).
- The `plannerVersion`/`createdAt`/`source` keys are written by the plugin —
  edit only if you know what you are doing.
- With no config file, the effective settings are the global defaults;
  subagents inherit the session's main model unless a model is set.

---

## MCP servers

MCP servers are OpenCode's own concern: enable/disable/reconnect them with the
native **`/mcps`** command in the TUI, or the `mcp` section of
`opencode.json(c)`. The planner plugin never disables, enables, or otherwise
manages MCP servers.

## How the opt-in gating works

- The plugin never sets a default agent — OpenCode's default (Build) stays.
- The planner instructions (cycle limit, serial/parallel rule, vision mode) are
  injected through the `session.context` hook **only when the request's agent is
  `planner`**. No other agent, and no normal session, receives planner text.
- No prompts or model calls are made at plugin load.

## Vision modes

- **`agent`** (default) — the planner delegates image work to the `vision`
  subagent (multimodal model). The plugin tells vision-less planner models to
  delegate.
- **`proxy`** — an upstream proxy (e.g. a GoProxy vision bridge) transcribes
  image parts into fenced `<<<IMAGE EVIDENCE>>>` text in-flight; agents simply
  `Read` the image file.
- **`native`** — the agents' own models are image-capable; they `Read` images
  directly.

Requirements per mode: **agent** — the vision model accepts image input;
**proxy**/**native** — the agents' models must be declared image-capable in the
provider config (`modalities.input` includes `"image"`).

## Live config reload

Edits from the TUI dialogs, the commands, or by hand are picked up by a file
watcher (≈1.5 s) that re-reads the config and triggers an agent reload — no
restart, no session disposal.

## Screenshots

Agents are told to save captures to `$OPENCODE_SCREENSHOT_DIR` (a per-project
temp dir). Nothing is posted by default; set `"screenshotNotes": true` to get a
one-line `Screenshot captured: <path>` note in the owning session.

---

## Tests

```bash
bash    tests/e2e/run_all.sh              # the five suites below together
python3 tests/e2e/unit_gate.py            # static gates (layout, contracts, installer)
python3 tests/e2e/test_semaphore_unit.py  # subagent-cap semaphore logic
python3 tests/e2e/test_update_unit.py     # version compare, status detection, agent refresh, update execution (fake clones)
python3 tests/e2e/test_planner_config.py  # installer sandbox + defaults + markers + config contract
python3 tests/e2e/test_planner_v2.py      # real OpenCode 2 server: agents, opt-in, snapshot, TUI dialogs

# Long real-model validation (needs a live provider; takes minutes):
python3 -u tests/e2e/test_subagent_limit_live.py   # concurrency cap on a real instance (two simultaneous sessions)
python3 -u tests/e2e/test_delegation_live.py       # watchdog: nudge, resume, cap, toggle-off
python3 -u tests/e2e/test_agent_models_live.py     # per-agent pins actually run (incl. missing project agent files)
python3 -u tests/e2e/test_spec_write_live.py       # brainstormer writes + verifies the spec file, pipeline runs
python3 -u tests/e2e/test_question_live.py         # question round: decision questions carry REAL options (never forced yes/no)
python3 tests/e2e/long_session_test.py --label A --cycles 2
python3 tests/e2e/long_session_test.py --task mario --label M --cycles 3   # Mario card game

# Live model switch with NO opencode restart, verified through the GoProxy
# dashboard request log (credentials via env, never committed):
GOPROXY_UI_PASSWORD=... python3 tests/e2e/test_model_switch_live.py
```

The live suites read the plugin's debug log via `PLANNER_DEBUG=1` +
`PLANNER_DEBUG_FILE=<path>` (server stdout is buffered).

The long test's `--task mario` builds a Mario-themed card game over several
worker→tester cycles (brainstormer spec → 3 cycles), verified without a browser
(`index.html`/`styles.css`/`game.js`, `node --check game.js`, required game
logic). The model-switch test boots one server, runs a tester cycle on model A,
changes the tester to model B **without restarting**, runs another cycle, and
proves via the dashboard that the requests really moved from A to B.

`test_planner_v2.py` skips itself when no V2 binary is available
(`PLANNER_V2_BIN` overrides). The live suites use a private `HOME`, so your
global OpenCode setup cannot leak into the results.

## Layout

```
src/
  index.ts      V2 server plugin: agent install, transforms, hooks, watchdog, commands
  core.ts       config schema/IO (atomic), global defaults + snapshots, agent markers,
                version compare/status, update check + execution, model specs, templates
  semaphore.ts  async semaphore behind the enforced maxParallelSubagents cap
  commands.ts   dialog operations shared by core and the TUI
  tui.ts        V2 CLI plugin: /planner-config + /planner-status dialogs, palette entries
index.ts tui.ts root re-exports for plugin-directory discovery
agents-v2/      V2 agent definitions (planner/brainstormer/worker/tester/vision)
tests/e2e/      Python + TS-harness suites (see Tests)
install.sh      global installer/uninstaller (V2; records sourceRoot, markers, defaults)
docs/planner-plugin.md
```

## Troubleshooting

- **Plugin not loaded** — check `opencode api plugin.list` for `planner`. A
  discovered plugin directory must contain `index.ts` (server) and `tui.ts`
  (CLI); a bare `.ts` file loads only the server half.
- **TUI dialogs** — type `/planner-config` for the configuration dialog, or
  press **ctrl+p** for the direct palette entries (`Planner: configure vision`,
  `Planner: subagent parallelism`).
- **Agents missing** — the definitions are installed into
  `.opencode/agents/` on first load; a read-only project keeps them out, copy
  them manually from `agents-v2/`.
- **MCP servers** — managed natively (`/mcps`, `opencode.json` `mcp`); the
  planner plugin does not touch them.
- **Pinned model not applied** — check `provider/model#variant` spelling and
  that the agent definition exists; `/planner-config` shows the current values.

## License

[MIT](LICENSE) — free to use, modify, and distribute.
