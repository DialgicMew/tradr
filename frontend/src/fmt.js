export const usd = (n) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
export const usd2 = (n) =>
  n == null ? "—" : n.toLocaleString("en-US", { style: "currency", currency: "USD" });
export const pct = (n) => (n == null ? "—" : `${(n * 100).toFixed(1)}%`);
export const pctSign = (n) => (n == null ? "—" : `${n >= 0 ? "+" : ""}${(n * 100).toFixed(1)}%`);
export const cls = (n) => (n == null ? "" : n >= 0 ? "pos" : "neg");
