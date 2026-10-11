# Planner v0.5.0 — Requirements

Source: brainstorming session, 2026-10-10. Target release: **v0.5.0** (OpenCode 2.x).
Scope: four features, each implemented as one point and verified on a real OpenCode
instance plus e2e tests. Implement top to bottom; each point lists its own acceptance
criteria and the tests that must pass.

> **Note (post-development).** This document captured the original R1–R5
> requirements. Later increments are recorded in `CHANGELOG.md` (0.5.0) and
> `docs/HANDOVER.md`; where they differ, the later text wins. Notable
> differences: the brainstormer now **asks clarifying questions**
> (`question` tool or `STATUS: NEEDS_INPUT` relayed by the planner, max 3
> rounds) instead of being fully autonomous; it writes **and verifies** its
> spec file on disk (missing file ⇒ `STATUS: FAILED`, never READY); the
> planner keeps short steps, treats goal/loop continuation prompts as
> "continue the pipeline", and never passes a `model` to `subagent` calls
> (the plugin strips overrides); agent-model pins are not gated on project
> agent files; legacy project agent files can be refreshed with backups.

## Locked decisions (from Q&A)

1. **Serial enforcement** — a hard cap, not just prompt text: 1 → only one subagent
   task runs at a time; 2 → at most two. Extra calls wait for a free slot (deny-and-retry
   is the fallback if slot-waiting cannot be wired).
2. **Delegation** — brainstormer-first, **always**; every planner request goes through
   the subagents, code changes through worker + tester; the plugin nudges the planner
   when it ends a turn without delegating.
3. **Config** — snapshot model: global defaults file seeds a full per-project copy on
   first planner use, plus a "re-sync from defaults" action.
4. **Versioning** — detect version conflicts (project config, agent definitions,
   project-local plugin copy, global install, latest on GitHub), surface them in
   `/planner-status`, and offer a one-click **pull + reinstall** update.
5. **Validation** — every point is verified on a real OpenCode instance, and e2e tests
   are added to `tests/e2e/` covering all new functionality.

---

## R1 — Hard concurrency cap for subagents (`maxParallelSubagents`)

**Problem.** `maxParallelSubagents` is only injected as prompt text (the
`# Subagent execution` block in the `session.context` hook, `src/index.ts`). Nothing
stops the planner from firing several `subagent` tool calls at once. Verified: the
OpenCode 2.0.26 tool is literally named `subagent`; the plugin API exposes the needed
levers.

**Requirements.**

1. A semaphore limits **concurrently executing** `subagent` tool calls to
   `cfg.maxParallelSubagents` (read live at acquire time; 1–16).
2. Primary mechanism: `ctx.tool.transform` wraps the `subagent` tool's `execute`
   (`editor.update("subagent", …)`); the wrapper acquires a slot before calling the
   original execute and releases it in `finally`. Wrapping must be idempotent — a tool
   transform can be replayed and must never double-wrap.
3. Fallback if the tool is not wrappable at runtime (registration timing): the
   `ctx.permission.hook("evaluate")` path sets `effect = "deny"` + explanatory
   `message` when a call would exceed the cap. In-flight calls are tracked with
   `ctx.tool.hook("execute.before"/"execute.after")` for `tool === "subagent"`
   (and/or the `session.tool.called` / `session.tool.success|failed` events).
4. Enforcement applies to calls made by the **planner** session only (hooks expose
   `agent`); subagents cannot delegate anyway.
5. The prompt block stays (so the model plans for serial/parallel) but its wording
   matches enforced behavior ("extra calls wait/are rejected; do not rely on
   parallelism").
6. Concurrency is observable for tests: debug logging gated by an env var
   (e.g. `PLANNER_DEBUG=1`) recording acquire/release per subagent task with
   timestamps and session ids.

**Acceptance criteria.**

- [ ] With `maxParallelSubagents: 1`, two simultaneous `subagent` calls never overlap;
      the second starts only after the first finishes (or is denied and retried, with
      the deny message visible in the transcript).
- [ ] With `maxParallelSubagents: 2`, three calls show a maximum overlap of two.
- [ ] Changing the value in `/planner-config` takes effect without restart (live config
      watcher path).
- [ ] No double-wrapping / no double-release after `ctx.tool.reload()` or config reload.
- [ ] Other agents' `subagent` usage (if any) is unaffected.

**Tests.** Unit-test the semaphore in isolation (new small module, e.g.
`src/semaphore.ts`). Live e2e (env-gated like `long_session_test.py`): a real model is
asked to "delegate two independent tasks at the same time"; the debug log proves
no-overlap with cap 1 and max-overlap-2 with cap 2.

---

## R2 — Mandatory delegation: brainstormer first, always

**Problem.** The workflow (brainstormer → spec check → worker → result check → tester)
is prompt-only. The planner can answer or act without ever calling a subagent, and
nothing notices. The user has to spell out "use the subagents" — pointless vs. a
plain single agent.

**Requirements.**

1. Update `agents-v2/planner.md`: the brainstormer step is **unconditional** — remove
   the "non-trivial only / skip for trivial asks" escape for the planner. Only the
   brainstormer classifies a request as `STATUS: TRIVIAL`; the planner never makes that
   call itself. Add an explicit rule: "You are not a builder — every request is handled
   by subagents; you only coordinate and verify."
2. The injected `# Step control` block states the same contract (brainstormer first,
   worker + tester for every change, planner writes nothing).
3. **Plugin watchdog** (server-side, per root session):
   - Track planner user turns (state keyed by root `sessionID`; reset on each new
     prompt via `ctx.session.hook("prompt")`).
   - Observe `subagent` calls (events and/or tool hooks) during the turn.
   - When a planner root session goes idle (`session.status`/`session.idle` events) and
     the turn made **zero** `subagent` calls → inject a short corrective instruction
     and continue the turn (bounded: at most 2 nudges per user turn; nudge text must
     read as a system directive, not as user content).
   - Rule 2 (best-effort): if the only subagent call was the brainstormer and it
     returned `STATUS: READY`, a turn that ends without any worker/tester call gets one
     nudge to run the worker→tester pipeline. `STATUS: TRIVIAL` (captured from the
     brainstormer child session's final text) ends the turn normally.
   - Watchdog can be disabled with a config key (`enforceDelegation`, default **true**),
     also shown in `/planner-config`.
4. Missing subagent definitions (agent files absent) must not cause loops: the plugin
   logs a warning and skips nudging for that turn.

**Acceptance criteria.**

- [ ] A question ("explain X") sent to the planner produces a brainstormer child
      session; `TRIVIAL` is allowed to end the turn.
- [ ] A change request produces brainstormer → worker → tester child sessions.
- [ ] A planner turn that tries to finish without delegating receives the nudge and the
      turn continues with the required delegation (provoked on a real instance with a
      weak/obedient-to-test model).
- [ ] No more than 2 nudges per user turn; no nudge loops when the model ignores them.
- [ ] `enforceDelegation: false` disables nudging entirely.

**Tests.** Live e2e on a real instance: run the three scenario prompts above and assert
the session tree (child sessions per agent) + nudge counts from the debug log. Static
gate asserts `planner.md` no longer contains the skip-for-trivial escape.

---

## R3 — Global defaults + per-project snapshot

**Problem.** Defaults are hardcoded in `src/core.ts` (`defaultPlannerConfig()`); a
project config only appears when `/planner-config` writes one. There is no global file
to edit and no per-project copy seeded on first planner use.

**Requirements.**

1. **Global defaults file**: `<CONFIG_DIR>/planner-defaults.json`
   (`CONFIG_DIR` = `OPENCODE_CONFIG_DIR` || `XDG_CONFIG_HOME/opencode` ||
   `~/.config/opencode`). Same settings schema as the project file, but **no**
   project metadata (no `plannerVersion`). Auto-created from built-in defaults when
   missing (plugin load and `install.sh`); never silently rewritten after creation.
2. **Effective config resolution** (read path):
   project file → per-key fallback to global defaults → built-in defaults. A malformed
   global file logs a warning and falls back to built-ins without crashing.
3. **Snapshot on first planner use**: when a project has no
   `.opencode/planner-plugin.json` and the planner is first used there (first
   planner-agent request via the `session.context` hook, or `/plan`), write a full copy
   of the current global defaults into the project, plus metadata:
   `plannerVersion` (R4), `createdAt`, `source: "defaults"`. Must be created at most
   once and race-safe (only when the file is missing).
4. Existing configs keep working: partial/older files fall back per key; unknown keys
   survive every write (current behavior, keep it).
5. **Re-sync action** in the `/planner-config` dialog: "Re-sync from defaults" replaces
   the project file with a fresh snapshot of the global defaults after an explicit
   confirmation that project-specific edits will be lost. Shows a toast with what was
   applied.
6. **TUI "Global defaults" screen** (from `/planner-config`): edit the same settings as
   the project dialog (models/variants/vision/cycles/parallelism/enforceDelegation)
   against `planner-defaults.json`. Dialog title makes clear it affects projects seeded
   afterwards, not existing snapshots.
7. Screenshot dir / skillsPath semantics unchanged; screenshot notes behave as today.

**Acceptance criteria.**

- [ ] Fresh machine: first load creates `planner-defaults.json` with the built-in
      defaults.
- [ ] Fresh project: the first planner request creates `.opencode/planner-plugin.json`
      as a full snapshot (identical settings to the defaults at that moment) with
      metadata.
- [ ] Editing the global defaults afterwards does **not** change an existing project
      until "Re-sync from defaults" is used.
- [ ] A project that never ran the planner has no config file.
- [ ] Re-sync restores a project to the current defaults in one action.
- [ ] All writes stay atomic and preserve unknown keys.

**Tests.** Extend `test_planner_config.py` (installer sandbox: defaults file created,
marker fields) and `test_planner_v2.py` (real server: snapshot on first planner request,
no file without planner use, re-sync via dialog path/invoking the shared operation).

---

## R4 — Plugin versioning, status, and one-click update

**Problem.** Version lives only in `package.json` and the global
`.planner-install.json`. Project configs and agent files carry no version; a project
pinned to an old copy (or an old agent set) is never flagged.

**Requirements.**

1. **Version stamps**
   - `install.sh` writes `sourceRoot` (the path it installed from) into
     `~/.config/opencode/plugins/opencode-planner/.planner-install.json`, next to the
     existing `version`/`installedAt`.
   - Every plugin write of the project config stamps `plannerVersion` (version read
     from `packageRoot()/package.json`, fallback `"0.0.0"`) and `updatedAt`.
   - Agent definitions get a marker: `.planner-agents.json` next to the global agent
     files and in `<project>/.opencode/agents/` (`version`, `writtenAt`). Updated when
     the plugin writes/refreshes agent files.
2. **Mismatch detection** (compared against the running plugin version):
   - project config `plannerVersion` differs → "config written by vX, running vY";
   - project agent marker differs (or is missing on a project with agent files) →
     "agent definitions need refresh";
   - project-local plugin copy `<project>/.opencode/plugins/opencode-planner/package.json`
     exists with a different version → flag it explicitly (and verify on a real instance
     what happens when it loads alongside the global plugin: double hooks must not
     happen — either stand down or warn);
   - global install version differs from the running plugin → flag.
3. **Latest available check** — GitHub repo `armen181/opencode-planner`
   (raw `package.json` on the default branch; tags API allowed). Runs in the background
   at most once per 24 h (cache in `CONFIG_DIR/planner-update-check.json`), silent on
   network failure, plus a manual "Check for updates" button. Never sends anything
   beyond the read-only request.
4. **`/planner-status`** (TUI dialog + palette entry, registered by `src/tui.ts`):
   shows running version, defaults file path, project config version, agent-defs
   version, project-local copy version (if present), global install version, latest
   available version, and the update state ("up to date" / "update available vX" /
   "restart required").
5. **One-click update** ("Update now", shown when a newer version is available):
   - runs `git -C <sourceRoot> pull --ff-only`, then `bash <sourceRoot>/install.sh --yes`;
   - `<sourceRoot>` comes from `.planner-install.json`; abort with a clear message if
     the path is missing/not a repo;
   - user-triggered only — never unattended; command output captured and shown
     (progress + error toast with the log path);
   - afterwards: offer to refresh the project's agent definitions (R4.2) and to re-sync
     config if its version lags; always report "restart OpenCode to apply" (running
     instances keep the old plugin code).
6. After a restart with an updated plugin, all stamps match and `/planner-status`
   reports "up to date".

**Acceptance criteria.**

- [ ] Running the installer records `sourceRoot`; a second install over the same target
      updates version + timestamp.
- [ ] A project whose config was written by an older version shows the mismatch in
      `/planner-status` and a one-time toast in that project.
- [ ] Agent-def refresh updates only files that are unmodified templates; user-edited
      files are kept (or backed up with explicit confirmation).
- [ ] With a newer tag on GitHub (simulated in tests with a fake source repo), "Update
      now" pulls, reinstalls, and the status flips to "restart required", then to "up
      to date" after restart.
- [ ] Offline / GitHub failure: no error spam, status shows "check unavailable".

**Tests.** `test_planner_config.py`: installer records `sourceRoot`, creates markers.
`test_planner_v2.py`: `/planner-status` present; version mismatch shown for a
downgraded fixture config; update runs against a **fake local git repo** with a stub
`install.sh` (no network). Unit gate: version comparison helper (including
prerelease-suffix handling).

**Note (decision):** `uninstall` removes the plugin, markers, and update cache, but
keeps `planner-defaults.json` (user-editable content) — documented in `install.sh
--help` output.

---

## R5 — Validation: real instance + e2e coverage

1. Every point above ships with its e2e tests in `tests/e2e/` (static gate additions,
   installer sandbox, real-server suite, env-gated live-model suite) and all existing
   suites keep passing (`tests/e2e/run_all.sh`).
2. Implementation is only "done" after a manual pass on a real OpenCode 2.x instance
   in a scratch project: install, restart, and verify R1–R4 live (evidence: plugin
   debug log, session tree, `/planner-status` output, config file contents).
3. `CHANGELOG.md` gains a 0.5.0 entry; `package.json` version bumps to 0.5.0;
   `docs/planner-plugin.md` and `README.md` document the new config keys, files, and
   the update flow.

---

## New config keys (project file and global defaults)

| Key | Default | Meaning |
|---|---|---|
| `plannerVersion` | (stamp) | Version that last wrote this project file — project files only |
| `enforceDelegation` | `true` | Watchdog nudges when the planner skips delegation |
| `maxParallelSubagents` | `1` | **Enforced** cap (was advisory) |

## Risks / open technical items (verify first, on the real instance)

- Wrapping the built-in `subagent` tool via `ctx.tool.transform` depends on
  registration timing — verify the wrapper is installed on OpenCode 2.0.26 before
  building on it; otherwise use the permission-deny fallback (keep both paths).
- The exact nudge delivery (steer vs. synthetic + continue) must be validated live so
  the watchdog cannot wedge a session.
- Double-load behavior with a project-local plugin copy must be tested, not assumed.
- GitHub raw/tags requests are rate-limited — cache and fail silently.

## Non-goals

- No change to MCP handling, opt-in gating, vision modes, or screenshot behavior.
- No telemetry of any kind; the update check is a single read-only request.
- OpenCode V1 support stays removed.
