# Planner Plugin (OpenCode 2) — internals

The plugin is an **opt-in** multi-agent orchestrator: `planner` (primary) delegates to
`brainstormer` / `worker` / `tester` (and optionally `vision`) subagents. Normal sessions are
untouched; the plugin injects nothing unless the request is running the `planner` agent.

## Surfaces

| Surface | File | What it registers |
|---|---|---|
| Server plugin | `src/index.ts` (package `.` / directory `index.ts`) | agent install, agent/command transforms, `subagent` tool wrapper (concurrency cap), `session.context` hook, `session.prompt` hook + event subscription (delegation watchdog), permission fallback, shell/tool hooks, config watcher |
| CLI (TUI) plugin | `src/tui.ts` (package `./tui` / directory `tui.ts`) | `/planner-config` (models/effort, cycles, parallelism, delegation enforcement, re-sync, global defaults) and `/planner-status` (versions, check/update/refresh agents); palette entries |
| Shared logic | `src/core.ts`, `src/commands.ts`, `src/semaphore.ts` | config schema/IO, defaults + snapshots, agent markers, version status + update execution, model specs, agent templates, gate |

The plugin directory layout (`index.ts` + `tui.ts` together) is what makes OpenCode load both
halves. `Plugin.define` is an identity helper, so the plugin has **no runtime dependency** on
`@opencode/plugin` (type-only imports; the package is a devDependency) and needs no `npm install`
in discovered installs.

## Configuration

Two files, one schema:

- **Global defaults** — `<configDir>/planner-defaults.json`
  (`OPENCODE_CONFIG_DIR` → `XDG_CONFIG_HOME/opencode` → `~/.config/opencode`). Created on first
  plugin load and by `install.sh`; never overwritten. Settings only — no project metadata.
- **Project snapshot** — `<project>/.opencode/planner-plugin.json`, materialized by the **first
  planner use** (planner-agent context hook or `/plan`) via an atomic create-or-fail write, with
  `plannerVersion`, `createdAt`, `updatedAt`, `source` metadata. Project writes preserve unknown
  keys and stamp `plannerVersion`/`updatedAt`.

Effective config = project file over defaults over built-ins (per key; `variants` is
override-if-present). Schema: `models` (brainstormer/worker/tester; planner follows `/model`),
`variants`, `maxCycles`, `maxParallelSubagents`, `enforceDelegation`, `screenshotNotes`,
`skillsPath`, `vision { enabled, model, variant, mode }`. MCP servers are **not** part of this
file (native `/mcps` owns them; a stale `mcp` key is dropped on write).

## Hooks and enforcement

- `ctx.tool.transform` — wraps the delegation tool's `execute` with a **semaphore**
  (`src/semaphore.ts`); `maxParallelSubagents` extra calls queue until a slot frees. The tool is
  resolved by name — `subagent`, falling back to `task` on builds that name it that way (an alias
  delegating into the wrapped canonical must never double-acquire). The wrapper is
  idempotent (`__plannerGate` marker) and scoped to planner-originated calls. A permission hook
  (`evaluate`) denies over-cap calls as a fallback when the tool cannot be wrapped. The resolved
  name (`delegationToolName`) is what every injected instruction, nudge, and deny message tells
  the model to call — models are never pointed at a tool name that does not exist on the build.
- **Planner write deny** — the `evaluate` permission hook denies `edit`/`write`/`patch` actions for
  the `planner` agent server-side ("delegate this to the subagents"), independent of how the agent
  definition was loaded.
- **Delegation watchdog** — `session.hook("prompt")` resets per-turn counters; `tool hooks` and
  the event stream (`session.execution.*`, turn-settle timer) track COMPLETED subagent calls; a
  turn that ends with none, with only a READY brainstormer spec and no worker/tester, with an
  unusable brainstormer verdict (NEEDS_INPUT/FAILED) and nothing delegated, or after the planner
  wrote files itself is resumed with a bounded `session.synthetic({ resume: true })` nudge (max 2
  per turn). A turn that relayed questions to the user (`question` tool completed) is a legitimate
  wait and is never nudged. Disabled by `enforceDelegation: false` or when the subagent definitions
  are missing. Hooks accept both delegation tool names (`subagent`/`task`).
- `ctx.agent.transform` — applies pinned models + variants to `brainstormer`/`worker`/`tester`
  (and `vision` in agent mode). Pinning is **not gated on project agent files** (an agent can come from
  the project, the global agents dir, or the opencode config), and `execute.before` **strips per-call
  `model` overrides** from `subagent` calls — the configured models are authoritative. Never sets a
  default agent and never pins `planner`.
- `ensureProjectSnapshot()` (planner context hook + `/plan`) also guarantees
  `docs/superpowers/specs/` exists — the file tool never creates parent directories, so the
  brainstormer can always write its spec; the planner then reads the file back before the worker.
- `ctx.command.transform` — registers `/plan` (snapshot + switch to Planner + send).
- `ctx.session.hook("context")` — **early-returns unless `event.agent === "planner"`**; pushes ONE
  system block with the cycle limit, the mandatory-delegation contract, the enforced
  serial/parallel rule, and the vision-mode instruction.
- `ctx.shell.hook("create.before")` — exports `OPENCODE_SCREENSHOT_DIR`.
- `ctx.tool.hook("execute.before"/"execute.after")` — watchdog attempts/successes, fallback-cap
  bookkeeping, and the optional `Screenshot captured:` notes (`screenshotNotes: true`).
- mtime watcher (1.5 s) — re-reads the project config, wakes queued subagent calls, calls
  `ctx.agent.reload()`.
- Debug logging: `PLANNER_DEBUG=1` (console) and `PLANNER_DEBUG_FILE=<path>` (synchronous append —
  live tests read this file because server stdout is buffered).

MCP servers are deliberately not touched (no `ctx.mcp` usage).

## Versioning & update flow

- `install.sh` records `sourceRoot` + `version` in
  `<configDir>/plugins/opencode-planner/.planner-install.json` and writes
  `agents/.planner-agents.json` (version + sha256 of template-identical files).
- `ensureAgentFiles` writes the project agent marker the same way; `refreshAgentDefinitions`
  rewrites files whose hash matches the marker (or that equal the current template) and **keeps
  user edits** (never recording their hashes).
- `detectVersionStatus(repo)` flags mismatches between the running plugin and the project config,
  project agent marker, project-local plugin copy, and the global install.
- `fetchLatestVersion(force)` reads the repo's `package.json` on GitHub (`raw.githubusercontent`),
  cached 24 h in `<configDir>/planner-update-check.json`; `force` always re-queries; failures are
  silent (`{error}`) and never throw. The startup toast is disabled with
  `PLANNER_NO_UPDATE_CHECK=1`.
- `runUpdate(sourceRoot, logPath)` — `git rev-parse` gate → `git pull --ff-only` → `bash
  install.sh --yes`; aborts without reinstalling on a failed pull or a non-repo source; writes a
  log on every path. User-triggered only (confirmation dialog).

## Agent definitions

`agents-v2/` holds the V2 markdown definitions. On setup the plugin copies **missing** files into
`<project>/.opencode/agents/` and creates `docs/superpowers/specs/` for the brainstormer.
Existing files are never overwritten; the global installer writes the same templates into
`~/.config/opencode/agents/`. The update flow can refresh definitions: files whose hash matches the
marker (or equal the current template) are rewritten, user edits are kept; `force` overwrites after
a `.md.bak` backup, and `/planner-status` offers that on legacy installs without a marker.

The brainstormer contract: superpowers skills (`brainstorming`, `writing-plans`) on the FIRST try,
spec written to the relative `docs/superpowers/specs/<date>-<slug>.md`, **verified on disk** before
`STATUS: READY` (`STATUS: FAILED` otherwise), questions allowed (the `question` tool — each
decision question carries 2-4 REAL alternatives as options, recommended first with
"(Recommended)", never flattened to yes/no; or `STATUS: NEEDS_INPUT` with ≤3 questions relayed by
the planner, max 3 rounds). The planner treats
automatic continuation messages (goal/loop prompts) as "continue the pipeline" and keeps replies
short — no design documents, no implementation reasoning, no per-call `model` on `subagent` calls.

## Tool-name aliases (extras/tool-name-aliases.js)

A global helper plugin installed by `install.sh`. Models — especially small local ones —
habitually call tools by Claude-Code/Roo-style names (`bash`, `ask_user`, `task`, `read_file`,
`list_dir`, …). The helper registers each alias as a real tool **only when its canonical OpenCode
tool exists in the session** (a missing canonical never becomes a hard error), rewrites alias
names in `execute.before` as a second line of defense, normalizes argument shapes
(`file_path`→`filePath`, …), and documents the expected shape in the alias description. The
`question`/`ask_user` path derives the REAL enumerated alternatives from a bare question string
(letter/number/bullet lists, "a, b, or c") instead of inventing Yes/No options; with no
enumeration the question stays open-ended (the TUI always adds a free-text answer). Unit-tested
by `tests/e2e/test_alias_unit.py`.

## Screenshots

`$OPENCODE_SCREENSHOT_DIR` points at `/tmp/opencode-screenshots/<project>-<hash>/`. Nothing is
posted unless `screenshotNotes` is enabled.

## Tests

- `unit_gate.py` — layout/contracts/installer (static)
- `test_semaphore_unit.py` / `update_unit.ts` (+ runner) — cap logic; version compare, status
  detection, agent refresh, update execution against fake clones
- `test_planner_config.py` — installer sandbox + defaults + markers + config contract
- `test_planner_v2.py` — real OpenCode 2 server: plugin/agents/opt-in/MCP/commands, snapshot,
  `/planner-config` + `/planner-status` in a PTY
- `test_subagent_limit_live.py` — real model: two simultaneous planner sessions, cap 1 serializes
  (waitedMs), live raise to 2 overlaps
- `test_delegation_live.py` — real model: brainstormer-first, deterministic zero-delegation turn
  nudged/resumed, 2-nudge cap, toggle-off
- `long_session_test.py` — real model: normal session + planner worker→tester cycles
