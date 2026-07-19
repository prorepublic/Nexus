#!/usr/bin/env bash
# One-time GitHub repository setup for Nexus.
# Requires: authenticated gh CLI with repo scope. Idempotent.
set -euo pipefail

REPO="${1:-prorepublic/Nexus}"

# Plain name=color pairs: macOS ships bash 3.2, which lacks associative arrays.
LABELS="
type:goal=1D76DB
type:feature=0E8A16
type:bug=D93F0B
type:architecture=5319E7
type:security=B60205
type:documentation=0075CA
status:ready=C2E0C6
status:running=FBCA04
status:blocked=D93F0B
status:review=BFD4F2
status:done=0E8A16
worker:claude=8250DF
worker:codex=1F883D
worker:auto=6E7781
risk:low=DDF4FF
risk:medium=FFF8C5
risk:high=FFEBE9
approval:required=B60205
"

echo "Bootstrapping Nexus labels on ${REPO}..."
for entry in $LABELS; do
  name="${entry%%=*}"
  color="${entry##*=}"
  gh label create "$name" --repo "$REPO" --color "$color" --force >/dev/null \
    && echo "  label: $name"
done

echo
echo "Recommended branch protection for 'main' (requires admin; apply manually or"
echo "uncomment below): require PR review, require CI status checks"
echo "'Control plane (lint, types, tests)' and 'Web dashboard (lint, types, build)',"
echo "disallow force pushes and deletions."
echo
echo "Recommended milestones: 'M1: Worktree + approval wiring',"
echo "'M2: Notion sync + GitHub PR loop', 'M3: Hybrid remote monitoring'."
echo "Create with: gh api repos/${REPO}/milestones -f title='...'"
echo
echo "Done."
