# planner

**planner** is a free, open-source (MIT) multi-agent plugin for **OpenCode 2**
— an **opt-in** planner agent that coordinates four subagents in one session:

- **planner** — a primary agent (reachable with **Tab** or `/plan`). It controls
  the whole flow — brainstorm → spec check → worker → result check → tester —
  and writes no code itself.
- **brainstormer** — subagent that runs BEFORE the work on non-trivial requests:
  it reads the superpowers `brainstorming`/`writing-plans` skills and writes an
  implementation-ready spec to `docs/superpowers/specs/<date>-<slug>.md`.
  **Fully autonomous**: it never asks questions — ambiguity is resolved with
  documented assumptions, so nothing blocks waiting for input.
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

Each subagent can use a **different LLM model** with an optional per-agent
**effort variant** (`provider/model#high`). Images are handled three ways: by a
dedicated vision subagent (**agent**), by an upstream proxy vision bridge
(**proxy**), or by the agents' own multimodal models (**native**).

Requires OpenCode 2.x. (OpenCode V1 support ended with v0.3.0; it remains in
git history.)

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
  **brainstormer** (spec, non-trivial work) → the planner **reads and checks the
  spec itself** → **worker** (implementation) → the planner **checks the
  changed files** → **tester** (verification). On `STATUS: FAIL` it re-delegates
  to the worker with the feedback, up to `maxCycles` times.
- The brainstormer never asks you anything: it resolves ambiguity with
  documented assumptions in the spec, so the flow never blocks.
- Configuration is per project and lives in one file:
  `.opencode/planner-plugin.json`.

### Commands

- **`/plan <request>`** — switches the session to Planner and sends the request.
- **`/planner-config`** — **opens the configuration dialog in the TUI**:
  brainstormer / worker / tester / vision model + effort variant, and max
  parallel subagents. It is provided by the TUI plugin, so typing it in the
  terminal always pops the dialog (no duplicate text command).
- Vision and parallelism also have `ctrl+p` palette entries
  (`Planner: configure vision`, `Planner: subagent parallelism`).

The server registers no planner command besides `/plan`. Outside the TUI,
configuration lives in `.opencode/planner-plugin.json` (see below).

### Example config

```json
{
  "models": { "worker": "goproxy/glm-5.3-flash", "tester": "goproxy/deepseek-v4-flash" },
  "variants": { "worker": "high" },
  "maxCycles": 3,
  "maxParallelSubagents": 1,
  "vision": { "enabled": true, "model": "goproxy/deepseek-v4-flash-vision-exp", "mode": "agent" }
}
```

- `maxParallelSubagents` (default **1 — strictly serial**): how many subagent
  tasks may run at once. Keep it at 1 for single-slot local model servers; raise
  it only if your server handles concurrency. The value is injected into the
  planner's system prompt (planner requests only).
- With no config file nothing is pinned: subagents inherit the session's main
  model.

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
python3 tests/e2e/unit_gate.py            # static gates (layout, contracts, installer)
python3 tests/e2e/test_planner_config.py  # installer sandbox + config contract
python3 tests/e2e/test_planner_v2.py      # real OpenCode 2 server: agents, opt-in, commands, TUI dialogs
bash    tests/e2e/run_all.sh              # the three suites above

# Long real-model validation (needs a live provider; takes minutes):
python3 tests/e2e/long_session_test.py --label A --cycles 2
python3 tests/e2e/long_session_test.py --task mario --label M --cycles 3   # Mario card game

# Live model switch with NO opencode restart, verified through the GoProxy
# dashboard request log (credentials via env, never committed):
GOPROXY_UI_PASSWORD=... python3 tests/e2e/test_model_switch_live.py
```

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
  index.ts      V2 server plugin: agent install, transforms, hooks, commands
  core.ts       config schema/IO (atomic), model specs, agent templates, vision instructions
  commands.ts   dialog operations shared by core and the TUI
  tui.ts        V2 CLI plugin: /planner-config dialog + palette entries (package "./tui" export)
index.ts tui.ts root re-exports for plugin-directory discovery
agents-v2/      V2 agent definitions (planner/brainstormer/worker/tester/vision)
tests/e2e/      Python suites
install.sh      global installer/uninstaller (V2)
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
