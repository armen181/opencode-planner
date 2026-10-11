# HANDOVER — opencode-planner (v0.5.0 working tree)

> **ADDENDUM — 2026-10-10 evening (tool-availability + reliability investigation).**
> The user reported three symptoms; all were diagnosed from the real session DB
> (`session`/`message`/`part` schema — the 1.18.34-era DB, NOT the 2.0.26
> `session_v2`/`session_message` one) and fixed the same day:
>
> 1. **"Tool not available" (even shell/disk-write).** Root causes found: (a)
>    stale V1-format global agent files in `~/.config/opencode/agent/`
>    (singular) denied `edit`/`bash`/`task`/`question` — on that build a denied
>    permission REMOVED the tool from the subagent toolset (and `write` is
>    gated by the `edit` action), so the brainstormer literally had no
>    file-write tool and tried writing its spec through a FreeCAD MCP tool;
>    (b) the plugin hard-coded the tool name `subagent` while that build named
>    the delegation tool `task` (shell = `bash`); (c) the alias plugin
>    registered aliases whose canonical tool was missing (hard
>    "maps to X, which is not available" errors) and missed many hallucinated
>    names. Live probes on 2.0.26 established: BOTH `agent/` and `agents/` are
>    read, and **plural `agents/` wins** on name conflicts (global and
>    project).
>    Fixes: the plugin resolves the delegation tool by name (`subagent`, else
>    `task`) and injects that resolved name everywhere (`delegationToolName`);
>    hooks accept both names; the planner's file writes are DENIED
>    server-side (`permission.evaluate`); `install.sh` moves stale singular
>    `agent/<name>.md` files to `<name>.md.v1bak-<timestamp>`; the alias
>    plugin moved into `extras/tool-name-aliases.js` (repo = source of truth,
>    installed by `install.sh`), registers an alias only when the canonical
>    tool exists, covers many more names, and documents argument shapes.
> 2. **Planner executing itself.** Reproduced from session `brainstorm-v1`
>    (the planner wrote the spec itself after two failed brainstormer runs).
>    Fixed: planner write-deny (above) + new watchdog reasons ("brainstormer
>    did not return READY or TRIVIAL", "the planner wrote files itself"); a
>    turn that relayed a question (`question` tool completed) is a legitimate
>    wait and never nudged.
> 3. **Decision questions flattened to yes/no.** The alias plugin INVENTED
>    `[{Yes},{No}]` options whenever a model passed a bare `question` string
>    to `ask_user`. Fixed: real options are derived from the question text
>    (letter/number/bullet enumerations, "a, b, or c" lists), else the
>    question stays open-ended (the TUI always adds free-text);
>    `brainstormer.md` documents the exact `question` tool schema and demands
>    2-4 concrete alternatives (recommended first); the planner relay rule and
>    the injected block require passing those options through.
>
> New/changed tests: `tests/e2e/test_alias_unit.py` (12 checks, in
> `run_all.sh`), `tests/e2e/test_question_live.py` (live, real model: the
> brainstormer asked THREE decision questions with concrete options, 8/8),
> installer sandbox extended to 44 checks (v1bak + alias install), unit gate
> extended to 194. All static gates green; live suites re-run green:
> delegation 20/20, spec-write 5/5, subagent-limit 9/9.
>
> **Deployed the same evening**: `./install.sh --yes --update-agents` (the
> global agents were verified unmodified v0.4.0 templates first; user's own
> `agent/cad.md` untouched; `.v1bak-*` backups kept). Deployment verified live
> on the REAL config (agents registered with v0.5.0 definitions, brainstormer
> `question` allow + specs allow, planner edit deny). The user must RESTART
> OpenCode for the running server to pick up the new plugin code.
> The 0.5.0 tree is still uncommitted (user decides).
>
> **ADDENDUM 2 — 2026-10-10 late evening (real session `ses_ed8eb8d8bffe`, mario).**
> The user reported "still telling no tool available". Findings from the
> session (2.0.26, planner on strata/qwen3.8-flash-next-iq3_s):
> (a) Round 1 actually WORKED end-to-end under the new code: brainstormer
> asked questions (5 `question` calls, answered), wrote the spec via the
> `write_file` alias, worker+tester ran. (b) Round 2 failed because the
> brainstormer hallucinated `mcp__local__*` tool names — the engine rejects
> unknown names before any plugin hook fires, so NO hook can rewrite them;
> the only working defense is registering the names as real tools. Fixed:
> `extras/tool-name-aliases.js` now registers the observed `mcp__local__*`
> shapes (read_file/write_file/edit_file/exec/execute_command/run_command/
> glob_file_search/list_dir → canonical read/write/edit/shell/glob), a real
> MCP tool with the same name always wins. (c) The planner tried to write the
> missing spec itself and got "No tool named write" — that is the DESIGNED
> behavior (planner has no write tools; denied permission removes the tool
> from the toolset on 2.0.26 too), but nothing told the model, and it
> reasoning-looped for 11 minutes until the user interrupted. Fixed: prompts
> now state explicitly that the planner has no write/edit tools and must
> re-delegate the brainstormer instead, plus a recovery rule for "No tool
> named …" errors in planner/brainstormer/worker + the injected block.
> (d) File-tool arguments normalized to `path` (what 2.0.26 accepts live).
> Alias unit harness 20 checks (incl. a mock-registry setup() harness), unit
> gate 200, full battery green, REDEPLOYED via install.sh (agents + alias
> plugin). The zen "weekly usage limit" error the user saw when continuing
> the session is the opencode Zen provider quota (resets Tue 2:19 PM) — not a
> plugin issue; switching the session model to a local provider avoids it.
> **All running OpenCode servers must be RESTARTED to load the new code.**
>
> **ADDENDUM 3 — proactive audit (same night).** Six latent issues fixed:
> planner→Build agent switches left the session watchdog-tracked (nudges aimed
> at a Build session — now dropped from the tracked set on agent change);
> `agentFileExists` only checked the PROJECT agents dir so the watchdog
> disabled itself when global definitions existed (now falls back to the
> global dir); brainstormer NEEDS_INPUT was watchdog-indistinguishable from a
> failure (now a dedicated nudge: relay the questions via `question` and
> wait); `/plan` with an empty prompt forwarded `undefined` text; `install.sh
> --update-agents` destroyed user-edited agent files without a backup (now
> `.bak-<timestamp>`); brainstormer could stack several `question` calls
> (mario showed 5 dialogs — now ONE call, ≤3 questions per spec) and templates
> now demand RELATIVE repo paths after a real `/home/arnen/...` typo failed
> every search. Known limitations documented: unknown tool names never reach
> plugin hooks (only registration helps — hence the mcp__ aliases), and a
> `question` asked from a subagent in a HEADLESS/API session blocks forever
> (no answering surface; fine in the TUI). Battery green: unit gate 206,
> alias 20, installer 44, v2 42. Redeployed; restart required.
>
> **ADDENDUM 4 — specs-dir laziness + startup investigation.** The user
> complained that (a) loading was very slow with an empty model list and (b)
> `docs/superpowers/specs/` appeared in projects that never used the planner.
> (b) was ours: `ensureAgentFiles` created the dir at plugin SETUP for every
> project. Fixed: removed from setup and from `ensureProjectSnapshot` — the
> dir is now created in the `execute.before` hook exactly when a brainstormer
> delegation starts (`subagentTarget(e.input) === "brainstormer"`), so a
> project that never uses the planner never grows plugin directories.
> (a) was measured, not guessed: fresh server boot (location 51-80 ms),
> model.list 80-250 ms with the full provider set, session queries indexed
> (<0.2 s on the 63 GB DB), TUI paint 1.1 s with the model picker populated.
> Every provider endpoint responds fast except `lan`/`flash-next`
> (192.168.5.148:9090 — connection refused; those two providers are dead in
> the config, instant failure, no hang). The slow/no-models symptom was the
> STALE long-running `opencode serve` processes (Oct 6/7/9 — older than every
> config change and deploy, including the goal-plugin removal); they were
> stopped (plus two probe servers). One leftover dead config entry: the
> `lan`/`flash-next` providers point at a down endpoint — remove or revive
> them in `~/.config/opencode/opencode.json` at will. Battery re-run green
> (206/20/36+44/42); v2 suite now asserts the specs dir is ABSENT before
> first planner use (opt-in).
>
> **ADDENDUM 5 — stay-open config menus.** The user asked for it: picking a
> model or a vision setting used to close the whole config menu. Both
> `/planner-config` and the Global defaults screen now LOOP back to their
> first page after every action (fresh values re-rendered; the loop reads the
> config at the top of each iteration) and close only on Esc or the new
> "Done — close" option. Verified live over a PTY in a scratch project
> (menu opens → model pick → menu reopens → Esc closes). Battery green
> (unit gate 207). Redeployed.
>
> **ADDENDUM 6 — clean SMB validation run (v3) + final alias additions.** The
> clean re-run (fresh project, planner=strata2/high, worker+tester=strata,
> vision=goproxy vision-exp, PLANNER_DEBUG_FILE captured the watchdog live)
> ran ~30 min on the user's stop: worker#1 (74 s, re-delegated) → worker#2
> (9 min, built a self-contained 878-line index.html, captured a real
> headless-Chromium screenshot and read it back) → tester (independent
> line-numbered + node-vm verification, stopped mid-run by the user). Only
> 9 tool errors total (vs 20+ in the unfixed run): 7 = the planner's denied
> build attempts (by design, recovered each time), 2 = tester `grep_files`.
> Watchdog debug log confirms live: semaphore acquire/release cycles,
> attempt/call counting, execution events. No loop-breaker trigger (correct:
> no identical-call spiral). Three more hallucinated names found and aliased
> (`grep_files`→grep, `list`→glob, `try_execute`→shell); alias harness +
> unit gate 208/208, redeployed. The v3 session tree: worker, worker, tester
> — the planner skipped the brainstormer spec again (workers ran, so the
> watchdog's current contract did not nudge); enforcing brainstormer-before-
> worker remains an open item if the user wants it.

Written 2026-10-10 for the next agent continuing this project. Read this end to end
before touching anything; it contains the behavior contract, every change made in
this session, how to validate, and the open items.

---

## 1. What this project is

**`@armen181/opencode-planner`** — a multi-agent plugin for **OpenCode 2.x**
(not Command Code; not OpenCode V1 — V1 support ended at 0.3.0 and was removed).

An **opt-in** primary agent `planner` coordinates subagents in one session:

```
user → planner → brainstormer (spec) → planner checks spec file
     → worker (implementation) → planner checks changed files
     → tester (verification) → … maxCycles cycles … → planner reports
```

- Normal sessions (Build/Plan/…) are untouched; nothing is injected unless the
  request runs the `planner` agent.
- `vision` is an optional subagent for image work.
- MCP servers are OpenCode's own concern (native `/mcps`, `opencode.json` `mcp`) —
  this plugin never touches them.

**Repo**: `/home/armen/IdeaProjects/opencode-planner`, branch `master`.
**Working tree state**: v0.5.0 changes are **uncommitted** (see §7). The user's
**global install is still v0.4.0** (`~/.config/opencode/plugins/opencode-planner/`)
— none of the fixes below are live in their real sessions until deployed (§8).

---

## 2. How it must work (behavior contract — the source of truth)

### 2.1 Config model
- `planner-defaults.json` lives in the OpenCode config dir
  (`OPENCODE_CONFIG_DIR` → `XDG_CONFIG_HOME/opencode` → `~/.config/opencode`).
  Created once by the plugin (`ensureDefaultsFile`) and by `install.sh`; user edits
  are never overwritten. Settings only — no project metadata.
- First planner use in a project (`ensureProjectSnapshot`, called from the
  `session.context` hook for agent=planner and from `/plan`) snapshots the current
  defaults into `<project>/.opencode/planner-plugin.json` with metadata
  `plannerVersion`, `createdAt`, `updatedAt`, `source:"defaults"` (atomic
  create-or-fail — an existing file is never overwritten).
- **Effective config** = project file over defaults over built-ins (per key).
  `variants` is *override-if-present*: a project that specifies variants owns the
  whole map (an empty map clears inherited variants).
- Project writes stamp `plannerVersion`/`updatedAt` and preserve unknown keys.
- `/planner-config` actions: models (brainstormer/worker/tester) + effort, vision,
  max parallel subagents, max worker→tester cycles, delegation enforcement,
  **Re-sync from defaults**, **Global defaults (new projects)**.

### 2.2 Brainstormer contract (agents-v2/brainstormer.md)
1. **Superpowers skills on the very first try**, before recon: activate
   `brainstorming` then `writing-plans`; fall back to reading
   `<skillsPath>/{brainstorming,writing-plans}/SKILL.md`
   (default `/home/armen/.kimi-code/plugins/managed/superpowers/skills`).
2. Recon → classify (TRIVIAL spike/question; bounded; architectural).
3. **Write the spec with the file tool to the RELATIVE path**
   `docs/superpowers/specs/<YYYY-MM-DD>-<slug>.md` — never invent absolute/home
   paths (a real failure was `/home/arnar/mario/...` / `/home/arnen/mario`).
   The plugin creates `docs/superpowers/specs/` on every planner use
   (`ensureSpecsDir`), because the file tool never creates parent directories.
4. **Verify the file on disk** (read back / glob) before replying; retry once;
   otherwise `STATUS: FAILED` with the error. **Never report READY without a file.**
5. **Ask questions while creating** when ambiguity is costly: use the `question`
   tool (permission allowed), or reply `STATUS: NEEDS_INPUT` with a numbered
   `QUESTIONS:` list (max 3). The planner relays and re-delegates (max 3 rounds).
   If no answers arrive → documented Assumptions, continue.
6. Reply formats: `STATUS: READY | TRIVIAL | NEEDS_INPUT | FAILED`.

### 2.3 Planner contract (agents-v2/planner.md)
- Every request: brainstormer FIRST (only it classifies TRIVIAL). Then READ the
  spec file and verify it exists/non-empty/requirements+acceptance → delegate to
  worker → inspect changed files → delegate to tester → process STATUS →
  re-delegate on FAIL (maxCycles) → report.
- **Keep it short — do not overthink**: brief status updates, no design
  documents, no implementation reasoning (that is the worker's job). Spend turns
  on tool calls.
- **Automatic continuation messages** ("Continue working toward the active
  session goal", goal/loop prompts) are NOT new requests: never re-brainstorm,
  never implement — continue the pipeline from where it stopped.
- **Never pass a `model` to the `subagent` tool** — the plugin pins per-agent
  models from config and strips per-call overrides.
- Relay brainstormer questions via the `question` tool; never invent answers.
- **Tool names are exact**: `subagent`, `question`, `read`/`glob`/`grep`,
  `shell`. Never call variants like `ask_user`, `bash`, `list_dir` (see §6 why).

### 2.4 Runtime enforcement (src/index.ts)
- **Subagent concurrency cap** (`maxParallelSubagents`, 1–16): the built-in
  `subagent` tool is wrapped with `Semaphore` (`src/semaphore.ts`). Extra calls
  queue until a slot frees; wrapper is idempotent (`__plannerGate`), scoped to
  planner-originated calls. Fallback: a `permission.hook("evaluate")` denies
  over-cap calls when wrapping fails. Raising the cap calls `subagentGate.wake()`.
- **Delegation watchdog** (`enforceDelegation`, default true): counts only
  **COMPLETED** subagent calls (execute.after). Turn-end detection uses
  `session.execution.*` events + a **4 s settle window** (`TURN_SETTLE_MS`; a step's
  tool calls run right after the step ends, and `session.idle`/`session.status`
  are NOT delivered to plugins on 2.0.x). A turn that ends with no completed call
  (or only a READY brainstormer spec and no worker/tester) is resumed with a
  `session.synthetic({ resume: true })` nudge — max **2 per user turn**, visible
  in the transcript, reset on the next user prompt.
- **Subagent model overrides are stripped** in `execute.before` (models
  hallucinate `model` fields; an unknown id aborts the delegation).
- `/plan` = snapshot + switchAgent(planner) + send. The server registers no other
  planner command.
- Screenshots: `OPENCODE_SCREENSHOT_DIR` via shell hook; notes off by default.
- Debug logging: `PLANNER_DEBUG=1` (console) + `PLANNER_DEBUG_FILE=<path>`
  (synchronous append — the server's stdout is buffered, so tests read this file).
- Agent-model pinning is **not gated on agent files**: the transform applies pins
  whenever the agent exists anywhere (project/global/config).
- **Per-agent model pins are what the subagents RUN with** — verified live (§5).

### 2.5 Versioning + update flow
- `install.sh` records `sourceRoot` in
  `~/.config/opencode/plugins/opencode-planner/.planner-install.json`, writes
  `agents/.planner-agents.json` (version + sha256 of template-identical files),
  creates `planner-defaults.json` once; uninstall keeps defaults, removes the
  marker.
- Agent refresh (`refreshAgentDefinitions(dir, {force})`) updates files whose hash
  matches the marker or that equal the current template; **user edits are kept**
  (their hashes are never recorded). `force` overwrites after a `.md.bak` backup.
- `/planner-status` dialog: versions (running/install/config/agents/local copy/
  latest), conflicts, actions: Check for updates (GitHub `raw.githubusercontent`
  `package.json`, cached 24 h, silent offline, `PLANNER_NO_UPDATE_CHECK=1` skips
  the startup toast), Refresh agent definitions (legacy installs with no marker
  get an explicit "Overwrite with backups" prompt), Update now (confirm →
  `git rev-parse` gate → `git pull --ff-only` (abort, no reinstall, on failure) →
  `bash install.sh --yes`; log written on every path; restart notice).

---

## 3. Architecture / files

```
src/index.ts      server plugin: agent install, transforms, semaphore wrapper,
                  permission fallback, watchdog, /plan, context/shell/tool hooks,
                  config watcher (1.5 s)
src/core.ts       config schema/IO, defaults+snapshots, agent markers, version
                  compare/status, update check+execution, agent templates
src/semaphore.ts  async semaphore behind maxParallelSubagents
src/commands.ts   dialog operations shared by core and the TUI
src/tui.ts        CLI plugin: /planner-config + /planner-status, palette entries
index.ts tui.ts   root re-exports for plugin-directory discovery
agents-v2/*.md    planner/brainstormer/worker/tester/vision definitions
tests/e2e/*       suites (see §5)
install.sh        global installer (V2 only)
docs/             planner-plugin.md (internals), REQUIREMENTS.md (R1–R5 spec),
                  RELEASING.md, HANDOVER.md (this file)
```

Key OpenCode 2.0.26 facts learned on real instances:
- tools are `read`, `write`, `edit`, `patch`, `glob`, `grep`, `shell`, `question`,
  `subagent`, `skill`, `webfetch`, `websearch`, `opencode_*` (no `bash`, no
  `read_directory`, no `list_dir`).
- `session.prompt` **ignores** an `agent` field — use `session.switchAgent`.
- plugin hook payloads (`tool.execute.before`, `permission.evaluate`,
  `session.context`) are mutable and mutation is honored.
- `session.idle`/`session.status` are not delivered to plugin subscribers;
  `session.execution.*` are (per model step).
- plugin modules are cached per path for the server lifetime; a restart loads new
  code.
- the `subagent` tool accepts a per-call `model`, which overrides the agent pin.

---

## 4. Environment specifics (the user's machine)

- Real provider blocks in `~/.config/opencode/opencode.json`: **goproxy** (many
  models incl. `deepseek-v4.1-flash`, `glm-5.3-flash`, `deepseek-v4-flash`,
  `mimo-v2.5`), **strata** and **strata2** (both declare only
  `qwen3.8-flash-next-iq3_s`, openai-compatible local endpoints).
- User's projects: `/home/armen/mario`, `/home/armen/mario_kart`, … — these run
  local qwen models that **hallucinate** tool names (`bash`, `ask_user`,
  `list_dir`) and model ids (`openai/gpt-5.1-codex-max`).
- Global opencode plugins of the user: `tool-call-repair.js` (repairs `execute`
  code-mode misfires + snake_case args), `edit-recovery.js`, `commandcode-proxy.js`,
  and (added this session) **`tool-name-aliases.js`** — registers alias tools
  (`bash`→`shell`, `ask_user`→`question`, `list_dir`→`glob`, `read_file`/`write_file`/
  `edit_file`→`read`/`write`/`edit`, `task`→`subagent`, `web_search`/`web_fetch`→
  `websearch`/`webfetch`, …) + an `execute.before` rewrite. Validated live:
  registry 37 tools, aliases present.
- **Removed**: `@prevalentware/opencode-goal-plugin` (npm uninstall + config
  `plugin` arrays emptied + cache removed; backups `~/.config/opencode/*.bak-goal-removed-20261010-193409`).
  Its `update_goal_plan` schema (for reference) required `goal_id`,
  `expected_revision` (int, NOT `planRevision`), `plan`, `reason`; strict zod,
  ids `^[a-zA-Z0-9_-]{1,80}$`.
- `/tmp` is a 16 GB tmpfs that fills up; live suites write scratch homes there.

---

## 5. Tests — commands and expected tallies

Static/quick (no models), all in `tests/e2e/`:
```
bash tests/e2e/run_all.sh                 # unit_gate + semaphore + update + installer + v2
python3 tests/e2e/unit_gate.py            # 184/184
python3 tests/e2e/test_semaphore_unit.py  # 8/8    (bun/node harness)
python3 tests/e2e/test_update_unit.py     # 36/36  (fake git clones)
python3 tests/e2e/test_planner_config.py  # 36/36  (installer sandbox)
python3 tests/e2e/test_planner_v2.py      # 42/42  (real server, no model turns; PTY dialogs)
```
Live (real model via goproxy, minutes; NOT in run_all):
```
python3 -u tests/e2e/test_subagent_limit_live.py  # 9/9   cap: 2 sessions, waitedMs≈23s; raise→inFlight=2
python3 -u tests/e2e/test_delegation_live.py      # 20/20 watchdog: nudge visible/resumed, cap 2, toggle-off
python3 -u tests/e2e/test_agent_models_live.py    # 11/11 per-agent pins actually run (incl. missing project agents)
python3 -u tests/e2e/test_spec_write_live.py      # 5/5   spec file on disk + pipeline
python3 tests/e2e/long_session_test.py --label A --cycles 2   # real worker→tester cycles
```
Reports are written to `tests/e2e/*-report.json` (gitignored) and `/tmp/*.log`.
Live suites set `PLANNER_DEBUG=1` + `PLANNER_DEBUG_FILE` and read that file.

---

## 6. Everything changed in this session (chronological)

**R1 — enforced concurrency cap.** `src/semaphore.ts`; `subagent` tool wrapped via
`ctx.tool.transform` (queue, idempotent); permission-deny fallback; prompt block
says the cap is enforced; debug logs `subagent#N acquire/release`.
Validated: live 9/9, independently reproduced with DB timestamps.

**R2 — mandatory delegation watchdog.** `planner.md` brainstormer-first
unconditional + "you are not a builder"; watchdog (completed-call counting,
4 s settle, synthetic `resume` nudge, max 2, `enforceDelegation` toggle in TUI);
`session.prompt` hook resets turns. Validated: live 20/20 (a deterministic
zero-delegation provocation via the test-only `watchdog_probe_plugin.ts`).

**R3 — global defaults + per-project snapshot.** `planner-defaults.json`,
`ensureDefaultsFile`, `snapshotProjectConfig` (atomic `wx`), effective config,
`resyncProjectConfig`, TUI "Global defaults"/"Re-sync"/"Max cycles"; installer
creates defaults, keeps it on uninstall. Validated live (no counterexamples;
snapshot TOCTOU fixed with create-or-fail).

**R4 — versioning + one-click update.** `install.sh` records `sourceRoot` + agent
marker; `detectVersionStatus`, `/planner-status`, GitHub check (24 h cache),
`runUpdate` (aborts without reinstall on failed pull / non-repo; logs every path),
agent refresh hash-marker logic. Validator found 2 real bugs, both fixed:
repeat refresh clobbered user edits (kept files were hash-recorded), and the
manual check ignored a fresh cache. Validated: live dialog-driven update with a
fake clone; real config untouched.

**R5 — final battery + docs.** Version 0.5.0, CHANGELOG/README/docs/RELEASING,
`.gitignore` for report JSONs, full battery green, independent real-model smoke
(defaults → snapshot → full pipeline → verified artifact, serial cap, 0 errors),
docs audit passed.

**R6 — pipeline discipline + legacy upgrade.** `planner.md` "Keep it short — do
not overthink", goal-continuation handling; injected block extended; agent
refresh `{force}` with `.md.bak` backups + legacy-overwrite prompt in
`/planner-status` (old projects' agent files are otherwise kept).

**R7 — brainstormer spec write + questions.** First-try superpowers skills,
relative-path write, read-back verification, `STATUS: FAILED`, questions allowed
(`question` permission + `NEEDS_INPUT` protocol), planner relays (max 3 rounds),
`ensureSpecsDir` on every planner use. Validated live 5/5 (spec on disk, 8k chars,
pipeline ran).

**R8 — pinning + tool-name hardening.**
- **Model pins no longer gated on project agent files** (the `agentFileExists`
  gate made subagents silently inherit the planner's model — reproduced from the
  user's DB). Validated live 11/11 `test_agent_models_live.py`.
- **Subagent model overrides stripped** (planner injected
  `"model":"openai/gpt-5.1-codex-max"` → "Model not available").
- `planner.md`/injected block name exact tool names (`subagent`, `question`,
  `read`/`glob`/`grep`; never `ask_user`).
- Global `~/.config/opencode/plugins/tool-name-aliases.js` created (outside the
  repo) + validated live (registry 37 tools).

---

## 7. Repo state / artifacts

- Uncommitted changes: modified `.gitignore`, `CHANGELOG.md`, `README.md`,
  `agents-v2/planner.md`, `docs/planner-plugin.md`, `install.sh`, `package.json`
  (0.5.0), `src/{core,index,tui}.ts`, `tests/e2e/{long_session_test,run_all,
  test_planner_config,test_planner_v2,unit_gate}.py`; new `src/semaphore.ts`,
  `docs/REQUIREMENTS.md`, `docs/HANDOVER.md`, `tests/e2e/{semaphore_unit.ts,
  test_semaphore_unit.py,update_unit.ts,test_update_unit.py,
  test_delegation_live.py,test_subagent_limit_live.py,test_agent_models_live.py,
  test_spec_write_live.py,watchdog_probe_plugin.ts}`.
- No commit has been made yet (the user decides). If committing, end the message
  with the project's usual trailer only if the user asks for it.

---

## 8. Open items / next steps

1. **Deploy** (nothing from R1–R8 is live in the user's real sessions):
   `cd ~/IdeaProjects/opencode-planner && ./install.sh --yes`, restart OpenCode,
   then `/planner-status → Refresh agent definitions` in each old project (mario's
   `.opencode/agents` was deleted at some point — the plugin recreates it on load,
   and the legacy refresh offers "Overwrite with backups").
2. **Commit** the 0.5.0 working tree if the user wants it.
3. Not yet validated end-to-end with a real model: the **question round**
   (brainstormer actually asking; `NEEDS_INPUT` relay or `question` tool from a
   subagent) — the protocol and permissions are in place; write a live test that
   provokes an ambiguous request and asserts the planner asked + re-delegated.
4. Not yet validated with a real model: the **model-override strip** (needs a
   model that hallucinates a `model` field again).
5. Functional (not just registration) validation of the alias plugin with a real
   model calling `bash`/`ask_user`.
6. `long_session_test.py` was not re-run after R6–R8; consider it in the next
   battery.
7. Consider adding the override-strip to the global alias plugin too, so
   non-planner agents get it.
8. Optional polish: `unit_gate` asserts `0.5.x`; bump to exact `0.5.0` if desired.

---

## 9. Working rules for the next agent (how this user wants it done)

- **Implement → validate on a REAL OpenCode instance** after every step (the user
  explicitly requires this), preferably by a separate validator subagent that
  reproduces evidence independently and tries to falsify.
- Keep the gates green: `tsc --noEmit`, `run_all.sh`, and any live suite the
  change touches. Update `CHANGELOG.md`/`README.md`/`docs/planner-plugin.md` when
  behavior changes.
- When a model misbehaves, **look at the real evidence first**: the session DB
  `~/.local/share/opencode/opencode.db` (tables `session_v2`, `session_message`;
  read-only `mode=ro`) shows exact tool calls, inputs, errors, child sessions and
  their models. Most "impossible" bugs in this session were model hallucinations
  (tool names, model ids, home-dir typos), not code faults.
- Ground every claim in either the code, a test tally, or DB/log evidence; say
  explicitly what is *not* validated.
