// Ticker -> display name, for showing "what is this" alongside a bare ticker on
// Alerts/Trades. Deliberately a static lookup, not a live API call — these change rarely
// and a stale name here is harmless, unlike a stale price. Covers the app's known
// universe (see UniverseRow); an unmapped ticker just renders without a name suffix.
export const COMPANY_NAMES = {
  AMD: "Advanced Micro Devices",
  AMZN: "Amazon",
  ANET: "Arista Networks",
  ARM: "Arm Holdings",
  ASML: "ASML Holding",
  AVGO: "Broadcom",
  BE: "Bloom Energy",
  CDNS: "Cadence Design Systems",
  CEG: "Constellation Energy",
  CRDO: "Credo Technology Group",
  CRWD: "CrowdStrike Holdings",
  CRWV: "CoreWeave",
  "CSPX.L": "iShares Core S&P 500 UCITS ETF USD (Acc)",
  DDOG: "Datadog",
  "EIMI.L": "iShares Core MSCI EM IMI UCITS ETF",
  EWY: "iShares MSCI South Korea ETF",
  GOOGL: "Alphabet",
  HOOD: "Robinhood Markets",
  HOVR: "New Horizon Aircraft",
  IREN: "IREN Limited",
  META: "Meta Platforms",
  MSFT: "Microsoft",
  MU: "Micron Technology",
  NVDA: "NVIDIA",
  ORCL: "Oracle",
  PLTR: "Palantir Technologies",
  RDDT: "Reddit",
  SNDK: "SanDisk",
  SOFI: "SoFi Technologies",
  TSLA: "Tesla",
  TSM: "Taiwan Semiconductor Manufacturing",
  VRT: "Vertiv Holdings",
  "VWRA.L": "Vanguard FTSE All-World UCITS ETF USD (Acc)",
};

// `${ticker} (Name)` when a name is known, else just the bare ticker.
export function tickerLabel(ticker) {
  const name = COMPANY_NAMES[ticker];
  return name ? `${ticker} (${name})` : ticker;
}
