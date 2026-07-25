## What does this change do, and why?

<!-- One or two sentences. Link an issue if there is one. -->

## Checklist

- [ ] Tests added/updated for any engine or service logic change (`pytest` passes)
- [ ] `ruff check src tests` passes
- [ ] The Engine Core (`src/tradr/engine/`) is still pure — no I/O, clock, or network added
- [ ] No personal/financial data, secrets, or `.env` values included in this diff
- [ ] If a ruleset rule/parameter changed: [RULESET.md](../RULESET.md) is updated to match
- [ ] Docs updated if user-facing behavior changed (README / ARCHITECTURE)

## How was this tested?

<!-- e.g. "added 3 unit tests for the new band" / "ran the UI locally against a fresh DB" -->
