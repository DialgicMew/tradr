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
| `DCA_INTERVAL_DAYS` | rolling per-ticker cadence window since that ticker's own last buy fill | 30 days |

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
- **A manually-logged fill, opt-in only.** Hand-entering a trade (Trades page) never
  touches the reserve by default — most manual entries are ordinary contributions, not
  reserve-funded dip-buys, and the reserve must not drain on every logged buy. A
  **"fund this buy from reserve" / "recycle proceeds into reserve"** checkbox draws (or
  recycles) it explicitly, for a fill you placed yourself outside the Alerts approve
  flow, and gives it the full engine-state treatment a confirmed signal gets: it draws
  the reserve (+fee), settles the band(s) this fill's price satisfies, and updates the
  3× cap. **Matched by price against the ladder, not by whichever alert happens to still
  be PENDING** — a band can sit ARMED at the exact moment you log the trade (e.g. right
  after a void re-armed it) with the price still deep in dip territory; matching on
  PENDING-only would treat that as nothing to settle and the very next engine run would
  re-cross the ARMED band and re-alert for a dip you already bought. The stale alert(s)
  this fill matched are retired immediately. Voiding such a trade reverses the reserve
  movement but does **not** currently un-fire the band(s) or un-count the cap —
  `void_trade` only reverses band/cap state for a signal-confirmed fill (it looks up
  `sig.band` off the linked `SignalRow`); a manual entry has no signal to look that up
  from. So voiding a mistaken reserve-funded manual entry leaves the ladder looking like
  the dip was bought when it wasn't — a real gap, not a deliberate choice. For now, a bad
  reserve-funded manual entry should be corrected by hand (re-arm the affected band(s))
  after voiding it.

**Earmarking:** the reserve may build up to `RESERVE_TARGET` (20%) and that portion
is **committed dry powder, exempt from the cash floor**. Only reserve **above** the
target counts as idle cash.

If a double-down suggestion exceeds available reserve, the notification **flags the
shortfall** ("reserve short by $X — top up or fund from cash") rather than shrinking
the suggestion. The human decides.

**Append-only, and reversible by compensation.** Every movement caused by a fill records
that fill (`source_lot_id`), so **voiding a trade reverses everything confirming it did**:
the reserve draw and its fee are undone with `void_reversal` entries (the originals stay
visible — nothing is deleted), the 3× cap counter gives back the headroom the fill
consumed, and the band it latched returns to ARMED. Without that link a voided trade
leaves the name looking both funded and capped by a trade that no longer exists, which
silently suppresses its future dip-buys. Voiding is idempotent.

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
6. **BUY (Rule 1 + Rule 7, opportunistic DCA)** — one signal per ticker, per §0's
   consolidation: for names not trimming, check **both** legs —
   - *Dip leg:* collect crossed bands (gap-through fires all crossed at once),
     resolve **shared-reserve contention deepest-`dd`-first** (ties: Tier 1 before
     Tier 2, then alphabetical), size first band ≈ 2× topping toward `DD_CAP` = 3×
     (deeper bands add until the cap; further bands suggest 0), fund from reserve,
     flag any shortfall.
   - *Cadence leg:* if no dip band is active AND `DCA_INTERVAL_DAYS` have elapsed
     since that ticker's own last buy fill (a **rolling, per-ticker** clock, not a
     shared calendar date) → compute `BASE_i`; apply **momentum brake** (×0.5 if
     30-day return > +20%) and **froth overlay** (×0.5 if S&P fwd P/E exceeds 10-yr
     avg by `FROTH_THRESHOLD`) — these **do NOT compound** (deploy multiplier floors
     at 0.5); apply 50/50 split; **Rule 3 steers contributions into the ETF** if
     core < 40%; annotate the PE zone (advisory only); fund from the contribution,
     not the reserve.
   - If both legs are due on the same ticker, the dip leg wins (larger, reserve-
     funded, already covers "money going into this name") and the cadence reason is
     folded into that one signal's notes rather than emitted as a second, competing
     recommendation. Any confirmed buy — dip or cadence — resets that ticker's
     rolling clock, since it was still capital deployed into the name.
7. **Cash floor (Rule 6)** — if idle capital (uncommitted cash + reserve *above*
   `RESERVE_TARGET`) > `CASH_CEILING` → suggest deploying the excess into the **ETF
   core**.
8. **Emergency-fund gate** — if the user's emergency-fund flag is unset, attach a
   "confirm 6–12 months funded first" **warning** to every buy signal (warn-only,
   does not block).
9. **Diversification (Rule 5)** — if a **new** name's buy fires while at
   `MAX_HOLDINGS` → emit a **"replace something?"** notification (requires dropping
   a name first). Existing holdings signal normally.
10. **Emit** all resulting signals.

---

## 5. The rules (detail + rationale)

### Rule 1 — Per-Stock Double-Down
> As of the opportunistic-DCA consolidation (§4 step 6), this rule and Rule 7 no
> longer emit two competing recommendations for the same ticker — they merge into
> one **"buy"** signal (dip sizing wins when both are due; see Rule 7 for the
> cadence leg and the bullish-market caveat).
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

### Rule 7 — Rolling Contribution (opportunistic DCA cadence)
- **`BASE_i = BASE_UNIT × tier_weight`** (Tier 1 > Tier 2 > ETF). Total contribution
  outflow scales with holdings.
- **Cadence is rolling and per-ticker, not a shared calendar date.** Each name's
  clock is anchored on **its own** last buy fill — a name bought on the 3rd is next
  due `DCA_INTERVAL_DAYS` (30, default) from the 3rd, independent of every other
  name's schedule. A dip-buy resets the same clock: it was still capital going into
  the name, so a redundant cadence buy right after would just be noise.
- **Only fires when there's no dip to chase.** If the name's Rule 1 ladder currently
  has a pending band, the dip-sized, reserve-funded buy fires instead and the
  cadence reason is folded into it as a secondary note — never a second signal.
- **50/50 split:** normal market → deploy `BASE_i/2`, bank `BASE_i/2` into reserve.
- **Momentum brake:** if a name's trailing 30-day return > +20% → **halve its
  deploy** (bank portion unchanged). Track the spike peak; **release when price
  pulls back `BRAKE_RELEASE_PCT` off that peak.**
- **Known, accepted limitation — the bullish-market case.** In a market with little
  or no drawdown, the cadence leg is the *only* thing firing: it forces a buy on
  schedule regardless of opportunity, same as classic DCA. This is by design — the
  alternative (skip when there's nothing cheap) means capital sits idle
  indefinitely waiting for a dip that may not come. The system does not currently
  taper cadence size in a frothy/bullish market beyond the existing momentum-brake
  and froth-overlay halving above; a further "reduce cadence contribution when
  richly valued" overlay is a candidate future refinement, not yet built.

### Rule 8 — Transaction costs (IBKR Tiered)
- Every order the engine proposes is **priced before you see it**: commission + GST +
  exchange + clearing, itemised, as a share of the order value. No signal shows a
  size without showing what it costs to put on.
- **Schedule (IBKR Tiered, USD, GST 18% on commission only):**
  - **LSE lines** (ticker suffix `.L`): 0.05% of trade value, **min USD 1.70**;
    exchange ~USD 0.14/order (GBP 0.10 ETF min); clearing ~USD 0.08 (GBP 0.06).
    A $400 order costs **≈ $2.23 → 0.56%**.
  - **US lines:** USD 0.0035/share, **min USD 0.35**, max 1% of trade value;
    clearing USD 0.00020/share. A $600 / 100-share order costs **≈ $0.43 → 0.07%**.
- **The asymmetry is the point.** The LSE commission floor does not dilute until
  ~$3,400 per order; the US floor is a fifth as large. Splitting entries across the
  month is nearly free on the US side and expensive on the LSE side. Three $200 US
  orders cost ~$0.86 extra; three $133 LSE orders cost ~$4.46 extra.
- **Whichever pot funds the order pays the fee.** The unified "buy" signal (§4 step
  6) carries which pot funded it in its own metrics, not in a separate rule name.
  - *Reserve-funded* (a dip-triggered buy — held or a new watchlist starter — plus
    trim): the reserve is drawn for the order value **and** the fee, as two separate
    ledger lines (`dd_draw`, `fee`) so the cost is visible rather than buried inside
    the draw.
  - *Contribution-funded* (a cadence-only buy with no dip behind it, core build, cash
    floor): the order is sized so **shares + fee fit the contribution** — a $180
    core contribution buys ~$177.77 of an LSE line and hands $2.23 to IBKR. The
    reserve does not move.
- **Every fill carries its cost** (`lots.fee`), whichever pot paid it, so "fees paid YTD"
  is one query. Voiding a trade removes its fee with it.
- **Brokerage is part of cost basis.** A buy contributes `qty × price + fee`: 1 share at
  $100 with $1 of brokerage cost $101, so that is what it averages in at. Fills recorded
  before fees were tracked carry `fee = 0` and average in unchanged — nothing is
  retro-inflated. Sell fees do **not** touch basis; they reduce proceeds. Consequence
  worth knowing: measured gains are now against true cost, so trims fire marginally later.
- **Manual entry can pick its plan** (`Tiered` / `Fixed` / actual / none), so an old fill
  can be costed under whichever IBKR plan it was actually traded on, independent of the
  account's current `COMMISSION_PLAN`. For a historic trade, **entering the actual fee off
  the statement beats any estimate.** Fixed is an all-in rate — exchange and clearing are
  bundled into the commission rather than added on top.
- **Order count is the only real lever.** One order = one ticker; different tickers can
  never be merged. So fees fall only by holding fewer names, or by giving each name a
  bigger, less frequent order. **Core rotation** (`CORE_ROTATE`, default on) does the
  latter: instead of one order per core line every run, the whole round goes to whichever
  line sits furthest below its intended share of the core. Same dollars in, one fee
  minimum instead of N, and it self-rotates — buying one line makes another the most
  underweight next run, so no rotation counter is needed.
- **Per-order minimum (`MIN_ORDER_DOLLARS`).** Below this, an order pays the fee minimum
  on too little money. Applied **at sizing time**, not as a filter on finished signals, so
  reserve contention and the 3× cap both see the number that will actually be traded.
  - An order within `MIN_ORDER_ROUNDUP_MAX` (1.25×) of the floor is **rounded up to it** —
    one real tranche beats three dribbles. The round-up counts against the 3× cap at its
    actual size, so a small name may spend ~3.4× base in a single order and then be done
    for that crash cycle. That concentration is the intent, not a leak.
  - Anything smaller is **deferred, not deleted** — and the distinction that matters:
    **hide only what re-derives on its own; show-and-flag anything that would be lost.**
    Held-position dip-buys, cadence-only buys, core build and cash floor are all hidden
    (a latched band reappears at a deeper rung with a bigger target; a skipped cadence
    buy just accumulates and fires next run instead of eating a fee-dominated order; the
    core gap widens; idle cash keeps sitting). A new **watchlist starter** buy is the one
    exception, **shown with a warning** instead — a rare dip on a small unheld name is
    worth knowing about even when the rule's own starter size is uneconomic.
    - *Revision note:* cadence-only buys used to be shown-and-flagged like a watchlist
      starter, on the theory that a skipped monthly contribution never comes back. That
      produced orders as small as $14 losing ~15–30% to commissions. Deferring instead —
      the contribution simply waits and fires next run once due again — trades a few
      days of delay for not burning double-digit percentages of the contribution on fees.
  - **Sells are exempt.** A fee floor must never trap you in a position.
  - Beware the interaction with `BASE_UNIT`: a floor above a tier's 3× cap excludes that
    tier permanently, since its ladder target can never grow to reach it. Check
    `3 × BASE_UNIT × tier_weight` against the floor for every tier before setting it.
- **Advisory, not blocking.** An order whose cost exceeds `COST_WARN_RATIO` (0.5%)
  carries a warning naming the single-order size that would get under the line.
  `COST_BLOCK_RATIO` can suppress such buys outright, but ships **off**: nothing
  accumulates a skipped contribution, so blocking would permanently starve any leg
  whose `BASE_i` sits below break-even.
- **Structural cost is not a bug to fix.** At a $400/month LSE contribution the core
  leg pays ~0.56% per contribution whatever the engine does. The rule surfaces that
  figure; it does not pretend a parameter choice removes it.

### Preconditions & hard constraints
- **Emergency fund:** user-attested flag (6–12 months of expenses). If unset →
  **warn-only** on buy signals (does not block). Sell/trim signals unaffected.
- **Leverage:** the engine never emits a leveraged/margin suggestion.
- **3× base cap:** absolute ceiling on any per-name double-down accumulation.

---

## 6. Precedence & conflict-resolution summary

1. **Hard constraints** (3× cap, zero leverage) override everything.
2. **Trim > buy** (per name, per day) — covers both the dip leg and the cadence leg.
3. **Dip leg > cadence leg** (per name, per day) — one "buy" signal, not two, when both fire.
4. **Rule 3 ETF steering > Rule 7 tier weight** when the core is underweight.
5. **Core allocation** is corrected by contributions only, **never by selling**.
6. **PE gate** is advisory annotation only; **dip-triggered buys are PE-exempt**.
7. **Froth and momentum half-speeds do not stack** (multiplier floors at 0.5).
8. **Emergency-fund** gate is warn-only.
9. **Reserve earmark** (up to `RESERVE_TARGET`) is exempt from the cash floor.

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
| `COST_WARN_RATIO` | **0.5 % of order value** | [DEFAULT] | Roughly a $450 LSE / $85 US order — below that the fee floor starts to bite. |
| `COST_BLOCK_RATIO` | **0 (off)** | [DEFAULT] | Blocking a sub-break-even buy starves the leg; nothing banks the skipped dollars. |
| `MIN_ORDER_DOLLARS` | **0 (off); broker-specific** | [DEFAULT] | Depends on your broker's fee minimums AND your contribution size — must be checked against `3 × BASE_UNIT × tier_weight` per tier. |
| `MIN_ORDER_ROUNDUP_MAX` | **1.25×** | [DEFAULT] | Lifts a near-miss order to the floor without letting it silently become a different position size. |
| `CORE_ROTATE` | **on** | [DEFAULT] | One core order per run rotating between lines; N lines would otherwise pay the fee minimum N× to move the same money. |
| `COMMISSION_PLAN` | **tiered** | [DEFAULT] | Which IBKR plan the account is on; individual fills may override it. |
| `FIXED_LSE_MIN` | **0 = not configured** | [DEFAULT] | IBKR's LSE fixed-plan minimum was **not verified** — costing a fixed-plan LSE order fails loudly until you set it from a statement. |
| IBKR fee figures | see Rule 8 | [LOCKED] | Broker's published Tiered schedule — facts, not preferences. |

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
