// Turn a raw signal into plain-English guidance: what to do, and why.
import { tickerLabel } from "./companyNames.js";
import { pct, usd, usd2 } from "./fmt.js";

// Broker-cost footnote. The engine prices every order (IBKR Tiered + GST, Rule 8) into
// metrics.cost; this just renders it, alongside the cash the order really moves. Returns
// null when the cost model is off or the signal isn't an order.
export function costLine(sig) {
  const m = sig.metrics || {};
  const c = m.cost;
  if (!c) return null;
  // Contribution-funded buys are sized so shares + fee fit the budget, so the fee is
  // shown as coming OUT of the contribution rather than added on top of it.
  const cash =
    m.budget != null ? `${usd2(m.budget)} contribution, ${usd2(c.total)} of it to IBKR`
    : m.cash_needed != null ? `${usd2(m.cash_needed)} leaves your account`
    : m.net_proceeds != null ? `${usd2(m.net_proceeds)} lands in your reserve`
    : null;
  return {
    text: `Fees ≈ ${usd2(c.total)} (${pct(m.cost_ratio ?? 0)} of the order)` +
      (cash ? ` · ${cash}` : ""),
    detail:
      `${c.venue}: commission ${usd2(c.commission)} + GST ${usd2(c.gst)}` +
      (c.exchange_fee ? ` + exchange ${usd2(c.exchange_fee)}` : "") +
      (c.clearing_fee ? ` + clearing ${usd2(c.clearing_fee)}` : ""),
  };
}

const pctAbs = (x) => `${Math.abs(x * 100).toFixed(0)}%`;
const shares = (n) => (n >= 1 ? n.toFixed(0) : n.toFixed(3));

export function describe(sig) {
  const m = sig.metrics || {};
  const price = m.price;
  const priceStr = price ? usd2(price) : "the current price";
  const approxShares = price && sig.size_dollars ? sig.size_dollars / price : sig.size_shares;

  if (sig.rule === "buy") {
    const trigger = m.trigger ?? "dip";
    const isStarter = m.new_position === true;

    if (trigger === "cadence") {
      return {
        headline: `Buy ${usd(m.deploy ?? sig.size_dollars)} of ${tickerLabel(sig.ticker)} at ~${priceStr}`,
        sub: `Rolling contribution${m.bank ? ` · plus ${usd(m.bank)} to reserve` : ""}` +
          (price && approxShares ? ` · ≈ ${shares(approxShares)} shares` : ""),
        why:
          `It's been a while since your last ${sig.ticker} buy, and there's no dip to chase right ` +
          `now — so your rolling plan buys anyway rather than waiting indefinitely for one` +
          (m.brake ? " (halved — it's already spiked recently, so you're not chasing it)" : "") +
          (m.froth ? " (halved — the market looks expensive right now)" : "") + ".",
        tone: "buy",
      };
    }

    // trigger === "dip" (optionally "dip+cadence" folded in) — either an existing
    // position averaging down, or a watchlist name opening a new starter position.
    return {
      headline: `Buy ${usd(sig.size_dollars)} of ${tickerLabel(sig.ticker)} at ~${priceStr}`,
      sub: price ? `≈ ${shares(approxShares)} shares${isStarter ? " (new starter position)" : ""}` : null,
      why:
        `${sig.ticker} has dropped ${pctAbs(m.dd ?? 0)} below its 52-week high` +
        (m.high_52w ? ` of ${usd2(m.high_52w)}` : "") +
        (isStarter
          ? (m.opportunity_score != null
              ? `. That's an unusually large move for this stock (opportunity score ${m.opportunity_score.toFixed(1)})`
              : "") +
            `. You don't own this yet — your rule flags it as a starter-sized opening position, ` +
            `not an averaging-down buy.`
          : `. That's past your "buy the dip" level${(m.bands?.length ?? 1) > 1 ? "s" : ""}, ` +
            `so your rule adds to the position while it's cheaper. (Dip-buys ignore valuation on purpose.)`) +
        (sig.metrics?.trigger === "dip+cadence"
          ? ` It's also been a while since your last buy here — this covers both.`
          : ""),
      tone: "buy",
    };
  }

  if (sig.rule === "trim") {
    const floored = m.floored;
    return {
      headline: floored
        ? `${tickerLabel(sig.ticker)} hit a trim level — but it's already at your floor`
        : `Sell ${usd(sig.size_dollars)} of ${tickerLabel(sig.ticker)} at ~${priceStr}`,
      sub: floored ? `No shares sold — protecting your minimum position size.` :
        `≈ ${shares(approxShares)} shares (taking profit)`,
      why:
        `${sig.ticker} is up ${pctAbs(m.gain ?? 0)} versus your average cost` +
        (m.ret_60d != null ? ` and ran up ${pctAbs(m.ret_60d)} in the last ~3 months` : "") +
        `. Your rule harvests fast gains like this — selling ${pctAbs(m.trim_pct ?? 0.2)} of the ` +
        `position and letting the rest keep running.`,
      tone: "sell",
    };
  }

  if (sig.rule === "core_build") {
    return {
      headline: `Buy ${usd(sig.size_dollars)} of ${tickerLabel(sig.ticker)} at ~${priceStr}`,
      sub: `Building your index core${price && approxShares ? ` · ≈ ${shares(approxShares)} shares` : ""}`,
      why:
        `Your index core is only ${pctAbs(m.core_weight ?? 0)} of your portfolio, below your ` +
        `${pctAbs(m.target ?? 0.4)} target. Steadily feeding the index anchors everything else — ` +
        `this is your long-term base, not a trade.` +
        (m.opportunistic
          ? ` Bonus: it's also ${pctAbs(m.dd ?? 0)} below its 52-week high right now` +
            (m.opportunity_score != null ? ` (opportunity score ${m.opportunity_score.toFixed(1)})` : "") +
            ` — good timing to add.`
          : ` Do this before chasing individual dips.`) +
        (m.rotating?.length > 1
          ? ` Your core rotates between ${m.rotating.join(", ")} — one order a month instead of ` +
            `${m.rotating.length}, because each order pays the exchange fee minimum whether ` +
            `it's $180 or $540.`
          : ""),
      tone: "buy",
    };
  }

  if (sig.rule === "cash_floor") {
    return {
      headline: `Buy ${usd(sig.size_dollars)} of ${tickerLabel(sig.ticker)} at ~${priceStr}`,
      sub: `Deploying idle cash into your core${price && approxShares ? ` · ≈ ${shares(approxShares)} shares` : ""}`,
      why:
        `You're holding more idle cash than your comfort limit. Your rule deploys the excess ` +
        `into your ETF core rather than letting it drag.`,
      tone: "buy",
    };
  }

  if (sig.rule === "replace") {
    return {
      headline: `Want to add ${tickerLabel(sig.ticker)}? You'll need to drop a name first`,
      sub: null,
      why: `You're at your cap of 20 individual stocks. To add ${sig.ticker}, sell out of one you like less.`,
      tone: "neutral",
    };
  }

  return { headline: `${sig.action} ${tickerLabel(sig.ticker)}`, sub: null, why: sig.cause, tone: "neutral" };
}
