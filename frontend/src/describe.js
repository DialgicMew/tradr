// Turn a raw signal into plain-English guidance: what to do, and why.
import { usd, usd2 } from "./fmt.js";

const pctAbs = (x) => `${Math.abs(x * 100).toFixed(0)}%`;
const shares = (n) => (n >= 1 ? n.toFixed(0) : n.toFixed(3));

export function describe(sig) {
  const m = sig.metrics || {};
  const price = m.price;
  const priceStr = price ? usd2(price) : "the current price";
  const approxShares = price && sig.size_dollars ? sig.size_dollars / price : sig.size_shares;

  if (sig.rule === "double_down") {
    return {
      headline: `Buy ${usd(sig.size_dollars)} of ${sig.ticker} at ~${priceStr}`,
      sub: price ? `≈ ${shares(approxShares)} shares` : null,
      why:
        `${sig.ticker} has dropped ${pctAbs(m.dd ?? 0)} below its 52-week high` +
        (m.high_52w ? ` of ${usd2(m.high_52w)}` : "") +
        `. That's past your "buy the dip" level${(m.bands?.length ?? 1) > 1 ? "s" : ""}, ` +
        `so your rule adds to the position while it's cheaper. (Dip-buys ignore valuation on purpose.)`,
      tone: "buy",
    };
  }

  if (sig.rule === "trim") {
    const floored = m.floored;
    return {
      headline: floored
        ? `${sig.ticker} hit a trim level — but it's already at your floor`
        : `Sell ${usd(sig.size_dollars)} of ${sig.ticker} at ~${priceStr}`,
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

  if (sig.rule === "base_dca") {
    return {
      headline: `Buy ${usd(m.deploy ?? sig.size_dollars)} of ${sig.ticker} at ~${priceStr}`,
      sub: `Monthly plan${m.bank ? ` · plus ${usd(m.bank)} to reserve` : ""}` +
        (price && approxShares ? ` · ≈ ${shares(approxShares)} shares` : ""),
      why:
        `It's your monthly contribution day. ${sig.ticker} gets its planned buy` +
        (m.steered ? ", boosted to steer your ETF core back toward 40–50%" : "") +
        (m.brake ? " (halved — it's already spiked recently, so you're not chasing it)" : "") +
        (m.froth ? " (halved — the market looks expensive right now)" : "") + ".",
      tone: "buy",
    };
  }

  if (sig.rule === "core_build") {
    return {
      headline: `Buy ${usd(sig.size_dollars)} of ${sig.ticker} at ~${priceStr}`,
      sub: `Building your index core${price && approxShares ? ` · ≈ ${shares(approxShares)} shares` : ""}`,
      why:
        `Your index core is only ${pctAbs(m.core_weight ?? 0)} of your portfolio, below your ` +
        `${pctAbs(m.target ?? 0.4)} target. Steadily feeding the index anchors everything else — ` +
        `this is your long-term base, not a trade. Do this before chasing individual dips.`,
      tone: "buy",
    };
  }

  if (sig.rule === "cash_floor") {
    return {
      headline: `Buy ${usd(sig.size_dollars)} of ${sig.ticker} at ~${priceStr}`,
      sub: `Deploying idle cash into your core${price && approxShares ? ` · ≈ ${shares(approxShares)} shares` : ""}`,
      why:
        `You're holding more idle cash than your comfort limit. Your rule deploys the excess ` +
        `into your ETF core rather than letting it drag.`,
      tone: "buy",
    };
  }

  if (sig.rule === "replace") {
    return {
      headline: `Want to add ${sig.ticker}? You'll need to drop a name first`,
      sub: null,
      why: `You're at your cap of 20 individual stocks. To add ${sig.ticker}, sell out of one you like less.`,
      tone: "neutral",
    };
  }

  return { headline: `${sig.action} ${sig.ticker}`, sub: null, why: sig.cause, tone: "neutral" };
}
