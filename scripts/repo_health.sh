#!/usr/bin/env bash
set -u

ROOT="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT" || exit 1

problems=0
warn() {
  printf 'WARNING: %s\n' "$1"
  problems=$((problems + 1))
}

printf 'repo=%s\n' "$ROOT"
printf 'branch=%s\n' "$(git branch --show-current 2>/dev/null || printf UNKNOWN)"
printf 'head=%s\n' "$(git rev-parse --short HEAD 2>/dev/null || printf UNKNOWN)"

staged="$(git diff --cached --name-only)"
untracked="$(git status --porcelain | awk '$1 == "??" {sub(/^\?\? /, ""); print}')"
printf '%s\n' 'staged_files:'
if [ -n "$staged" ]; then printf '%s\n' "$staged"; else printf '%s\n' '(none)'; fi
printf '%s\n' 'untracked_files:'
if [ -n "$untracked" ]; then printf '%s\n' "$untracked"; else printf '%s\n' '(none)'; fi

if printf '%s\n' "$staged" | grep -Ev '^$|^(ARCHITECTURE\.md|PROJECT_MAP\.md|README\.md|RUNBOOK\.md|docs/PROJECT_RULES\.md|scripts/repo_health\.sh|report_templates/rove_web_report\.html)$' | grep -q .; then
  warn 'unexpected staged file present'
fi
if printf '%s\n' "$untracked" | grep -Ev '^$|^(CLAUDE\.md|docs/PROJECT_RULES\.md|scripts/repo_health\.sh)$' | grep -q .; then
  warn 'unexpected untracked file present'
fi

printf '%s\n' 'root_entries:'
find . -maxdepth 1 -mindepth 1 -print | sed 's#^./##' | sort

printf '%s\n' 'generated_candidates:'
find . -path './.git' -prune -o -type f \( \
  -name '*.backup*' -o -name '*.bak' -o -name '*.old' -o -name '*.orig' \
  -o -name '*.tmp' -o -name '*.copy' -o -name '*.log' -o -name '*.db-wal' \
  -o -name '*.db-shm' -o -name '*.pyc' -o -name '.DS_Store' \
\) -print | sort | head -100

printf '%s\n' 'large_files_over_25MB:'
find . -path './.git' -prune -o -type f -size +25M -print | sort

required=(
  frontend/index.html scripts/test.sh docs/TESTING.md docs/MIGRATIONS.md
  docs/DEPENDENCIES.md docs/DEPLOYMENT.md docs/ENVIRONMENT.md
  docs/BOT_LEGACY.md docs/PROJECT_RULES.md
)
for file in "${required[@]}"; do
  if [ -f "$file" ]; then
    printf 'required=%s OK\n' "$file"
  else
    warn "missing required file: $file"
  fi
done

printf '%s\n' 'suspicious_index_files:'
find . -path './.git' -prune -o -type f -name 'index*.html' -print | sort

if [ -f frontend/index.html ]; then
  printf 'frontend_canonical=OK\n'
else
  warn 'canonical frontend missing'
fi
if [ -f scripts/test.sh ]; then
  printf 'test_runner=OK\n'
else
  warn 'test runner missing or not executable'
fi

if git diff --check; then
  printf 'git_diff_check=OK\n'
else
  warn 'git diff --check failed'
fi

if [ "$problems" -eq 0 ]; then
  printf 'repo_health=OK\n'
  exit 0
fi
printf 'repo_health=WARNING problems=%s\n' "$problems"
exit 1
