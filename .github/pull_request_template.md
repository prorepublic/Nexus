# Summary

<!-- What does this change deliver, in one or two sentences? -->

## Linked work

- Nexus goal/task IDs (if orchestrated):
- GitHub issue:

## Validation evidence

- [ ] `make validate` passes locally (lint, typecheck, unit tests, build)
- [ ] Integration tests pass (`make test-integration`) or are not affected
- [ ] New behavior is covered by tests
- [ ] Documentation updated where behavior changed

## Security checklist

- [ ] No secrets, tokens, or .env content in the diff
- [ ] No new paid service, metered API, or billing change
- [ ] Subprocess calls use argv lists through the command policy
- [ ] Model/agent output is treated as untrusted input

## Notes for reviewers

<!-- Risks, trade-offs, follow-ups. -->
