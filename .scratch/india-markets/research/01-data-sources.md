# 01 - India market-data sources

Ticket: `.scratch/india-markets/issues/01-data-sources.md`. Probed live with curl on 2026-10-12/13 (a Monday evening IST, market closed). All endpoints keyless.

## Coverage

| Need | Source | Works? | Fields | Verdict |
|---|---|---|---|---|
| Stock price / 52wk high / closes | Yahoo `v8/finance/chart/RELIANCE.NS?range=1y&interval=1d` (same endpoint the adapter uses) | Yes, 200, ~247 daily bars | `meta.regularMarketPrice`, `meta.fiftyTwoWeekHigh/Low`, `meta.chartPreviousClose`, `indicators.quote[0].close`, `indicators.adjclose` | Good |
| NSE ETF (NIFTYBEES.NS) | same | Yes, identical shape, 247 bars | same | Good |
| USDINR | Yahoo `USDINR=X` | Yes, 261 bars (weekday FX, last bar is the live quote) | same meta incl. 52wk hi/lo | Good |
| Nifty index level | Yahoo `^NSEI` | Yes | same | Good (no P/E) |
| BSE (`.BO`) | Yahoo `RELIANCE.BO` | **Broken for history**: `range=1y` returned 1 bar, `firstTradeDate` = 2026-07-17. `TCS.BO` returned normal meta (not checked for bar count) | meta ok | Prefer `.NS`; adapter returns None for 1-bar series (fail-safe, fine) |
| Previous close | Yahoo meta | **No usable field**: `previousClose` is absent/null; `chartPreviousClose` is the close *before the range start* (1381.7 for RELIANCE with range=1y), not yesterday | - | Derive from the series (see caveats) |
| Fwd P/E | Yahoo `v10/quoteSummary`, `v7/quote` | **401** ("Invalid Crumb" / "User is unable to access this feature") without cookie+crumb session | - | Not available keyless; manual entry |
| MF NAV latest + history | mfapi.in `GET /mf/{code}` | Yes, 200, 133 KB, full history since scheme start (axis ELSS 120503: 3398 rows from 02-01-2013) | `meta{fund_house, scheme_type, scheme_category, scheme_code, scheme_name, isin_growth, isin_div_reinvestment}`, `data[{date "DD-MM-YYYY", nav "string"}]` newest first | Good |
| MF latest only | mfapi.in `/mf/{code}/latest` | Yes, 1 row | same | Good |
| MF search | mfapi.in `/mf/search?q=` | Yes, JSON `[{schemeCode, schemeName}]`; no match returns `[]` | - | Good |
| MF full list | mfapi.in `/mf` | Yes, 5.7 MB | - | Use sparingly |
| MF NAV, official | AMFI `https://portal.amfiindia.com/spages/NAVAll.txt` (`www.amfiindia.com/spages/NAVAll.txt` 302s there) | Yes, 1.5 MB, ~14k rows, `;`-delimited | `Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;Scheme Name;Plan;Option;Net Asset Value;Date` (date `12-Oct-2026`); section header lines (category, fund house) interleaved with blank lines | Good (latest day only, no history) |
| Nifty P/E | NSE `https://nsearchives.nseindia.com/content/indices/ind_close_all_DDMMYYYY.csv` | Yes, 200, 17 KB CSV | `Index Name,Index Date,...,Closing Index Value,...,P/E,P/B,Div Yield` per index (Nifty 50 = 19.27 on 09-10-2026) | Best available; trailing P/E, not forward |
| Nifty P/E (live) | NSE `www.nseindia.com/api/allIndices` | 200 with a `Mozilla/5.0` UA, connection dropped with no/other UA; homepage and `/api/quote-equity` returned 403 | `pe`, `pb`, `dy`, `yearHigh`, `perChange30d`... | Works today, fragile; don't depend on it |
| USDINR, official-ish | Frankfurter (`api.frankfurter.dev/v1/latest?base=USD&symbols=INR`, `/v1/2026-10-01..2026-10-09?...`) | Yes, ECB reference rates, daily, history by date range | `{date, rates:{INR}}` | Good fallback / cross-check |
| USDINR alt | `open.er-api.com/v6/latest/USD` | Yes, updates once a day | rates map; response links ToS (attribution required on free tier) | Fallback only |

## What the 1y Yahoo close series looks like

- Stocks/ETFs: 247 timestamps at 03:45 UTC (09:15 IST bar open), one per NSE trading day, no gaps >5 days, no duplicate dates.
- **Null closes**: 1-2 per series. (a) The *last* bar (today) has `close: null` even after the close, because the series' last bar is the in-progress/unsettled day while `meta.regularMarketPrice` carries the live/last price. (b) Occasional interior nulls (NIFTYBEES.NS 2025-10-24; USDINR=X 2025-12-25 and 2026-01-01). The adapter's `if c is not None` filter handles both.
- Consequence for the adapter: after filtering, `closes[-1]` is the *previous trading day's* close when today's bar is null, so `closes[-1]` is the previous close and `price / closes[-31]` is "price vs 31 trading days before that". During market hours or once the bar settles, `closes[-1]` may instead equal today's close. Off-by-one in the 30d/60d windows is small but real. Rows are trading days, not calendar days (30 "days" is about 6 weeks).
- Regular price vs last close: RELIANCE.NS `regularMarketPrice` 1158.9 vs last non-null close 1170.3 (a day apart), consistent with the above.
- `fiftyTwoWeekHigh` is intraday (RELIANCE 1611.8 vs max close 1592.3), so it is a bit higher than the max close. The adapter's fallback `max(closes)` is close-based; they differ slightly.
- FX (`USDINR=X`): last bar is stamped with the live quote time, so there is no separate "today's close"; weekend bars absent.
- Currency is INR in meta for `.NS`/`.BO`/`USDINR=X`; no adapter change needed for price units.

## Reliability, limits, ToS

- **Yahoo chart endpoint**: unofficial, undocumented, no SLA, no published rate limit; throttles (429) by IP under bursts. Needs a browser-like `User-Agent` (adapter already sets one). Endpoints needing crumb (`quoteSummary`, `v7/quote`) are blocked. Yahoo's terms restrict data to personal/non-commercial use and forbid redistribution; the API is not offered publicly. I could not retrieve the terms page in this session (non-HTML response), so treat that as from general knowledge, not verified.
- **mfapi.in**: community project, "no authentication, no API keys, no rate limiting" per its homepage, ~99.9% uptime claimed, data refreshed 6x/day (10:05, 14:05, 18:05, 21:05, 03:09, 05:05 IST). Observed lag: at 20:45 UTC (02:15 IST) the latest NAV for 120503 was 09-10-2026 while AMFI already had 12-Oct (105.0868 vs 105.1415 for 09-10). Source data is AMFI's, so mfapi adds delay but no new coverage. Has a ToS page linked from the homepage (not read). Single volunteer-run host: back it with AMFI.
- **AMFI NAVAll.txt**: official publisher of record, free, no key, no stated rate limit. NAVs for the day are published in the evening IST (usually by ~9-11 PM); some funds (debt, FoFs) post late or the next morning, so a missing/old date per scheme is normal. Redirects www to portal host (follow redirects). `robots.txt` disallows `/search/`, `/login/`, `/admin/`, not `/spages/`. No history; for history use mfapi.in or the (form-driven) AMFI NAV history page.
- **NSE**: `www.nseindia.com` is bot-hostile (403 on homepage and quote API, connection drops for non-browser UAs, cookie handshakes typical). `nsearchives.nseindia.com` static CSVs returned 200 for no-UA and `Mozilla/5.0` but dropped `python-httpx/...`; weekend/holiday dates are 404. NSE content is subject to NSE's terms (redistribution/commercial use restricted); personal polling of a daily CSV is low-risk but not licensed for redistribution. Terms page not retrieved (404 on guessed URL).
- **Frankfurter**: open source ECB reference rates, no key, no stated limit; ECB rate is published ~16:00 CET, one rate per business day.

## Recommended source per need

| Need | Use | Fallback |
|---|---|---|
| Stock/ETF price, 52wk high, 30d/60d returns | Yahoo chart `.NS` (existing `YahooProvider`, no change needed to hit NSE tickers) | Manual |
| Previous close | Derive from the filtered series (`closes[-1]` while today's bar is null; otherwise `closes[-2]`); do not use `meta.chartPreviousClose` | - |
| NSE-listed ETF | Yahoo `.NS` as above (NIFTYBEES.NS works) | AMFI NAV (NAVAll.txt lists ETFs, e.g. 141545 Axis Nifty 50 ETF) for NAV rather than market price |
| Mutual fund daily NAV | AMFI NAVAll.txt (one fetch, parse by scheme code) | mfapi.in `/mf/{code}/latest` |
| Mutual fund history (30d/60d returns) | mfapi.in `/mf/{code}` (dates are `DD-MM-YYYY`, NAV is a string, newest first) | Self-accumulate daily AMFI snapshots |
| USDINR daily | Yahoo `USDINR=X` (same adapter path) | Frankfurter `/v1/latest?base=USD&symbols=INR` |
| Nifty P/E | NSE `ind_close_all_DDMMYYYY.csv` (trailing P/E, row `Nifty 50`; walk back a day on 404) | Manual entry |

## Gaps that need manual entry

1. **Forward P/E per stock**: no keyless source. Yahoo's quoteSummary/quote endpoints require a cookie+crumb session and returned 401 here; keep `fwd_pe=None` in the adapter and keep manual entry.
2. **Nifty forward P/E (the S&P froth-input equivalent)**: no free machine-readable source. NSE publishes only *trailing* P/E (plus P/B, dividend yield) in the daily index CSV and `allIndices`. If the rule's threshold is calibrated to forward P/E, the trailing figure is not a drop-in (trailing is usually higher in falling-earnings years). Either re-calibrate thresholds against trailing Nifty P/E or keep manual entry from a broker/research note.
3. **Anything via `www.nseindia.com`**: bot-blocked; do not build on it.
4. **BSE-only listings**: Yahoo `.BO` history looked broken for RELIANCE.BO; use `.NS` where dual-listed, manual otherwise.
5. **Previous close as a labelled field**: not provided; derived, not fetched.
