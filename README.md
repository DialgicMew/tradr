# tradr

**A self-hosted, rules-based investing assistant for US equities.** You tell it what
you own; it watches the market against *your* rules and, a few times a day, tells you
exactly what to buy or sell — and why — in plain English. **It never trades for you.
You approve every action.**

> ⚠️ **Not financial advice.** tradr runs arithmetic on rules *you* configure. Every
> suggested trade is mechanical, not a recommendation. Market data can be delayed or
> wrong. Treat every alert as a prompt to *check*, not to act blindly. No warranty
> (Apache-2.0 §7). You are responsible for your own trades, taxes, and outcomes.

---

## What is this, in plain English?

Say you have a few personal investing rules like:

- *"If a stock I like drops 20% from its high, buy a bit more."*
- *"If a stock doubles, sell a slice and lock in some profit."*
- *"Keep about 40–50% of my money in an index fund."*
- *"Put a fixed amount in every month."*

Following rules like these by hand is tedious and error-prone — the numbers move every
day, price alerts go stale, and it's easy to forget or act emotionally.

**tradr does the bookkeeping and the watching for you.** You log what you buy and sell
(via a web page or a Telegram message). It tracks your holdings, your average cost, and
a cash "reserve." A few times a day it checks live prices against your rules and, when
one triggers, sends you a card like:

> **Buy $87 of ORCL at ~$116.97** — *ORCL has dropped 66% below its 52-week high. That's
> past your "buy the dip" level, so your rule adds to the position while it's cheaper.*
> **[Approve] [Decline]**

You place the trade yourself with your broker, then tap **Approve** and enter the fill —
tradr updates your books. That's the whole loop. It's a disciplined **rules-checker and
reminder**, not a robot trader and not an advisor.

**What it deliberately does NOT do:** place orders, touch your money, connect to your
brokerage, or tell you *which* stocks are good. Those are your decisions.

---

## Features

- **Position tracking** — holdings, average cost, and a notional dry-powder "reserve",
  all derived from an append-only log of your own confirmed buys/sells. No brokerage link.
- **A deterministic rules engine** ([RULESET.md](RULESET.md)) — dip-buying on drawdowns,
  profit-trimming on fast gains, monthly dollar-cost-averaging, an index-core builder, and
  a cash-floor nudge — with hysteresis and latching so you don't get spammed.
- **Plain-English recommendations** — every alert states *what to do*, *the exact dollar
  amount and price*, and *why*, ranked so index/core actions come first.
- **Confirm-each workflow** — nothing is ever assumed done; you Approve (record the fill)
  or Decline (dismiss) each one.
- **Web UI** (localhost) — dashboard with holdings, allocation chart, and performance;
  trade logging (by shares *or* by dollar amount, fractional supported); and a full config
  editor with plain-language help on every setting.
- **Telegram bot** (optional) — log trades with `/buy`, `/sell`, `/reserve` and act on
  alerts from your phone.
- **100% self-hosted & configurable** — every personal value lives in your own database,
  entered through a first-run setup wizard. Nothing personal is baked into the code.

---

## Quick start

**Prerequisites:** Python **3.11+**, Node **18+** (for the web UI). That's it — no database
server needed to try it (it uses SQLite by default).

```bash
git clone <your-fork-url> tradr && cd tradr
./start.sh
```

`start.sh` does everything on first run: creates a `.env`, sets up the Python virtualenv
and installs backend deps, runs `npm install` for the frontend, and launches both:

- **Web UI** → http://127.0.0.1:5173
- **API** → http://127.0.0.1:8000 (interactive docs at `/docs`)

Open the web UI and the **setup wizard** walks you through the only values you must
provide (your monthly budget, your tier weights, whether your emergency fund is funded).
Then add your holdings on the **Trades** page and your watchlist/tiers on **Config**.

Press **Ctrl-C** to stop both servers. On later runs it skips the install steps, so it
starts in seconds.

> If `python3.11` isn't on your PATH under that exact name: `PYTHON=python3 ./start.sh`.

### Turn on live prices

Out of the box the market-data provider is `none` (so you can explore offline). To get
real prices and live signals, set in `.env`:

```
MARKET_DATA_PROVIDER=yahoo
```

This uses a free, keyless data source. It's unofficial and can rate-limit — for anything
you rely on long-term, swap in a paid provider behind the same adapter
(`src/tradr/market/adapter.py`). Forward-P/E figures aren't available on the free tier;
enter the two S&P macro numbers monthly on the Config page (they change slowly).

### Turn on the Telegram bot (optional)

1. Create a bot with [@BotFather](https://t.me/botfather) and copy the token.
2. Get your numeric chat ID (message [@userinfobot](https://t.me/userinfobot)).
3. Put both in `.env` as `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
4. Run: `PYTHONPATH=src .venv/bin/python -m tradr.bot`

### Run it on a schedule (optional)

The engine can evaluate a few times a day during US market hours:

```bash
PYTHONPATH=src .venv/bin/python -m tradr.scheduler
```

For a persistent deployment, `docker-compose.yml` runs Postgres + API + scheduler + bot,
all bound to localhost. See [ARCHITECTURE.md §4](ARCHITECTURE.md).

---

## Configuration is 100% yours

**No personal values ship in this repo.** On first run the setup wizard collects the
values only you can decide — your monthly `BASE_UNIT`, tier weights, and emergency-fund
flag. Everything else uses documented neutral defaults you can tune on the Config page
(each setting has an ⓘ tooltip explaining it with an example). The engine **refuses to
run until required config is present** — it never sizes a trade off a guessed budget.

Key concepts you configure:
- **Tiers** — conviction levels (`T1`/`T2` for stocks, `ETF`/`ETF2` for index/satellite
  ETFs) that set how much each name gets.
- **Universe** — the tickers the engine may act on, plus an avoid-list.
- **Parameters** — the drawdown/trim bands, hysteresis, reserve target, etc. Some are
  locked (safety rails from the ruleset); most are tunable.

Full rationale for every number is in [RULESET.md](RULESET.md) (Appendix A) and validated
against a neutral SPY backtest (Appendix B).

---

## Documentation

- **[RULESET.md](RULESET.md)** — the complete rules specification (v3): system boundary,
  every rule, evaluation order, precedence, parameters, and backtest.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — system design, data model, deployment, and the
  strict code↔config split that keeps it open-source-safe.
- **[CONTRIBUTING.md](CONTRIBUTING.md)** — how to run tests and contribute.

The heart of the system is the **Engine Core** (`src/tradr/engine/`): a *pure*,
I/O-free function that takes a snapshot of your state + market data and returns
`(recommendations, state changes)`. That purity is why it's fully unit-tested and
backtestable.

---

## Project layout

```
src/tradr/
  engine/        pure rules engine (no I/O) — the crown jewel + its tests
  services/      ledger, config, signal lifecycle (DB-backed)
  market/        market-data adapter (Yahoo / pluggable)
  api/           FastAPI backend
  bot.py         Telegram bot (long-polling)
  runner.py      one evaluation pass
  scheduler.py   market-calendar-aware scheduler
frontend/        React (Vite) web UI
tests/           engine + service test suite
RULESET.md ARCHITECTURE.md CONTRIBUTING.md start.sh docker-compose.yml
```

## Testing

```bash
pip install -e ".[dev]" && pytest        # ~40 tests, engine + services, no DB needed
```

## Security & privacy

- **Single-user, self-hosted.** Each person runs their own instance with their own DB —
  nobody's holdings ever touch a shared server.
- All services bind `127.0.0.1`. Reach the UI via Tailscale or an SSH tunnel — **never
  expose it publicly** (it holds your full portfolio).
- Secrets live in `.env` (git-ignored). The Telegram bot uses long-polling (outbound
  only), so no inbound port.
- **Never commit personal data.** Your database (`tradr.db*`), any broker records
  (`orders/`), and `.env` are git-ignored by default — keep them that way.

## Roadmap

- Brokerage/FX cost-awareness (suppress or batch trades whose fees exceed a threshold).
- Broaden market-data providers behind the adapter.
- Reconciliation prompts against a brokerage statement.

## License

[Apache-2.0](LICENSE). Provided as-is, without warranty.
