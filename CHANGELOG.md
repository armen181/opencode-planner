# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
