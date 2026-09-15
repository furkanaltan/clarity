#!/usr/bin/env bash
set -u

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON_BIN="${ROVE_PYTHON:-python3}"
SUITE="${1:-full}"

export ROVE_FRONTEND_PATH="${ROVE_FRONTEND_PATH:-$ROOT/frontend/index.html}"
export PYTHONPYCACHEPREFIX="${PYTHONPYCACHEPREFIX:-${TMPDIR:-/tmp}/rove-pycache}"

if [ -n "${ROVE_EXTRA_PYTHONPATH:-}" ]; then
  export PYTHONPATH="$ROVE_EXTRA_PYTHONPATH:$ROOT${PYTHONPATH:+:$PYTHONPATH}"
else
  export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"
fi

cd "$ROOT" || exit 1

run_files() {
  "$PYTHON_BIN" -m unittest "$@"
}

case "$SUITE" in
  full)
    "$PYTHON_BIN" -m unittest discover -p 'test_*.py'
    ;;
  auth)
    run_files \
      test_auth_pin_sprint9_phase2.py \
      test_auth_sprint9.py \
      test_frontend_cookie_auth_9_1b.py \
      test_frontend_pin_sprint9_phase2.py \
      test_stability_sprint8.py \
      test_state_link_security_9_1.py
    ;;
  finance)
    run_files \
      test_coach_savings_copy.py \
      test_etf_contribution_assignment.py \
      test_financial_accounts_sprint1.py \
      test_financial_accounts_sprint2.py \
      test_financial_accounts_sprint3.py \
      test_monthly_checkin_v1.py
    ;;
  frontend)
    run_files \
      test_coach_savings_copy.py \
      test_feature_announcements_sprint2.py \
      test_final_fix_before_sprint3.py \
      test_frontend_cookie_auth_9_1b.py \
      test_frontend_pin_sprint9_phase2.py \
      test_quick_capture_close.py
    ;;
  reports)
    run_files \
      test_report_render_v2.py \
      test_report_snapshot_v2.py \
      test_report_story_v2.py
    ;;
  stability)
    run_files \
      test_stability_sprint1.py \
      test_stability_sprint5.py \
      test_stability_sprint6.py \
      test_stability_sprint7.py \
      test_stability_sprint8.py \
      test_state_link_security_9_1.py
    ;;
  list)
    printf '%s\n' full auth finance frontend reports stability
    ;;
  *)
    printf 'Unknown test suite: %s\n' "$SUITE" >&2
    printf 'Available: full, auth, finance, frontend, reports, stability, list\n' >&2
    exit 2
    ;;
esac
