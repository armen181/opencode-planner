# Contributing

Thanks for your interest in improving **planner**. This document covers local
setup, the test suites, the project layout, and what a pull request should look
like.

## Prerequisites

- `bash` — the install script is a bash script
- `python3` — the test suites are Python; the installer itself has no Python
  dependency
- [OpenCode](https://opencode.ai) **2.x** — needed to run the live/TUI suites
  (`opencode2`/`opencode` on PATH, or point `PLANNER_V2_BIN` at a V2 binary)
- `node` + `npm` — only for `npx tsc --noEmit` (types come from the
  `@opencode/plugin` devDependency)

## Local install (development)

Copy the package as a plugin directory into the project you are testing:

```bash
mkdir -p /path/to/project/.opencode/plugins/opencode-planner
cp -R src agents-v2 index.ts tui.ts package.json \
      /path/to/project/.opencode/plugins/opencode-planner/
```

No `npm install` is needed — the plugin has no runtime dependencies. Restart
OpenCode, then press **Tab** to reach the Planner agent (it is opt-in and not
the default).

To install globally instead:

```bash
./install.sh --yes     # → ~/.config/opencode/plugins/opencode-planner/ + agents
./install.sh uninstall
```

## Running the tests

```bash
# Static (fast, no opencode needed)
python3 tests/e2e/unit_gate.py
python3 tests/e2e/test_planner_config.py

# Live OpenCode 2 server: plugin/agents/opt-in/MCP/commands + TUI dialogs
python3 tests/e2e/test_planner_v2.py

# Everything above
bash tests/e2e/run_all.sh

# Long real-model session (needs a live provider; takes minutes)
python3 tests/e2e/long_session_test.py --label A --cycles 2
```

The live suites boot their own server with a private `HOME`, so host-level
OpenCode state cannot leak in. `test_planner_v2.py` skips itself when no V2
binary is available.

## Typecheck

```bash
npx tsc --noEmit
```

`src/` is included; the config uses `moduleResolution: Bundler` (extensionless
relative imports, as OpenCode's loader resolves them).

## Project layout

```
src/
  index.ts      V2 server plugin (agent install, transforms, hooks, commands, MCP control)
  core.ts       config schema/IO (atomic), model specs, agent templates, vision texts
  commands.ts   /planner-* logic + user-facing texts (server + TUI share this)
  tui.ts        V2 CLI plugin: /planner-* popup dialogs (package "./tui")
index.ts tui.ts root re-exports for plugin-directory discovery
agents-v2/      V2 agent definitions (planner/brainstormer/worker/tester/vision)
tests/e2e/      Python suites
install.sh      global installer/uninstaller (V2)
docs/           documentation
```

Keep the config schema and command texts **single-sourced** in `src/core.ts` /
`src/commands.ts`; the server plugin and the TUI dialogs must never drift.

## Commit style

Use [Conventional Commits](https://www.conventionalcommits.org/), matching the
existing history:

- `feat:` — a new user-facing capability
- `fix:` — a bug fix
- `docs:` — documentation only
- `chore:` — maintenance, packaging, cleanup
- `test:` — tests only

Keep the subject in the imperative mood and use a short body to explain *why*
when the change is not obvious.

## Pull requests

- Run `python3 tests/e2e/unit_gate.py`, `test_planner_config.py`, and — when
  you touch hooks/agents/TUI — `test_planner_v2.py`; say which you ran.
- Add or update tests in `tests/e2e/` for behavior changes where practical.
- Update `README.md` and `CHANGELOG.md` when the change is user-facing.
- There is no CI: reviewers rely on your local test results.
