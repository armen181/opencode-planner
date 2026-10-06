# Planner Plugin (OpenCode 2) — internals

The plugin is an **opt-in** multi-agent orchestrator: `planner` (primary) delegates to
`brainstormer` / `worker` / `tester` (and optionally `vision`) subagents. Normal sessions are
untouched; the plugin injects nothing unless the request is running the `planner` agent.

## Surfaces

| Surface | File | What it registers |
|---|---|---|
| Server plugin | `src/index.ts` (package `.` / directory `index.ts`) | agent install, agent/command transforms, `session.context` hook, shell/tool hooks, config watcher |
| CLI (TUI) plugin | `src/tui.ts` (package `./tui` / directory `tui.ts`) | `/planner-config` opens the configuration dialog; vision + parallelism are `ctrl+p` palette entries. The server registers no planner command besides `/plan`, so no name appears twice |
| Shared logic | `src/core.ts`, `src/commands.ts` | config schema/IO, model specs, agent templates, command texts |

The plugin directory layout (`index.ts` + `tui.ts` together) is what makes OpenCode load both
halves. `Plugin.define` is an identity helper, so the plugin has **no runtime dependency** on
`@opencode/plugin` (type-only imports; the package is a devDependency) and needs no `npm install`
in discovered installs.

## Configuration

One file per project: `.opencode/planner-plugin.json` (atomic writes preserve unknown keys):

```json
{
  "models": { "worker": "", "tester": "", "brainstormer": "" },
  "variants": { "worker": "high" },
  "maxCycles": 3,
  "maxParallelSubagents": 1,
  "screenshotNotes": false,
  "skillsPath": "/home/armen/.kimi-code/plugins/managed/superpowers/skills",
  "vision": { "enabled": false, "model": "", "mode": "agent" }
}
```

- `models.planner` is legacy and ignored — the planner follows `/model` (the session's model).
- `variants` holds the `#effort` suffix separately from the model id.
- MCP servers are **not** part of this file: OpenCode manages them natively
  (`/mcps`, the `mcp` section of `opencode.json`). A stale `mcp` key from
  older plugin versions is removed on the next write.

## Hooks and gating

- `ctx.agent.transform` — applies pinned models + variants to `brainstormer`/`worker`/`tester`
  (and `vision` in agent mode). Never sets a default agent and never pins `planner`.
- `ctx.command.transform` — registers `/plan` (switch to Planner + send). All
  configuration happens through the TUI dialogs, which write the config file;
  the watcher below re-reads it.
- `ctx.session.hook("context")` — **early-returns unless `event.agent === "planner"`**; pushes
  ONE system block with the cycle limit, the serial/parallel rule, and the vision-mode
  instruction (skipped when the planner model can already see images).
- `ctx.shell.hook("create.before")` — exports `OPENCODE_SCREENSHOT_DIR`.
- `ctx.tool.hook("execute.after")` — optional `Screenshot captured:` notes
  (`screenshotNotes: true`; off by default).
- mtime watcher (1.5 s) — re-reads the config and calls `ctx.agent.reload()`.
- `ctx.event.subscribe` — tracks busy sessions for the reply retry.

MCP servers are deliberately not touched (no `ctx.mcp` usage): the native `/mcps`
command and the `mcp` section of `opencode.json` own them.

## Agent definitions

`agents-v2/` holds the V2 markdown definitions (`mode: primary/subagent`, `permissions:` rules).
On setup the plugin copies **missing** files into `<project>/.opencode/agents/` and creates
`docs/superpowers/specs/` for the brainstormer. Existing files are never overwritten; the
global installer writes the same templates into `~/.config/opencode/agents/`.

## Screenshots

`$OPENCODE_SCREENSHOT_DIR` points at `/tmp/opencode-screenshots/<project>-<hash>/`. Nothing is
posted unless `screenshotNotes` is enabled; files merely referenced in tool output that live in
the shared dir are ignored to avoid cross-session attribution.

## Tests

- `unit_gate.py` — layout/contracts/installer (static)
- `test_planner_config.py` — installer sandbox + config contract
- `test_planner_v2.py` — real OpenCode 2 server: plugin/agents/opt-in/MCP/commands + TUI dialog probe
- `long_session_test.py` — real model: normal session + planner worker→tester cycles
