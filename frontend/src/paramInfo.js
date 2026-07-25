// Plain-language help for each engine parameter, shown on the Config page.
// `desc` = one/two-line meaning; `example` = concrete effect (tooltip on the ⓘ icon).
export const PARAM_INFO = {
  base_unit: {
    desc: "The size of one scheduled monthly buy for a baseline name. Everything else scales from this.",
    example: "If BASE_UNIT is $1,000, a Tier-1 stock (weight 2) gets a $2,000 monthly buy — split 50/50 between investing now and banking to your reserve.",
  },
  tier_weight: {
    desc: "How much more you put into higher-conviction names. Tier 1 > Tier 2 > ETF core.",
    example: "With T1=2 and T2=1 on a $1,000 base: Tier-1 names get $2,000/mo, Tier-2 names get $1,000/mo.",
  },
  snooze_days: {
    desc: "If you skip a signal, how many days it stays quiet before it can fire again.",
    example: "Skip a 'buy the dip' alert with snooze = 5, and tradr won't nag you about that same dip for 5 trading days.",
  },
  bands_stock: {
    desc: "The drops from a stock's 52-week high that trigger buying more of it.",
    example: "With −20/−30/−40/−50%: a stock down 20% from its high triggers the first buy-more signal, another at −30%, and so on.",
  },
  bands_etf: {
    desc: "Same as bands_stock, but for ETFs — they move less, so the triggers are shallower.",
    example: "With −10/−20/−30/−40%: an ETF down 10% from its high triggers the first buy-more signal.",
  },
  arm_stock: {
    desc: "A stock's buy-more ladder resets only after it recovers above this line — stops repeat alerts while it bounces near a bottom.",
    example: "After buying a −20% dip, the ladder won't re-arm until the stock climbs back to within −10% of its high.",
  },
  arm_etf: {
    desc: "The same reset line, for ETFs.",
    example: "An ETF's ladder re-arms once it recovers to within −5% of its high.",
  },
  dd_cap_mult: {
    desc: "Hard ceiling on how much you can add to one name during a crash, as a multiple of its base buy. Locked for safety.",
    example: "With cap 3×, a name with a $2,000 base can receive at most $6,000 of dip-buying in one crash — no matter how far it falls.",
  },
  dd_first_band_mult: {
    desc: "How big the FIRST dip-buy is, as a multiple of the base buy.",
    example: "With 2×, a stock's first −20% dip triggers a buy worth twice its normal monthly amount.",
  },
  trim_bands: {
    desc: "The gains above your average cost that trigger selling a slice (taking some profit).",
    example: "With +120/+170/+220%: once a stock is up 120% from what you paid, you get a trim signal to sell a portion.",
  },
  trim_pct: {
    desc: "What fraction of your shares to sell each time a trim triggers.",
    example: "At 20%, hitting all three trim rungs leaves you ~half your shares (0.8³ ≈ 51%) — you de-risk but let the rest run.",
  },
  trim_arm_buffer: {
    desc: "How far a gain must fall back before a trim rung can fire again — prevents repeat selling on small wiggles.",
    example: "A +120% trim rung won't fire again until the gain drops below +100%, then climbs past +120% once more.",
  },
  trim_velocity_pct: {
    desc: "A trim fires only if the stock ran up fast recently — this is the minimum recent gain required. Protects slow, steady compounders.",
    example: "At +30%: a stock that doubled slowly over years is left alone, but one that popped +35% in the last ~3 months gets trimmed.",
  },
  trim_velocity_window: {
    desc: "The recent lookback window (in trading days) used for the velocity check above.",
    example: "60 days ≈ 3 months — the run-up must clear the velocity threshold within this window to allow a trim.",
  },
  min_weight: {
    desc: "tradr will never trim a name below this share of your portfolio, so winners can't be whittled down to nothing.",
    example: "At 3%, a big winner gets trimmed down toward — but never below — 3% of your total portfolio.",
  },
  froth_threshold: {
    desc: "How expensive the overall market must get (vs its 10-year average P/E) before tradr halves your scheduled buys.",
    example: "At +15%, if the S&P's forward P/E runs 15% above its 10-year norm, monthly buys deploy at half speed until it cools.",
  },
  momentum_brake_ret: {
    desc: "If a name has already jumped this much in 30 days, tradr halves that month's buy so you don't chase a spike.",
    example: "At +20%, a stock up 25% this month gets a half-size scheduled buy (the amount banked to reserve is unchanged).",
  },
  brake_release_pct: {
    desc: "How far a spiking stock must pull back from its peak before the momentum brake is lifted.",
    example: "At 10%, once a hot stock drops 10% off its recent peak, normal-size buys resume.",
  },
  reserve_target: {
    desc: "The share of your portfolio you keep as 'dry powder' for crashes. This much is protected from the cash-floor nudge.",
    example: "At 20%, tradr treats up to 20% held in reserve as intentional — not idle cash it should push you to deploy.",
  },
  cash_ceiling: {
    desc: "How much idle cash (beyond the reserve target) you tolerate before tradr nudges you to deploy it into the ETF core.",
    example: "At 5%, if idle cash above your reserve exceeds 5% of the portfolio, you get a 'put this to work' signal.",
  },
  auto_bank_5050: {
    desc: "Whether each scheduled buy splits 50/50 — half invested now, half banked into your reserve for future dips.",
    example: "On: a $2,000 base buy invests $1,000 now and banks $1,000. Off: the full $2,000 is invested immediately.",
  },
  max_holdings: {
    desc: "The most individual stocks you'll hold (the ETF core doesn't count). At the cap, a new name asks you to drop one first.",
    example: "At 20, trying to buy a 21st stock triggers a 'replace something?' prompt instead of a plain buy.",
  },
};
