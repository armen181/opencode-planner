# Summary

<!-- One or two sentences: what does this PR change, and why? -->

## Changes

-

## How tested

<!--
Which suites did you run? For example:
  python3 tests/e2e/unit_gate.py
  python3 tests/e2e/test_planner_config.py
  python3 tests/e2e/test_planner_v2.py
The live suite boots its own OpenCode 2 server; note the opencode version you used.
-->

## Checklist

- [ ] `python3 tests/e2e/unit_gate.py` passes
- [ ] `python3 tests/e2e/test_planner_config.py` passes
- [ ] `python3 tests/e2e/test_planner_v2.py` passes (or explain why it was skipped)
- [ ] `npx tsc --noEmit` passes
- [ ] `CHANGELOG.md` updated for user-facing changes
- [ ] Config logic stays single-sourced in `src/core.ts` / `src/commands.ts`
