# Rules-Based Advisory Engine — Consolidated Ruleset (v3)

> **Purpose of this file.** This is a complete, self-contained specification of a
> rules-based investing *advisory* engine. It distills a discretionary
> growth-investing philosophy into unambiguous, logically-proofed rules. Every design
> decision below was deliberated and locked — **do not silently re-open or
> re-interpret them.** If something seems suboptimal, flag it and ask; don't
> quietly change it. Narrative/opinion content (theses, price targets, macro calls,
> psychology) was intentionally discarded as non-mechanizable — the engine keeps only
> what can be computed from your holdings and market data.
>
> **How to use this file in another chat:** paste it whole. It contains the system
> boundary, all rules, the daily evaluation order, precedence for conflicts, the
> parameter list, and the open items. The consuming chat should implement against
> this spec and ask before deviating.

---

## 0. System boundary (non-negotiable)

- **Advisory only.** The engine emits notifications shaped as
  `{ cause, action, size_hint, asset, band }`. **The human takes every action.**
  The engine never places orders, never touches cash, never executes.
- **No market-data computation.** Current price, 52-week high, trailing 30-day
  return, per-stock forward P/E, S&P 500 forward P/E, and the S&P's 10-year
  average forward P/E are all **fetched from a market-data API**. The engine must
  **never** derive these from raw price series itself.
- **Engine owns rule state only** — fired-band flags, crash flags, reset points,
  the notional reserve ledger, and positions/shares/avg-basis/weight. Because
  there is **no brokerage sync**, all of this state is built up from the user's
  **confirmations** (see §2). When the user confirms a buy/sell as DONE, they
  report the fill (shares + price) so the engine can update basis and weights.
- **Hard constraints that override every rule below:**
  - Never suggest more than **3× base** into a single name (even in a deep crash).
  - **Zero leverage / margin, ever.**

---

## 1. Parameters (symbolic — fill in real values before running)

| Param | Meaning | Locked default (if any) |
|---|---|---|
| `BASE_UNIT` | base unit of a scheduled buy | — |
| `tier_weight{T1, T2, ETF}` | per-name multiplier by conviction tier; `BASE_i = BASE_UNIT × tier_weight` | T1 > T2 > ETF |
| `SNOOZE_DAYS` | quiet period after a signal is marked SKIPPED | — |
| `BANDS_stock` | double-down drop bands for individual growth stocks | –20 / –30 / –40 / –50 % |
| `BANDS_etf` | double-down drop bands for broad ETFs | –10 / –20 / –30 / –40 % |
| `ARM_stock` | re-arm (hysteresis) line for stocks | recover above –10 % of high |
| `ARM_etf` | re-arm line for ETFs | recover above –5 % of high |
| `DD_CAP` | max cumulative double-down per name per crash cycle | 3 × `BASE_i` |
| `TRIM_BANDS` | trim gain bands (vs avg cost basis) | +120 / +170 / +220 % |
| `TRIM_PCT` | % of current shares sold per trim band fire | 10–30 % |
| `TRIM_ARM_BUFFER` | hysteresis for re-arming a trim band | e.g. band re-arms when gain < band − 20pp |
| `TRIM_VELOCITY_PCT` | min trailing gain over `TRIM_VELOCITY_WINDOW` to allow trim | +30 % |
| `TRIM_VELOCITY_WINDOW` | lookback window for velocity check | 60 trading days |
| `MIN_WEIGHT` | floor: never trim a name below this portfolio weight | 3 % |
| `RESERVE_TARGET` | earmarked dry-powder cap (exempt from cash floor) | 20 % of portfolio |
| `BRAKE_RELEASE_PCT` | pullback off the momentum-spike peak that releases the brake | — |
| `FROTH_THRESHOLD` | how far S&P fwd P/E must exceed its 10-yr avg to count as "frothy" | — |
| `CASH_CEILING` | idle-capital ceiling that triggers a deploy nudge | 5 % of portfolio |
| `MAX_HOLDINGS` | cap on number of individual stocks (ETF core excluded) | 20 |

---

## 2. Notification lifecycle (confirm-each model)

Every trigger produces a **signal** with an explicit state machine:

```
PENDING ──(user acks DONE)────▶ DONE     : latch band; update ledger + positions/basis
   │
   ├──────(user acks SKIPPED)─▶ SNOOZED  : quiet for SNOOZE_DAYS, then re-arm if still valid
   │
   └──(condition clears first)▶ EXPIRED  : emit "withdrawn — price recovered" note;
                                           band returns to ARMED (NOT burned)
```

- State advances **only** on user confirmation. **Nothing is ever assumed
  executed.**
- On DONE, the user reports the fill so positions/avg-basis/weight update.

---

## 3. Reserve ledger (notional dry powder)

The reserve is a **number the engine tracks**, backed by the user's real cash. The
engine never holds money.

**Funded by:**
- **50/50 auto-bank** (toggleable): each normal-market base buy deploys half of
  `BASE_i` and banks the other half into the reserve.
- **Trim proceeds** recycle in (see Rule 2).
- **Manual external top-ups.**

**Drawn by:**
- **Per-stock double-down deployment** — the reserve unlocks for a name only when
  *that name* has an active double-down band (per-stock trigger, not market-level).

**Earmarking:** the reserve may build up to `RESERVE_TARGET` (20%) and that portion
is **committed dry powder, exempt from the cash floor**. Only reserve **above** the
target counts as idle cash.

If a double-down suggestion exceeds available reserve, the notification **flags the
shortfall** ("reserve short by $X — top up or fund from cash") rather than shrinking
the suggestion. The human decides.

---

## 4. Daily evaluation pipeline (ORDER IS SIGNIFICANT)

1. **Ingest** API data (price, 52wk high, 30-day return, per-stock fwd P/E, S&P fwd
   P/E + 10-yr avg). Update ledger/positions from any confirmations since last run.
2. **Expire** stale PENDING signals whose condition has cleared → send withdrawal
   note; band returns to armed.
3. Per holding compute: `dd = price / 52wk_high − 1`; `gain = price / avg_basis − 1`;
   30-day momentum from API; 60-day trailing gain from API (for trim velocity gate).
4. **Reset / re-arm checks:** DD bands re-arm when `dd ≥ ARM line`; trim bands re-arm
   when `gain < band − TRIM_ARM_BUFFER`; momentum brake release; snooze expiry.
5. **TRIM (Rule 2)** — for each name, check if any trim bands crossed AND the
   velocity gate passes (60-day trailing gain ≥ +30%); if velocity fails, the
   band stays armed but does not fire today. Fire eligible bands. **If a name
   trims today, suppress its double-down for today** (its DD bands are left
   untouched, not burned). Proceeds recycle into the reserve on DONE.
6. **DOUBLE-DOWN (Rule 1)** — for names not trimming: collect crossed bands (gap-
   through fires all crossed at once), resolve **shared-reserve contention
   deepest-`dd`-first** (ties: Tier 1 before Tier 2, then alphabetical), size first
   band ≈ 2× topping toward `DD_CAP` = 3× (deeper bands add until the cap; further
   bands suggest 0), fund from reserve, flag any shortfall.
7. **BASE DCA (Rule 7)** — *monthly scheduled date only*: compute `BASE_i` per name;
   apply **momentum brake** (×0.5 if 30-day return > +20%) and **froth overlay**
   (×0.5 if S&P fwd P/E exceeds 10-yr avg by `FROTH_THRESHOLD`) — these **do NOT
   compound** (deploy multiplier floors at 0.5); apply 50/50 split; **Rule 3 steers
   contributions into the ETF** if core < 40%; annotate the PE zone (advisory only).
8. **Cash floor (Rule 6)** — if idle capital (uncommitted cash + reserve *above*
   `RESERVE_TARGET`) > `CASH_CEILING` → suggest deploying the excess into the **ETF
   core**.
9. **Emergency-fund gate** — if the user's emergency-fund flag is unset, attach a
   "confirm 6–12 months funded first" **warning** to every buy signal (warn-only,
   does not block).
10. **Diversification (Rule 5)** — if a **new** name's buy fires while at
    `MAX_HOLDINGS` → emit a **"replace something?"** notification (requires dropping
    a name first). Existing holdings signal normally.
11. **Emit** all resulting signals.

> Note: on non-scheduled days, step 7 is skipped; trim / double-down / cash-floor /
> expiry checks still run daily.

---

## 5. The rules (detail + rationale)

### Rule 1 — Per-Stock Double-Down
- **Trigger:** per-stock drawdown from the API's 52-week high crosses a band.
  Asset-class-dependent: **ETF ladder –10/–20/–30/–40**, **stock ladder
  –20/–30/–40/–50**.
- **Reference:** rolling 52wk high (from API), with **latched** fired flags so
  window drift can't un-fire a band.
- **Re-trigger control (hysteresis):** each band fires once; the whole ladder
  re-arms only after price recovers above the ARM line (stock –10%, ETF –5%). This
  prevents whipsaw re-firing around a band in a choppy bottom.
  - *Known, intended limitation:* a stock that crashes and settles well below its
    high (never recovering past the ARM line) will not re-arm — it won't be
    averaged into indefinitely. This is deliberate (no averaging into a broken
    stock).
- **Gap-through:** if one close skips multiple bands, **all** crossed bands fire at
  once — but total is still clamped by `DD_CAP`.
- **Sizing:** first band ≈ 2× base; deeper bands top up toward the **3× hard cap**;
  cumulative per name per crash **never exceeds 3× base**.
- **Funding:** from the shared reserve (only when that name has an active band).
  Multi-name same-day contention → deepest-`dd`-first.
- **Edge cases:** trading halt / missing close → skip the day, state carries.
  Recent IPO with < 1yr history → use best available high, treat as low-confidence.
  Avoid-list names → no buys ever (universe filter overrides).

### Rule 2 — Trim (sell strength)
- **Trigger:** gain vs **average cost basis** crosses a band: **+120 / +170 / +220%**.
  (Raised from +100/+150/+200 after backtest showed premature trimming of
  compounders; see Appendix B.)
- **Velocity gate (mandatory):** a trim band fires **only if** the stock's trailing
  gain over `TRIM_VELOCITY_WINDOW` (60 trading days) exceeds `TRIM_VELOCITY_PCT`
  (+30%). If the gain was slow (gradual compounding over years), the trim is
  **suppressed** — the band stays armed but doesn't fire. This separates
  momentum spikes (trim-worthy) from steady compounders (let them ride). The
  velocity check is re-evaluated daily; a suppressed band will fire if velocity
  later spikes (e.g. an earnings pop accelerates a slow mover past the threshold).
- **Style:** laddered, each band fires once (if velocity gate passes), selling
  `TRIM_PCT` (10–30%) of current shares. Gap-up fires all crossed bands.
- **Re-arm (hysteresis):** a fired band re-arms only when gain falls back below
  `band − TRIM_ARM_BUFFER` (mirror of Rule 1; stops whipsaw selling).
- **Floor:** never trim a name below `MIN_WEIGHT` (3%) of the portfolio (protects
  compounders). If already at/below floor, sell 0 but still mark the band fired.
- **Proceeds:** recycle into the reserve (this is what links trim and double-down
  into one loop — sell strength to reload the powder that buys weakness).
- **Precedence:** **Trim beats double-down.** If a name is both trim-eligible and
  double-down-eligible on the same close, execute the trim and skip the double-down
  that day (DD bands untouched). *Deliberately kept even during a drawdown* — the
  user chose profit-taking over the "never sell in panic" maxim here.
  - *Known bounded side effect:* can produce sell-one-day / buy-next-day on the same
    name. Both sides latch, so it can't loop forever. Add an N-day lockout later
    only if it proves annoying.
- **Reference note:** gain is measured off **average cost basis**, which double-downs
  *lower* — so accumulating pulls a name toward trim triggers. This coupling is live
  and intended.
- **Tax:** trims realize taxable gains. Out of scope for the engine; the human
  handles tax. (The recycling design reframes trims as "reload ammunition," not
  "take profit to spend.")

### Rule 3 — Core S&P Allocation (hold 40–50%)
- **No-trade band** on **invested equity only** (excludes reserve/cash).
- **Correction is contributions-only, never by selling.** If ETF < 40% → steer
  upcoming DCA into the ETF until back to **40%** (nearest edge). If ETF > 50% →
  stop adding to the ETF and let other names catch up (passive; never sold).
- **Precedence:** when the core is underweight, this steering **overrides Rule 7's
  tier-weighted split** for the ETF portion.

### Rule 4 — Valuation
- **Per-stock PE gate: advisory only.** Base-buy notifications annotate the fwd-P/E
  zone (`<17 strong / 20–24 fair / >24 risky`). It **never** changes size or blocks
  — this resolves the source's own contradiction ("ignore PE for great businesses").
- **Scope:** **double-downs are exempt** (dip-buying ignores valuation entirely).
- **Market overlay:** when S&P forward P/E exceeds its 10-year average by
  `FROTH_THRESHOLD` → **half-speed deploy** on all base buys until it normalizes.
  (Deliberately pinned to a number so no gut-feel judgment creeps in.)
- **Interaction:** froth half-speed and momentum-brake half-speed **do not
  compound** — deploy multiplier floors at 0.5.

### Rule 5 — Diversification Cap
- Max **`MAX_HOLDINGS` = 20 individual stocks**; the **ETF core does not count**.
- At the cap, a **new** name's buy fires as a **"replace something?"** notification
  (must drop a name first). Existing holdings signal normally.

### Rule 6 — Cash Floor (never sit in excess cash)
- Practical form of "never go to 100% cash": if **idle capital** (uncommitted cash +
  reserve **above** `RESERVE_TARGET`) exceeds `CASH_CEILING` (5%), the engine
  **suggests deploying the excess into the ETF core**.
- **Reserve up to its target is exempt** (committed dry powder). This reconciles the
  cash floor with the reserve strategy — without the earmark, the floor would
  continuously sweep the dry powder away and defeat the double-down engine.

### Rule 7 — DCA Base Schedule
- **`BASE_i = BASE_UNIT × tier_weight`** (Tier 1 > Tier 2 > ETF). Total monthly
  outflow scales with holdings.
- **Cadence:** monthly on a fixed date; if the market is closed → **next trading
  day**.
- **50/50 split:** normal market → deploy `BASE_i/2`, bank `BASE_i/2` into reserve.
  Bad market for a name (its double-down band is active) → deploy `BASE_i/2` **plus
  reserve draw**, per Rule 1's ladder and 3× cap.
- **Momentum brake:** if a name's trailing 30-day return > +20% → **halve its
  deploy** (bank portion unchanged). Track the spike peak; **release when price
  pulls back `BRAKE_RELEASE_PCT` off that peak.** (Brake and double-down conditions
  are mutually exclusive, so no clash.)

### Preconditions & hard constraints
- **Emergency fund:** user-attested flag (6–12 months of expenses). If unset →
  **warn-only** on buy signals (does not block). Sell/trim signals unaffected.
- **Leverage:** the engine never emits a leveraged/margin suggestion.
- **3× base cap:** absolute ceiling on any per-name double-down accumulation.

---

## 6. Precedence & conflict-resolution summary

1. **Hard constraints** (3× cap, zero leverage) override everything.
2. **Trim > double-down** (per name, per day).
3. **Rule 3 ETF steering > Rule 7 tier weight** when the core is underweight.
4. **Core allocation** is corrected by contributions only, **never by selling**.
5. **PE gate** is advisory annotation only; **double-downs are PE-exempt**.
6. **Froth and momentum half-speeds do not stack** (multiplier floors at 0.5).
7. **Emergency-fund** gate is warn-only.
8. **Reserve earmark** (up to `RESERVE_TARGET`) is exempt from the cash floor.

---

## 7. Deliberately excluded (not mechanizable — do not add as rules)

Picks-&-shovels thesis, AI value-chain narrative, TPU-vs-GPU, war/geopolitics
"buy the dip," stagflation odds, market-cycle psychology, and all price targets.
These informed the *why* but are not computable triggers and were dropped by design.

The curated stock **universe** (Tier 1 / Tier 2 lists) and the **avoid-list**
(tickers you choose to exclude) are **static config data**, not rules — the engine reads
them; it does not decide them.

---

## 8. Open items before build

1. **Fill in the parameter table** (§1) with real values.
2. **Positions/basis integrity:** all of `gain`, `MIN_WEIGHT`, and allocation math
   depend on the user accurately reporting fills at confirmation time. Add a periodic
   "reconcile against brokerage statement" prompt to catch drift.
3. Decide whether the **50/50 auto-bank** is on by default or a toggle per the user's
   cash-flow comfort.
4. Confirm the **froth threshold** and **brake release** numbers with a quick
   historical sanity check once data is wired up.

---

## Appendix A — Suggested Default Values

> **READ THIS FIRST.** Everything below is a **DEFAULT / starting point**, not a
> recommendation and not investment advice. I am not an authoritative financial
> source. These numbers are reasoned engineering defaults meant to make the system
> *runnable and internally coherent* on day one — they are chosen for sensible
> behavior, not for return optimization, and they have **not** been backtested here.
> Before risking real money: backtest each value, and calibrate to your own
> risk tolerance, tax situation, and cash flow. Consult a licensed advisor for
> anything you'd treat as advice. Every value is labeled **[DEFAULT]**; the ones
> already fixed during the design session are labeled **[LOCKED]**.

### A.1 Quick-reference table

| Param | Suggested value | Status | One-line rationale |
|---|---|---|---|
| `BASE_UNIT` | **user-specific — no default** | — | Must come from your budget; method below. |
| `tier_weight` T1 / T2 / ETF | **2.0 / 1.0 / derived** | [DEFAULT] | Conviction ordering; ETF weight solved to hit the 40–50% core. |
| `SNOOZE_DAYS` | **5 trading days** | [DEFAULT] | ~1 week: long enough for a dip to evolve, short enough not to miss it. |
| `BANDS_stock` | –20 / –30 / –40 / –50 % | [LOCKED] | Source threshold for growth stocks; even 10pp steps. |
| `BANDS_etf` | –10 / –20 / –30 / –40 % | [LOCKED] | ETFs are less volatile → shallower entry per source. |
| `ARM_stock` / `ARM_etf` | –10 % / –5 % | [LOCKED] | Hysteresis = half the first band width (see A.3). |
| `DD_CAP` | 3× base | [LOCKED] | Hard cap straight from the source. |
| `TRIM_BANDS` | +120 / +170 / +220 % | [DEFAULT] | Raised from +100/+150/+200 after backtest; lets compounders run longer. |
| `TRIM_PCT` | **20 % of current shares/fire** | [DEFAULT] | Halves the position over a full 3-rung run; lets half ride. |
| `TRIM_ARM_BUFFER` | **20 pp** | [DEFAULT] | Gain must retrace 20pp before a rung re-arms; anti-whipsaw. |
| `TRIM_VELOCITY_PCT` | **+30 %** | [DEFAULT] | Minimum 60-day trailing gain to allow a trim; see A.9. |
| `TRIM_VELOCITY_WINDOW` | **60 trading days** | [DEFAULT] | ~3 calendar months lookback for velocity gate. |
| `MIN_WEIGHT` | **3 % of portfolio** | [DEFAULT] | Raised from 2% after backtest; stronger compounder protection. |
| `RESERVE_TARGET` | 20 % of portfolio | [LOCKED] | Enough powder to fund several names to 3× in a crash. |
| `BRAKE_RELEASE_PCT` | **10 % off spike peak** | [DEFAULT] | A standard "correction" retrace = momentum has cooled. |
| `FROTH_THRESHOLD` | **+15 % above 10-yr avg fwd P/E** | [DEFAULT] | Moderate premium; lands near the source's "fair→risky" zone. |
| `CASH_CEILING` | **5 %** | [DEFAULT] | Lowered from 20% by decision — see A.7; keeps total non-deployed near ~25%, not ~40%. |
| `MAX_HOLDINGS` | 20 stocks (ETF excl.) | [LOCKED] | Breadth you can actually track. |

### A.2 `BASE_UNIT` and tier weights — the sizing spine

**`BASE_UNIT` has no default on purpose.** It is the one number that must come from
*your* cash flow, not from analysis. The source's own test is "it should feel like
just another Tuesday" — i.e. painless enough that you never skip it in a scary
market. A defensible method: take the monthly surplus you can invest without
touching the emergency fund, hold back a buffer for the 50/50 banking and the 3×
crash cap, and set `BASE_UNIT` so that a *normal* month deploys comfortably inside
that surplus. Setting it too high is the classic failure mode — it makes the
double-down and the 3× cap unfundable exactly when they matter.

**Tier weights — [DEFAULT] T1 = 2.0, T2 = 1.0, ETF = derived.** Rationale: highest-
conviction names get twice the per-name flow of secondary names — a deliberate but
not reckless tilt (2:1, not 5:1, because concentration risk compounds). The ETF is
*not* a conviction pick; its weight is a **solved** number so that scheduled
contributions naturally hold the 40–50% core without relying only on Rule 3's
corrective steering:

```
ETF_weight = ( p / (1 − p) ) × Σ(individual tier weights)      where p = target core share
```

Worked example (p = 0.45, holding 8 T1 + 6 T2):
Σ individual = 8×2.0 + 6×1.0 = 22 → `ETF_weight = (0.45 / 0.55) × 22 ≈ 18`.
So with those holdings the ETF's per-period weight ≈ 18. **Recompute this whenever
your holding count changes** — a fixed ETF weight silently drifts the core
allocation as you add or drop names.

### A.3 Hysteresis parameters (`ARM_*`, `TRIM_ARM_BUFFER`) — the geometry

The re-arm gaps are not arbitrary. The whole point of hysteresis is that the "start
buying" line and the "reset" line must be far enough apart that ordinary noise
can't bounce price across both. The rule of thumb I used: **the gap ≈ half the first
band's depth.**
- Stocks: first band –20%, arm at –10% → 10pp gap.
- ETFs: first band –10%, arm at –5% → 5pp gap.
- Trims: rungs are 50pp apart, so a 20pp `TRIM_ARM_BUFFER` is well under one rung
  (can't skip a level) but large enough that a normal wiggle around +100% won't
  re-fire a sell. **[DEFAULT] 20pp.**

If you later find signals still re-firing on chop, *widen* these gaps — never narrow
them.

### A.4 `TRIM_PCT` = 20 % [DEFAULT] — what a full run does

Trimming 20% of *current* shares at each rung compounds: after all three rungs fire,
remaining shares = 0.8³ ≈ **0.51**. So a name that triples and hits every rung ends
up roughly **half sold, half left to run** — meaningful de-risking without
amputating the compounder. 10% would barely de-risk; 30%+ starts sacrificing too
much upside on your best names. An escalating alternative (e.g. 15 / 20 / 25%)
trims harder the more extended the stock gets — reasonable if you want the schedule
to lean more defensive; left as an option, not the default.

### A.5 `MIN_WEIGHT` = 3 % [DEFAULT] — raised after backtest

With `MAX_HOLDINGS` = 20 individual names sharing roughly the non-core ~55% of the
book, the average name is ~2.75%. A 3% floor (raised from 2% after the SPY backtest
showed over-trimming of compounders) means trimming can shrink a winner modestly but
never below a *meaningful* position. At 3%, a trimmed name still has enough weight to
contribute to portfolio returns — it can't be whittled into an irrelevant stub. If
you run a more concentrated book (fewer names), raise this proportionally.

### A.6 Valuation & momentum (`FROTH_THRESHOLD`, `BRAKE_RELEASE_PCT`) [DEFAULT]

- **`FROTH_THRESHOLD` = +15% above the 10-yr average forward P/E.** The S&P's
  10-year average forward P/E has historically sat in roughly the high-teens
  (this is a *general* range, not a live figure — the engine reads the real number
  from the API). A +15% premium lands you near a low-20s forward P/E, which
  coincides with the source's own "fair value → getting risky" zone (20–24). It's a
  *moderate* trip-wire: rich enough to matter, not so tight it fires in every
  healthy market. If you can compute it, "+1 standard deviation above the 10-yr
  mean" is a more statistically grounded alternative.
- **`BRAKE_RELEASE_PCT` = 10% off the spike peak.** The brake exists to stop you
  chasing a vertical move (+20% in 30 days). A 10% pullback from the local peak is
  the conventional "correction" marker — enough to say the melt-up has cooled
  without waiting for a full trend reversal that would keep you side-lined too long.

### A.7 Resolved: `CASH_CEILING` lowered to 5% × `RESERVE_TARGET` (20%)

`CASH_CEILING` and `RESERVE_TARGET` were originally both set to 20% in separate
decisions. Together, because the reserve up to its target is *exempt* from the cash
floor, they permitted far more idle capital than the "never sit in cash" intent
implies:

```
exempt reserve (up to 20%)  +  old idle allowance (up to 20%)  ≈  up to ~40% not deployed
```

That ~40% combined tolerance fought the anti-cash-drag rule. **Decision taken:
`CASH_CEILING` lowered from 20% → 5%.** Now anything beyond the earmarked 20%
reserve gets a deploy nudge almost immediately:

```
exempt reserve (up to 20%)  +  new idle allowance (up to 5%)  ≈  up to ~25% not deployed
```

This keeps a meaningful 20% war chest for crashes while ensuring you don't drift
into a large idle-cash position on top of it. The anti-cash stance now wins over
war-chest size, by choice. If you later want the reserve to breathe larger, raise
`CASH_CEILING` again — deliberately.

### A.9 Trim velocity gate (+30% in 60 days) [DEFAULT] — the key backtest lesson

The SPY backtest (Appendix B) showed that trimming a steady compounder — one that
doubled over 5+ years of grinding up — destroyed more value than it protected. The
trim rule is *meant* for momentum spikes: a stock that pops +40% in a month on
earnings hype or a sector rotation. Those gains are fragile and worth harvesting.
A stock that doubled by compounding 8%/year for 9 years is *not* fragile — selling
it just amputates your best performer.

The velocity gate separates these two cases with one check: **has the stock gained
at least +30% over the trailing 60 trading days (~3 calendar months)?** If yes, the
recent run is fast enough that the trim band fires. If no, the gain was gradual
compounding, and the band stays armed but suppressed.

Why +30% / 60 days specifically:
- A +30% move in 60 trading days annualizes to roughly +150% — well above any
  normal compounding rate. It's unambiguously "hot."
- 60 days is long enough to filter out one-day earnings pops (which may reverse)
  but short enough to catch a genuine momentum wave before it peaks.
- The check is directional: it measures *recent acceleration*, not total gain.
  A stock up +200% over 5 years that suddenly jumps another +35% in 2 months
  *will* pass the velocity gate — the trim fires precisely because the *recent*
  move is fast, even though the total gain was slow.

The gate can be tightened (+40%/30d) or loosened (+25%/90d) after a multi-name
backtest. Starting moderate.

### A.10 What I did *not* default, and why

`BASE_UNIT` (yours to set from budget), the **tier membership** and **avoid-list**
(static config, your judgment), and the **emergency-fund flag** (a fact only you
know) are intentionally left blank. Defaulting them would be guessing at your
personal finances, which is exactly the kind of number this file should not invent.

---

## Appendix B — Backtest Results (SPY, 2000–2026)

> **What this tested:** the engine *mechanics* on a neutral, bias-free universe
> (SPY). This validates whether the rules add value vs. naive DCA — it says
> nothing about stock picks.

**Setup:** $1,000/month base, SPY only, daily-on-close, Jan 2000 – Jul 2026
(26.5 years). Four arms, same data, same total inflows for comparable arms:

| Strategy | Contributed | Final value | Total return | Max drawdown |
|---|---|---|---|---|
| A: Plain DCA | $320K | $1.89M | 492% | –48% |
| B: DCA + double-down | $358K | $2.10M | 488% | –47% |
| C: DCA + DD + trim | $358K | $0.90M | 151% | –47% |
| D: Full system | $320K | $1.30M | 305% | –37% |

**Key findings:**

1. **Double-down works (B > A).** Every extra $1 contributed via bands generated
   $5.52 of final value. Buying discounted shares that then compound for years is
   mechanically sound. The bands, 3× cap, and hysteresis are validated.

2. **Trim destroyed value on a single holding (C).** When trims fired on SPY's
   +100% gain, they sold the only compounder and moved proceeds to cash with no
   other dip to buy. This is a *structural limitation of the single-holding test*,
   not a flaw in the trim rule — but it motivated two changes:
   - **Raised trim bands** from +100/+150/+200 to **+120/+170/+220** (let
     compounders run longer).
   - **Added velocity gate** (+30% in 60 days) so slow compounders are never
     trimmed — only momentum spikes.
   - **Raised `MIN_WEIGHT`** from 2% to **3%** (stronger floor).

3. **Full system reduced drawdowns significantly (D).** –37% max drawdown vs –48%
   for plain DCA — an 11pp cushion. The return cost came from the 50/50 reserve
   split (half the money sat idle with no other name's dip to deploy into).

4. **The 50/50 split and trim recycling need a multi-name backtest.** They are
   designed for a 20-name book where the reserve flows between names; a single-SPY
   test structurally cannot validate them. **Do not ship to live money without
   running a multi-name basket test (e.g. 5 sector ETFs).**

5. **This test validated the buy-side engine. The sell-side (trim + reserve) is
   unproven on neutral data.** Keep this separation sharp.
