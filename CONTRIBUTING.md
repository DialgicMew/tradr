# Contributing to tradr

Thanks for your interest! tradr is an advisory-only, self-hosted investing assistant.
Contributions that improve correctness, safety, docs, and provider coverage are very
welcome.

## Ground rules

- **Never commit personal data.** No real holdings, broker statements/screenshots,
  `.env` files, or databases. These are git-ignored — keep them that way. PRs adding
  personal financial data will be rejected.
- **The engine stays pure.** `src/tradr/engine/` must have no I/O, no clock, no network,
  no DB. Time and prices are passed *in*. This is what makes it testable and
  backtestable — don't break it.
- **Advisory-only boundary is non-negotiable.** The system must never place orders, move
  money, or connect to a brokerage to execute. It only ever *suggests*.
- **Respect the ruleset.** Locked parameters and precedence rules in
  [RULESET.md](RULESET.md) were deliberated. If you think one is wrong, open an issue to
  discuss — don't quietly change behavior.

## Dev setup

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                     # run the test suite
ruff check src tests       # lint (enforced in CI)
mypy src                   # type-check (best-effort; not yet CI-enforced — there's
                           # existing type-annotation debt, so don't worry about
                           # pre-existing errors unrelated to your change)

# frontend
cd frontend && npm install && npm run dev
```

Or just `./start.sh` to bring up API + UI together.

## Making changes

1. Add or update tests — engine rule changes **must** come with table-driven tests in
   `tests/`. Interaction edge cases (precedence, hysteresis, caps) especially.
2. Keep the code↔config split intact (see [ARCHITECTURE.md §4a](ARCHITECTURE.md)): no
   personal or strategy-specific values hardcoded; everything user-facing is DB config.
3. Run `pytest` — keep it green.
4. For a new market-data provider, implement the `MarketDataProvider` protocol in
   `src/tradr/market/adapter.py` and register it in `get_provider`.

## Submitting a pull request

1. Fork the repo and create a branch off `main` (e.g. `fix/trim-velocity-edge-case`).
2. Make your change with tests, per "Making changes" above.
3. Run the full check locally before opening the PR:
   ```bash
   pytest && ruff check src tests
   ```
4. Open the PR against `main` and fill in the template — it checklists the things
   reviewers will look for (tests, purity of the engine, no personal data, docs).
5. CI (GitHub Actions) runs the same checks automatically; a green run is required
   before merge.
6. Keep PRs focused — one logical change per PR is much faster to review than a bundle
   of unrelated fixes.

## Reporting issues

Include steps to reproduce and, for engine logic, a minimal snapshot (positions +
params + market data) that shows the wrong output. Never paste real account details.
See the bug report template for the exact shape, and [SECURITY.md](SECURITY.md) if
the issue is a vulnerability rather than a bug.

## Code of Conduct

This project follows the [Contributor Covenant](CODE_OF_CONDUCT.md). By participating,
you're expected to uphold it.
