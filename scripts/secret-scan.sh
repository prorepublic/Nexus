#!/usr/bin/env bash
# Repository secret scan (free, no external service).
# Scans tracked files for known credential patterns; exits 1 on findings.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

PATTERNS=(
  'gh[pousr]_[A-Za-z0-9]{20,}'          # GitHub tokens
  'ntn_[A-Za-z0-9]{20,}'                 # Notion internal integration tokens
  'sk-(ant|proj)-[A-Za-z0-9-]{20,}'      # Anthropic/OpenAI API keys
  'AKIA[0-9A-Z]{16}'                     # AWS access keys
  '-----BEGIN (RSA|EC|OPENSSH|PGP) PRIVATE KEY'
  'xox[baprs]-[A-Za-z0-9-]{10,}'         # Slack tokens
)

# Files that legitimately DESCRIBE these patterns (scanners, redaction, tests).
ALLOWLIST_RE='(^|/)(secret-scan\.sh|observability\.py|validation\.py|test_redaction\.py|test_execution\.py|test_notion_sync\.py|test_policies\.py|SECURITY\.md|THREAT-MODEL\.md)$'

status=0
for pattern in "${PATTERNS[@]}"; do
  matches=$(git grep -I -l -E "$pattern" -- . 2>/dev/null | grep -Ev "$ALLOWLIST_RE" || true)
  if [ -n "$matches" ]; then
    echo "POSSIBLE SECRET (pattern: $pattern):"
    echo "$matches" | sed 's/^/  /'
    status=1
  fi
done

if [ "$status" -eq 0 ]; then
  echo "secret scan: clean"
fi
exit "$status"
