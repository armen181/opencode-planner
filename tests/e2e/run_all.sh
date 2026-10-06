#!/usr/bin/env bash
# Run all planner e2e test suites.
#
#   ./run_all.sh              # static + live V2 suites (no model turns)
#   ./run_all.sh unit_gate    # one suite
#
# The long real-model session suite is NOT part of the default run (it needs a
# live provider and takes minutes); run it explicitly:
#   python3 long_session_test.py --label A --cycles 2
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

suites=(unit_gate test_planner_config test_planner_v2)

if [ "${1:-}" ]; then
  suites=("$1")
fi

overall=0
for suite in "${suites[@]}"; do
  echo ""
  echo "================ $suite ================"
  if python3 "$HERE/$suite.py"; then
    echo "[PASS] $suite"
  else
    echo "[FAIL] $suite"
    overall=1
  fi
done

echo ""
if [ "$overall" -eq 0 ]; then
  echo "[SUCCESS] All planner e2e suites passed."
else
  echo "[FAIL] One or more planner e2e suites failed."
fi
exit "$overall"
