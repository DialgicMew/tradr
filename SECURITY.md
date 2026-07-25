# Security Policy

tradr is **self-hosted and single-user per instance** — there is no shared server, and
no central place where multiple users' data lives. Each instance holds one person's
portfolio, in their own database, on their own machine.

## Reporting a vulnerability

If you find a security issue — e.g. a way to leak another local user's data, an
injection vulnerability, unsafe secret handling, or an auth bypass in a future
multi-user feature — please report it privately rather than opening a public issue:

- Preferred: open a [GitHub Security Advisory](../../security/advisories/new) on this
  repo (private to maintainers until resolved).
- Otherwise: open a regular issue asking a maintainer to contact you privately, without
  posting exploit details publicly.

Please include steps to reproduce and the potential impact. We'll acknowledge reports
as promptly as we can — this is a community project without a dedicated security team,
so response times may vary.

## Scope

In scope: the application code in this repository (engine, API, bot, frontend).

Out of scope / your responsibility as a self-hoster:
- Securing the machine/VM you run tradr on, and not exposing it publicly (see
  [README](README.md#security--privacy) — bind to `127.0.0.1`, use Tailscale/SSH tunnel).
- Your own `.env` secrets, Telegram bot token, and market-data API keys.
- The accuracy or availability of third-party market-data providers.

## Not a source of financial security either

To be clear on scope: this policy covers *software* security (data leakage, injection,
etc.), not investment risk. tradr is advisory-only and not a licensed financial service;
see the disclaimers in [README.md](README.md) and [RULESET.md](RULESET.md).
