# Releasing planner

Maintainer runbook for publishing planner (git tag + npm package).

**Current state: not published to npm.** The npm name
`@armen181/opencode-planner` does not exist on the registry yet, so the primary
install path today is a plugin directory (see the README). Publishing to npm is
a separate, deliberate step.

## Pre-release checklist

- [ ] `git status` is clean and `git log --oneline -3` shows the release commits.
- [ ] `package.json` version matches `CHANGELOG.md`'s newest section.
- [ ] `npx tsc --noEmit` passes.
- [ ] Static suites pass: `python3 tests/e2e/unit_gate.py`,
      `python3 tests/e2e/test_planner_config.py`.
- [ ] Live suite passes on a real OpenCode 2 binary:
      `python3 tests/e2e/test_planner_v2.py`.
- [ ] Long real-model validation passes:
      `python3 tests/e2e/long_session_test.py --label A --cycles 2`.
- [ ] No secrets committed in the tree.

## Git tag / GitHub release

```bash
git tag -a vX.Y.Z -m "planner vX.Y.Z"
git push origin master
git push origin vX.Y.Z
```

Create the GitHub Release from the tag and copy the matching `CHANGELOG.md`
section into its body.

## Publish to npm (when ready)

The package ships `src/`, `agents-v2/`, `install.sh`, `README.md`, `LICENSE`
(see `files` in `package.json`); the server and TUI entrypoints are the `"."`
and `"./tui"` exports. Once published, the README's npm install path
(`"plugins": ["@armen181/opencode-planner"]` in `opencode.json(c)`) becomes
valid — OpenCode installs the package and loads both entrypoints.

```bash
npm publish --access public
```

## Post-publish smoke test

From a checkout, install the published package into an empty scratch project's
`opencode.json(c)`:

```jsonc
{ "plugins": ["@armen181/opencode-planner"] }
```

Restart OpenCode and confirm: `opencode api plugin.list` shows `planner`
active; the five agents exist; a normal session is NOT on the planner; pressing
Tab reaches Planner; `/planner-status` opens the TUI dialog and reports
`opt-in` + `mcp: disabled`.

## Preparing the next release

1. Make the changes and add a new `CHANGELOG.md` version section.
2. Bump `package.json` `version` to match.
3. Commit with a `chore(release): ...` message.
4. Tag and publish as above.
