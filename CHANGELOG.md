# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-10-10

Hard enforcement where it used to be prompt-only, per-project config snapshots,
and version/update awareness. Every feature was validated on a real OpenCode 2
instance; each point has its own e2e suite.

### Added

- **The subagent concurrency cap (`maxParallelSubagents`) is now ENFORCED.**
  The built-in `subagent` tool is wrapped with a semaphore: with 1, extra
  calls wait for the running task instead of starting in parallel; with N, at
  most N run at once. A permission-deny fallback covers builds where the tool
  cannot be wrapped. Live config edits apply immediately (raising the cap wakes
  queued calls). Scoped to planner-originated calls.
- **Delegation watchdog (`enforceDelegation`, default on).** Every planner
  turn must contain a COMPLETED subagent call: a turn that ends without one is
  resumed with a bounded nudge (max 2 per user turn), and a READY brainstormer
  spec that never reaches the worker/tester gets nudged too. The nudge is a
  visible synthetic message that continues the turn. `planner.md` no longer
  allows the planner to skip the brainstormer — only the brainstormer may
  classify a request as TRIVIAL — and states "you are not a builder".
- **Global defaults + per-project snapshots.** `planner-defaults.json` lives
  in the opencode config dir (created on first load and by `install.sh`; never
  overwritten), is editable by hand and through `/planner-config → Global
  defaults (new projects)`. The first planner use in a project materializes a
  full copy into `.opencode/planner-plugin.json` (atomic create-or-fail, with
  `plannerVersion`/`createdAt`/`source` metadata). "Re-sync from defaults"
  replaces a project's settings on demand. Effective config = project over
  defaults over built-ins; `variants` are override-if-present.
- **Version awareness + one-click update.** Project configs stamp
  `plannerVersion`/`updatedAt`; agent definitions get a hash marker
  (`.planner-agents.json`) so refreshes update files the plugin wrote and keep
  user edits; a project-local plugin copy is detected. `/planner-status` (TUI
  dialog + palette) lists running/install/config/agents/local-copy/latest
  versions and conflicts, offers "Check for updates" (GitHub `package.json`,
  cached 24 h, `PLANNER_NO_UPDATE_CHECK=1` disables the startup notice),
  "Refresh agent definitions", and "Update now" — `git pull --ff-only` +
  `install.sh --yes` from the `sourceRoot` recorded at install time, with a
  confirmation step, a log file, and a restart notice. A failed pull aborts
  instead of reinstalling a stale tree.
- **Test coverage for every new point, verified on real instances:**
  `test_semaphore_unit.py` (cap logic), `test_update_unit.py` (version
  compare, status detection, agent refresh, update execution against fake
  clones), `test_subagent_limit_live.py` (two simultaneous planner sessions —
  cap 1 serializes with `waitedMs≈24 s`; a live raise to 2 overlaps, `inFlight=2`),
  `test_delegation_live.py` (brainstormer-first; deterministic zero-delegation
  turn nudged, resumed and delegated; cap of 2 nudges; toggle-off), and new
  phases in `test_planner_v2.py` (defaults creation, first-use snapshot,
  `/planner-status` dialog).

### Changed

- `/planner-config` gained "Max worker→tester cycles", "Re-sync from
  defaults", and "Global defaults (new projects)".
- Config writes stamp `plannerVersion`/`updatedAt` and preserve unknown keys;
  the defaults file is settings-only (no metadata).
- `install.sh` records `sourceRoot`, writes the global agent marker, creates
  the defaults file once, and keeps it on uninstall (the agent marker is
  removed).
- Debug logging (`PLANNER_DEBUG=1`) can append to `PLANNER_DEBUG_FILE` — the
  live suites read that file because server stdout is buffered.
- **The planner keeps short steps.** `planner.md` forbids design documents and
  implementation reasoning ("keep it short — do not overthink"), treats automatic
  continuation messages ("Continue working toward the active session goal" and
  other goal/loop prompts) as "continue the pipeline", and names the exact tools
  (`subagent`, `question`, `read`/`glob`/`grep`) so models stop inventing names
  like `ask_user`.
- **The brainstormer asks questions again** (per the user's requirement): it may
  use the `question` tool or reply `STATUS: NEEDS_INPUT` with up to 3 numbered
  questions; the planner relays them to the user with its `question` tool and
  re-delegates with the answers (max 3 rounds). Unanswered questions fall back to
  documented assumptions. It also uses the superpowers skills on the FIRST try,
  writes the spec to the relative path `docs/superpowers/specs/<date>-<slug>.md`,
  and verifies the file on disk before `STATUS: READY` (`STATUS: FAILED`
  otherwise) — the plugin guarantees the directory exists on every planner use.
- **Agent-model pins no longer depend on project agent files.** Pinning is
  applied whenever the agent exists anywhere (project, global agents dir, or
  opencode config); previously a missing `<project>/.opencode/agents/<name>.md`
  made the subagent silently inherit the planner's session model — the exact
  "I selected a model but it uses the main model" report.
- `refreshAgentDefinitions(dir, {force})` can overwrite legacy/user-edited agent
  files after writing `<name>.md.bak` backups; `/planner-status → Refresh agent
  definitions` offers that explicitly on legacy installs (no marker).

### Fixed

- **Subagent model overrides are stripped.** Models sometimes inject a
  hallucinated `model` into the `subagent` call (observed:
  `openai/gpt-5.1-codex-max`), which overrode the configured per-agent model and
  aborted the delegation with "Model not available". The plugin now drops any
  per-call `model` from planner-originated subagent calls.
- **The brainstormer can always write its spec.** The old flow relied on the
  directory existing at plugin load; a deleted/missing
  `docs/superpowers/specs/` made the write fail silently and the model reported
  READY for a file that never existed (it even hallucinated absolute paths like
  `/home/arnar/mario/...`). The directory is now ensured on every planner use and
  the spec is verified on disk before READY.
- **"Tool not available" root causes (real-session diagnosis).** On the user's
  previous OpenCode build, the delegation tool was named `task` (not
  `subagent`), the shell tool `bash`, and a denied permission REMOVED the tool
  from the subagent's toolset — so the stale V1-format global agent files
  (`agent/brainstormer.md` with `edit: deny`) stripped the brainstormer's
  `write` tool entirely ("this session exposes no file-write tool") and the
  plugin's instructions referenced tool names that did not exist there. Fixed:
  the plugin resolves the delegation tool by name (`subagent`, falling back to
  `task`) and every injected instruction, nudge, hook and prompt block uses
  that resolved name; `install.sh` moves stale `agent/<name>.md` copies aside
  (to `<name>.md.v1bak-<timestamp>`, never deleted) so no build can pick them
  up again; `extras/tool-name-aliases.js` (installed globally by `install.sh`)
  now registers an alias only when its canonical tool exists, covers many more
  hallucinated names, and documents the argument shape in the alias
  description.
- **The planner can no longer build the request itself.** The watchdog only
  nudged on "no subagent call" or "READY spec without worker/tester", so a
  planner whose brainstormer returned NEEDS_INPUT/FAILED could write the spec
  (or code) itself and slide through — reproduced from a real session
  (`brainstorm-v1`). Fixed: file writes/edits by the planner are denied
  server-side via the permission hook ("delegate this to the subagents"), and
  the watchdog now also nudges when the brainstormer did not return READY or
  TRIVIAL and nothing reached the worker/tester. A turn that relayed questions
  to the user (`question` tool) is a legitimate wait and is never nudged.
- **Decision questions no longer collapse to yes/no.** A model calling the
  `ask_user` alias with a bare `question` string got forced Yes/No options
  invented for it. The alias plugin now derives the REAL alternatives from the
  question text (letter/number/bullet enumerations, "a, b, or c" lists) and
  otherwise leaves the question open-ended (the TUI always offers a free-text
  answer); string options become proper `{label, description}` objects.
  `brainstormer.md` documents the exact `question` tool shape and requires
  2-4 concrete alternatives per decision (recommended first, "(Recommended)"),
  and `planner.md`/the injected block require the planner to relay those
  options instead of flattening them.
- New suite: `test_alias_unit.py` (alias input normalization — question option
  derivation, key aliasing, glob fallbacks, setup() registration harness) in
  `run_all.sh`; the unit gate gained section 17 (tool-availability +
  question-options hardening).
- **Follow-up (same day, real session `ses_ed8eb8d8bffe` in mario):** the
  brainstormer hallucinated Claude-Code-style MCP tool names
  (`mcp__local__write_file`, `mcp__local__exec`, …) — the engine rejects
  unknown names BEFORE any plugin hook fires, so no hook can rewrite them.
  Fixed by REGISTERING the observed `mcp__local__*` shapes as real alias tools
  (a real MCP tool with the same name always wins), normalizing file-tool
  arguments to `path` (the name 2.0.26 accepts live), and adding recovery
  rules to `planner.md`/`brainstormer.md`/`worker.md` + the injected block:
  a "No tool named …" error means the NAME was wrong — retry once with the
  exact canonical names, never `mcp__`-prefixed inventions. The same session
  also confirmed the planner-side design works: its attempts to write the
  spec itself fail by design ("no write/edit tools for the planner"), and the
  prompts now say so explicitly so small models do not spiral after the
  expected failure. Alias unit harness extended to 20 checks; unit gate to
  200.
- **Proactive audit (same evening) — six latent issues found and fixed:**
  a session that switched planner→Build stayed in the watchdog's tracked set
  and could get delegation nudges aimed at a Build agent (tracked set now
  drops sessions whose context reports another agent); the watchdog disabled
  itself whenever the PROJECT agent files were missing even though global
  definitions existed (`agentFileExists` now falls back to the global agents
  dir); a brainstormer `STATUS: NEEDS_INPUT` was indistinguishable from a
  generic failure for the watchdog (it now nudges with relay-the-questions
  guidance instead of re-delegate); `/plan` with an empty prompt forwarded
  `undefined` text (now coerced); `install.sh --update-agents` overwrote
  user-edited agent files with no backup (now `.bak-<timestamp>` first);
  the brainstormer could fire several `question` calls in a row (the mario
  session showed 5 sequential dialogs) — prompts now demand ONE call with up
  to 3 questions per spec, plus a relative-paths rule after a real
  `/home/arnen/...` home-directory typo made every search fail. Unit gate at
  206.
- **`docs/superpowers/specs/` is no longer created on plugin load.** It was
  created by `ensureAgentFiles` at setup — every project (even ones that never
  ran the planner) grew the directory at startup. It is now created exactly
  when a brainstormer delegation starts (the `execute.before` hook), and
  nowhere else; `/plan` and the first planner use only snapshot the config.
  The startup-time investigation also measured fresh TUI/server boots (paint
  1.1 s, models present, model.list 80-250 ms, session queries indexed) — a
  "very long" load with an empty model picker was traced to long-running
  stale `opencode serve` processes (started days before the config changes
  and deploys), which were stopped; a fresh launch is fast and complete.
- **The `/planner-config` and Global defaults menus stay open.** Picking a
  subagent model, a vision model, parallelism or cycles used to close the
  whole config menu — every additional tweak meant retyping /planner-config
  and navigating again. Both menus now return to their first page after every
  action (the dialog re-renders with the fresh values), and close only on Esc
  or the new "Done — close" option. Verified live over a PTY: pick a model →
  the menu reopens on the first page; Esc closes.
- New live suites: `test_agent_models_live.py` (per-agent models actually run,
  including with project agent files deleted — 11/11) and
  `test_spec_write_live.py` (spec file on disk + Requirements/Acceptance + full
  pipeline — 5/5).

## [0.4.0] - 2026-10-06

OpenCode 2 revival: the plugin actually loads, the TUI dialogs are back, and the
planner is opt-in with MCP servers off by default.

### Changed

- **OpenCode 2 only.** The V1 implementation (`plugin/`, `agents/`) and the
  dual-entrypoint packaging were removed; V1 support ended at v0.3.0 and
  remains in git history. `install.sh` is now a thin V2 installer.
- **The planner is opt-in.** The plugin never sets a default agent: normal
  sessions (Build/Plan/…) stay completely normal, and the planner instructions
  (cycle limit, parallelism, vision) are injected through the `session.context`
  hook **only when the request's agent is `planner`**. Reach it with Tab or
  `/plan`. This also removes the planner prompt/token cost from every session
  start.
- **MCP servers stay native.** The plugin no longer enables, disables, or
  otherwise manages MCP servers: the native `/mcps` command and the `mcp`
  section of `opencode.json(c)` own them. (The short-lived `mcp.enabled` config
  key and `/planner-mcp` command were removed; a stale `mcp` key is dropped
  from `.opencode/planner-plugin.json` on the next write.)
- **TUI dialogs work again** — as the package `./tui` entrypoint loaded from the
  plugin-directory layout (`index.ts` + `tui.ts` together). A single
  top-level `plugins/planner.ts` file can only load the server half, which is
  why popup configuration had disappeared.
- **The brainstormer is fully autonomous (completely separated from the
  planner/user).** It never asks questions and no longer returns
  `STATUS: NEEDS_INPUT`; ambiguity is resolved with documented assumptions in
  the spec. The planner no longer relays question rounds.
- **The planner explicitly controls every step**: it reads and checks the spec
  before the worker, inspects the worker's changed files before the tester, and
  only then runs the tester cycle — the plugin's context hook reinforces this.
- Each subagent model — including the **brainstormer** — is configured
  independently via `/planner-config` and applied per agent; unset models
  inherit the main model, so set a model to give the brainstormer its own.
- **One planner slash command that matters: `/planner-config` opens the TUI
  configuration dialog** (brainstormer/worker/tester/vision model + effort,
  max parallel subagents). Vision and parallelism also have palette entries.
  `/planner-vision`, `/planner-status`, `/planner-help` and the short-lived
  `/planner` menu/text commands were removed; the server registers only
  `/plan`, so no planner name appears twice anywhere.
- **Zero runtime dependencies.** `Plugin.define` is an identity helper, so the
  plugin uses a type-only import and a local `define`; discovered installs need
  no `npm install` of `@opencode/plugin`.
- Agent definitions are installed into the V2 location
  (`.opencode/agents/*.md`); the installer writes global definitions into
  `~/.config/opencode/agents/`. Missing files only — user edits are never
  overwritten.

### Fixed

- Plugin failed to load from a discovered directory on a machine without
  `@opencode/plugin` installed (`Cannot find package '@opencode/plugin'`).
- **Brainstormer could not write its spec.** V2 evaluates permissions in order
  and the *last matching rule wins*; the broad `edit: deny *` came after the
  `docs/superpowers/specs/**` exceptions, so every write was denied. The
  exceptions now come last. Subagents (brainstormer/worker/tester/vision) also
  explicitly deny the `subagent` action — only the planner delegates.
- The model picker dialog was unusable with many providers: it now supports
  type-to-filter by `provider/model` or provider name.
- `/planner-config` effort variants (`provider/model#high`) were persisted but
  never applied to the agent model.
- Vision model configuration was ignored, and the vision-mode instruction was
  never injected after the previous slim rewrite.
- `maxCycles` / `maxParallelSubagents` were stored but not injected into the
  planner's instructions.
- Screenshot directory env var and the (opt-in) screenshot notes are wired
  again; the config-file watcher applies edits live.

### Added

- `/plan <request>` — switches the session to the Planner agent and sends the
  request.

## [0.3.0] - 2026-10-03

The planner now follows opencode's main model; `/planner-config` only manages
the subagents.

### Changed

- **Subagent parallelism is configurable — `maxParallelSubagents`, default 1
  (strictly serial).** The planner is told how many subagent tasks it may run
  at the same time; with 1 it must wait for each task before starting the
  next. That is the right default for single-slot local model servers (vLLM
  etc.), where parallel subagents thrash the model — prefill, evict, prefill
  again. Set it with `/planner-config maxParallel <1-16>` (web) or the TUI
  config menu (Max Parallel Subagents). The value is clamped 1–16 and
  injected into the planner's system prompt on both V1 (system.transform) and
  V2 (context hook), so the model queues tasks instead of launching them
  together.
- **The enable/disable commands are gone.** The planner is always available —
  new sessions start on it and Tab switches to it — so `/planner-enable` and
  `/planner-disable` (TUI popups and web commands) were removed, the internal
  `enabled` flag with them (existing config files keep working; the key is
  ignored). The TUI palette now only has Planner Config / Status.
- **The planner is always active after installation — no `/planner-enable`
  step.** `install.sh` now installs the server plugin globally
  (`plugins/planner.ts`, a one-line entry re-exporting the real plugin from
  `plugins/planner/` — opencode only scans top-level files, so the helper
  modules never load as plugins themselves) and the agent definitions into
  `~/.config/opencode/agent/`. Those put planner/worker/tester/vision in the
  Tab agent cycle in **every** project and set the default agent, so after one
  opencode restart the planner is ready everywhere: press Tab to reach it.
- **Active by default, opt-out via `/planner-disable`.** A project with no
  config file counts as enabled (`defaultPlannerConfig().enabled = true`);
  disabling writes an explicit `enabled: false` that is respected. V2 behaves
  the same once the package is in the project's `plugins` array.
- **Models are pinned only when the project has a config file.** With no
  config, worker/tester stay model-less and inherit opencode's main model —
  nothing to rot and it works on any provider. `/planner-config` pins models
  explicitly when wanted.
- A project that still carries its own `.opencode/plugins/planner.ts` copy
  takes precedence: the global plugin detects the project copy and returns no
  hooks, so the two never run side by side (no double screenshot surfacing).
- `/planner-config` and `/planner-status` say "disabled" (not "NOT enabled")
  and point at `/planner-enable` to switch a project back on.

- **The planner always uses opencode's main model** — the model selected with
  `/model`. The plugin no longer pins a planner model (V1 `config` hook, V2
  agent transform) and no longer switches new sessions' models, so the
  session's own model choice governs the planner automatically.
- **`/planner-config` configures worker, tester, and vision only.**
  `/planner-config planner <model>` is rejected with an explanation; the TUI
  dialogs no longer list the planner as a selectable agent.
- `/planner-status`, `/planner-config`, and the TUI popups show the planner as
  "opencode main model (use /model)".
- V1 screenshot injection no longer pins the planner model on the injected
  message (the session's model is used).
- A `models.planner` entry from older config files is preserved but ignored.

### Added

- **State-aware installer.** `install.sh` now detects opencode V1 and V2
  (including v2 betas, which use the 0.0.0-beta-NNNNN version scheme), reports
  what it found, and adapts: fresh machines get a single install prompt;
  existing installs get an update/reinstall/remove menu (an older install is
  detected through a version marker and a downgrade is flagged);
  non-interactive runs default to install/update. Uninstall now removes every
  known layout from every generation — planner and vega-era plugin files, both
  shared roots, `web-commands.json`, stale `planner-*`/`vega-*` text commands,
  our `tui.json` entries, and the package in a global `opencode.json` plugins
  array. `opencode.jsonc` is never rewritten (comments survive); the script
  reports what to remove instead. `--yes` and `--help` are supported.
- `/planner-enable` now pre-installs opencode's plugin dependency
  (`@opencode-ai/plugin` / `@opencode/plugin`) in the background. opencode runs
  that npm install before loading plugins on the first start after enabling (or
  after `git clean` / an opencode update), which made startup take seconds to
  minutes; pre-warming keeps restarts on the fast path (~0.1s instance boot).
- **Enabling applies without a restart (V1).** A first-time `/planner-enable`
  copies the per-project plugins, then asks the project's opencode instance to
  reload (the same dispose-and-reboot opencode uses for its own config edits)
  once no session of the project is busy — so the planner becomes the default
  agent immediately. The TUI reports the outcome and only falls back to a
  restart hint if the reload cannot be triggered. Model changes were already
  live.
- **One restart total on V1, one on V2 — both only to load the plugin for the
  first time.** Verified against real servers that opencode does not hot-load a
  newly added plugin (neither a package directory nor a config `plugins` entry),
  so the initial install still takes effect on the next start. Everything after
  that is live on both lines: on V2 the enable/disable reply no longer implies a
  restart (it only notes the background dependency pre-warm).
- Unit-gate coverage that the planner model is never pinned (V1 and V2) and
  that the dialogs/commands do not offer the planner.
- V1/V2 e2e coverage: `/planner-config planner …` writes nothing, no forced
  model switch on new sessions, and the planner agent stays model-less.

### Added

- **`extras/strata-fit-output.js` — client-side fit for the strata context
  wall.** The V100 box rejects a request when `prompt_tokens + max_tokens`
  exceeds 262144 (its fixed default max_tokens is 32000 while opencode sends
  none), so long sessions died with "prompt (231537 tokens) + max tokens
  (32000) exceeds the context…" — five times since Sep 30, always at ~230144
  tokens (262144 − 32000). The plugin estimates the outgoing prompt from the
  session and asks for `min(output limit, room − 4096)`, so the request always
  fits; `options.max_tokens: 16000` on that model is the static safety net.
  Scoped to the `strata` provider only. Verified against a local mock endpoint:
  tiny prompt → max_tokens 15430, big prompt → 3588, total fits the declared
  context. (The definitive fix stays server-side: `"fit_max_tokens": true`.)

### Added

- **Brainstormer subagent (V1 + V2).** The planner now delegates to a new
  `brainstormer` subagent FIRST for non-trivial requests. It follows the
  superpowers `brainstorming`/`writing-plans` skills (path configurable via
  `skillsPath`, default `/home/armen/.kimi-code/plugins/managed/superpowers/skills`,
  registered in `skills.paths`), asks the questions that matter — the planner
  relays them to the user verbatim, up to 5 rounds — and writes an
  implementation-ready spec to `docs/superpowers/specs/<date>-<slug>.md` with
  numbered verifiable requirements, acceptance criteria, non-goals and
  assumptions. It returns `STATUS: NEEDS_INPUT` / `READY` / `TRIVIAL`, never
  writes code, and the worker is told to read the spec first. Its model is
  configurable (`/planner-config brainstormer <model>`, TUI dialog); the
  permissions allow reading the skills path (`external_directory`) and native
  skill activation (`skill`) while denying edit/shell/task. Verified on real
  V1 and V2 instances.

### Fixed

- **A deploy can no longer abort a turn at a subagent handoff.** The
  idle-guarded instance reload treated a root session waiting on a worker as
  idle (its "busy" entry is purged after 10 minutes, and the server stops
  reporting it busy while it waits), so a pending reload fired at the exact
  second a 1-hour worker finished — the planner's continuation died with
  `MessageAbortedError: Aborted` ("interrupted" in the TUI; observed live in
  `ses_ef5367998…`). The reload now requires the ENTIRE project to be quiet
  for `reloadQuietMs` (default 5 minutes: no busy status, no session updated),
  and logs every deferral (`reload deferred: last activity Ns ago`). Verified
  by simulating a deploy mid-run of a worker/tester session: the session
  completed with zero aborted messages, and the reload applied afterwards.
  Also declared a guard variable whose missing declaration made the reload
  path throw silently, and stopped the sweep summary from logging once per
  session directory (log spam).
- **Deployed fixes now reach RUNNING instances (stable loader shim).** opencode
  caches plugin modules by path for the life of the server process: an instance
  reboot re-runs plugin registration but re-imports nothing, so a changed
  plugin file on disk was silently ignored — sessions already open kept
  behaving the old way while the fix sat on disk (proven on a real instance:
  after a dispose the config hook re-ran 3x while the module import count never
  moved). `planner.ts` is now a tiny stable loader that copies the
  implementation (`planner-impl.ts` + `planner-core.ts` +
  `vision-delegation.ts`, relative imports rewritten) to unique file names on
  every instance boot and imports those; unique paths can never hit the module
  cache, so the running code is always the code on disk. The implementation
  watches its own files and triggers the idle-guarded instance reload when a
  new build lands. Verified on a real server: a probe appended to the impl
  executed after the reload without restarting opencode.
- **Oversized sessions auto-heal and stay continuable.** Sessions from older
  builds carried tens to hundreds of MB of inlined screenshots; every request
  then died with `Cannot connect to API: The socket connection was closed
  unexpectedly` (llama.cpp drops oversized bodies) and the session could not be
  continued at all. The plugin now prunes its OWN injected image messages from
  any session whose screenshot dir exceeds `maxSessionImageBytes` (default
  24 MB) — the image files stay on disk, real messages untouched — shortly
  after boot, once a minute, and immediately before an auto-resume so the retry
  has a chance. Verified on a real instance: a 30 MB fixture session was pruned
  after the reboot and the files remained on disk.
- **Screenshots stay in the subagent's scope: nothing is surfaced by
  default.** A real session's worker ran for ~40 minutes and captured ~80
  screenshots; every one entered the planner's session (the plugin surfaced
  each) and the session accumulated **~90 MB of image data** — its continuation
  requests ballooned until the model endpoint dropped the connection. Worse,
  the notes/images are chat noise for the user: the capture path is already in
  the subagent's own tool output. Now the plugin posts **nothing** for a
  screenshot unless explicitly opted in: `"screenshotNotes": true` posts a
  one-line `Screenshot captured: <path>` note, `"inlineScreenshots": true`
  renders image parts (bounded per session by `maxInlineScreenshots` = 6 /
  16 MB, 2 MB per image, with one notice and skip markers past the budget).
  Verified on a real instance: default → no note and no image part; notes
  opt-in → note only; inline opt-in → image part.
- **Strict chat templates work again: the plugin never adds system messages.**
  opencode turns every entry of the system array into its own system message,
  and strict templates (Qwen on vLLM/llama.cpp: "System message must be at the
  beginning.") accept exactly one — the planner's two injected instruction
  blocks made every call fail with HTTP 400 on such servers (found while
  reproducing a user's endpoint outage). The instructions are now appended to
  the existing system entry on both V1 (system.transform) and V2 (context
  hook), so the message count never changes.
- **Auto-resume after connection-class endpoint errors.** When the model
  endpoint drops mid-flow (a single-slot local server restarting: "Cannot
  connect to API: The socket connection was closed unexpectedly") opencode's
  own retries run out after ~1 minute and the planner stalls right when it
  should continue. The V1 plugin now watches `session.error` events, and for
  connection errors (socket closed/hang up, fetch failed, ECONNREFUSED,
  ECONNRESET, ETIMEDOUT, …) re-triggers the interrupted turn once the session
  is idle — bounded to 3 attempts per burst (10-minute budget), configurable
  with `autoResume: false`. Verified by reproducing the outage with a fault
  proxy in front of a real model: the planner continues by itself once the
  endpoint is back.
- **Fast starts: the plugin dependency is pre-warmed again.** opencode runs
  `npm install @opencode-ai/plugin@<version>` synchronously before loading
  plugins whenever the dependency is missing or at a different version than
  the running binary — the "opencode takes long to open after installing"
  case (measured: ~14s cold vs ~3.5s warm; a stale 1.18.5 install next to a
  1.18.34 binary made it re-check on every start). Now `install.sh`
  pre-installs the **exact running version** into the config dir in the
  background, and the V1/V2 server plugins pre-warm a project's `.opencode`
  whenever the project carries its own plugin files. Both are best-effort and
  no-ops when the right version is already installed.
- **Screenshot flood guard.** The sweep surfaced EVERY screenshot younger than
  the 24h TTL, and its "already injected" map was in-memory only — every
  instance re-boot re-injected the whole directory. A project with screenshot
  history therefore received image messages in bursts (one session got 289,
  a legacy debug session 24,328 — about 50 GB in the session DB), which also
  left the session too heavy to open. Now: only FRESH files (modified within
  5 minutes — the same rule the tool-run path always used) are surfaced, at
  most 3 per sweep tick, and the seen-marker persists in the project's
  screenshot dir (`.injected.json`) so a reload never re-injects. The live
  suite pins all three: stale files are ignored, a fresh burst is bounded,
  and an instance dispose injects nothing twice.
- **The enable toast now points at the agent switcher.** opencode's Tab key
  CYCLES the current agent (Build → Plan → Planner) rather than opening a
  list, and the planner is the third entry — verified on a real instance with
  a terminal-emulator capture. The success toast says "press Tab to switch to
  it", new sessions start on Planner, and the TUI suite asserts that the
  planner lands in the Tab cycle after an in-session enable.
- **A nested enable no longer escapes the repo.** The TUI's root resolution
  climbed above the git worktree, so enabling from a repo whose parent folder
  happened to hold a planner config (e.g. `~/IdeaProjects`) wrote the config
  and plugin copies into the PARENT and never activated in the project the
  user was in. The climb now stops at the worktree, and the post-enable reload
  disposes both the launch directory and the resolved project root, so the
  running instance is always the one that reloads.
- **Neutral enable messages**: the "no restart needed" phrasing is gone from
  the toasts (the reload status is reported as plain "Planner is active.").
- **Stale project-level `planner-tui.ts` is cleaned on enable.** Projects
  enabled under the very old layout keep a TUI-only file in
  `.opencode/plugins/`, which opencode's server loader rejects on every start
  ("Plugin export is not a function"). `/planner-enable` now removes that copy
  while it refreshes the server plugins, so re-enabling an old project both
  upgrades it and clears the load error.
- **`vision-delegation.ts` failed to load in V1** ("Plugin export is not a
  function"): opencode's legacy loader treats every export of a plugin file as
  a plugin function, and the vision instruction strings had been exported from
  it. The constants now live in `planner-core.ts`, and the V1 live suite
  asserts that `planner.ts` and `vision-delegation.ts` load cleanly.
- **V1 install layout**: the TUI plugin (`planner-tui.ts` + `planner-core.ts`)
  now installs into `~/.config/opencode/planner-tui/` instead of the
  auto-scanned `plugins/` dir. opencode loads every file in `plugins/` as a
  SERVER plugin, so the TUI-only file (and the helper module) produced load
  errors on every start. `install.sh` migrates old installs and `uninstall`
  cleans both locations.
- **install.sh JSON safety**: `tui.json`/`config.json` are written atomically
  and a malformed file is backed up (install aborts instead of silently
  replacing it with our content).
- **V2 robustness**: legacy `planner.json` is now migrated on V2 as well; a
  read-only project can no longer fail plugin setup when agent definitions
  cannot be written; each V2 registration (agent transform, commands, context
  hook) is best-effort so a partial API surface does not kill the plugin;
  stale "busy" session flags expire after 10 minutes so undelivered command
  replies can always be retried.
- V1 resolves the project from `process.cwd()` as a last resort when the
  plugin's own path cannot be used; the vision-model self-check accepts
  `provider/model#variant` configs.
- Config writes preserve fields the plugin does not own: unknown top-level
  keys and unknown `models`/`vision` keys written by a newer version (or a
  human) survive every update; deleting a variant still sticks.
- Screenshot surfacing recognizes quoted paths that contain spaces
  (`"/tmp/my shots/a.png"`) in tool output.
- **Screenshots are isolated per session.** V1 gave every session the same
  project-wide screenshot dir and injected new files into "the most recently
  active session", so a screenshot taken in one session popped up in another
  (and its global toast appeared regardless of the session you were viewing).
  Each session now gets its own subdirectory (named after the session that ran
  the shell) and the sweep attributes every image through that name; the toast
  is gone (v1 toasts are global and cannot be scoped). V2 no longer reports
  files merely mentioned in tool output when they live in the shared dir (an
  `ls` of another session's screenshot was enough to surface it), keeping
  explicit write/edit targets only. Regression check added: screenshot in
  session A appears in A and NOT in a second session B.
- The TUI e2e suite installs into a hermetic config dir (it no longer depends
  on the host's global install) and asserts the new layout; its popup checks
  poll longer to remove a timing flake.

## [0.2.0] - 2026-10-02

OpenCode V2 support, from the same package as V1.

### Added

- **OpenCode V2 (2.x) implementation** — the default export is a
  `Plugin.define({ id: "planner", setup })` that applies per-agent models via
  `ctx.agent.transform`, registers `/planner-*` as native commands, injects
  `maxCycles` + vision instructions through the `context` session hook, and
  switches new planner sessions to the configured planner model. Requires no
  restart: config edits are watched and transforms replayed via
  `ctx.agent.reload()`.
- **V2 TUI dialogs** (`./tui` export): `/planner-status`, `/planner-config`
  (agent → model → effort variant), `/planner-enable`, `/planner-disable`,
  `/planner-vision`, `/planner-help` as interactive popups.
- **V2 agent definitions** (`agents-v2/*.md`) using the V2 frontmatter contract
  (`permissions` rules). `/planner-enable` installs both V1 and V2 layouts, so a
  project works under either opencode line.
- **Dual package entrypoint**: one package exposes the V2 `setup()` and the V1
  `server()` (openCode >= 1.18.29), sharing the single
  `.opencode/planner-plugin.json` config format.
- **V2 e2e suite** (`tests/e2e/test_planner_v2.py`, 27 checks) covering agent
  transforms, per-agent worker/tester models, default agent, session model
  switching, vision config, disable, and TUI dialogs. Skips when no V2 binary
  is available (`PLANNER_V2_BIN`).

### Changed

- Package name is now `@armen181/opencode-planner` (the unscoped
  `opencode-planner` name is taken on npm by an unrelated project).
- V1 e2e live suite boots its server with a private `HOME`, so host-level
  plugins (including `$HOME/.opencode/plugins`) can no longer override the
  project config under test.

### Fixed

- V2 agent-model injection no longer depends on the agent being present in the
  transform editor at registration time (agents installed later are picked up).
- V2 command replies are delivery-verified and retried by the plugin timer until
  they land — opencode 2.0.22 rejects synthetic messages posted while a session
  is busy, which previously lost `/planner-enable` confirmations.
- V2 e2e suite waits for each session to go idle between commands, avoiding an
  opencode 2.0.22 session-runner wedge when the agent registry changes mid-turn.
- The unit gate no longer requires `pyyaml` (falls back to a minimal
  frontmatter parser), so it runs on fresh systems without Python packages.

## [0.1.0] - 2026-09-29

First public release.

### Added

- Multi-agent workflow for [opencode](https://opencode.ai): a `planner` agent
  that delegates to `worker` and `tester` subagents, rendered as clickable
  subagent cards in one session.
- Optional `vision` subagent for image requests.
- Three vision modes: `agent` (dedicated multimodal subagent), `proxy`
  (an upstream proxy transcribes images in-flight), and `native` (the agents'
  own multimodal models read images).
- Per-agent model configuration plus per-agent effort (opencode "variant":
  `low` / `medium` / `high` / `max`), for planner, worker, tester, and vision.
- Single configuration file, `.opencode/planner-plugin.json`, written by the
  plugin — no second config file to keep in sync.
- Live config reload: model and vision changes apply to all sessions in a
  project without restarting opencode.
- Inline screenshot surfacing: screenshots written by agents (or `.png` paths
  detected in tool output) are injected into the main session, scoped per
  project.
- `/planner-*` commands as TUI popup dialogs, and the same commands as text
  commands in the web UI.
- `install.sh` to install the plugins and agents, and `./install.sh uninstall`
  to remove them.
- Robust default-model resolution: a first `/planner-enable` seeds
  planner/worker/tester with the first default model candidate that the
  running opencode actually advertises.

### Fixed

- `/planner-config` stays visible while the planner is disabled, explaining the
  current state and how to enable, instead of disappearing from the palette.
- First-time enables now include one-time restart guidance, because per-project
  server plugins are only loaded at boot.
- Installing planner now removes the legacy orchestrator-tui plugin entry,
  which managed the same agent files and could conflict with planner.
