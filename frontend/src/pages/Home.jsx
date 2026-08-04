import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api.js";
import { cls, pct, pctSign, usd, usd2 } from "../fmt.js";
import RunButton from "../components/RunButton.jsx";

function Stat({ label, value, klass }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className={`value ${klass || ""}`}>{value}</div>
    </div>
  );
}

// gainAbs and invested are derived client-side from shares/avg_basis/value.
// Column order: identity → live price → today's move → total return → position size → cost basis → weight
const HOLDINGS_COLUMNS = [
  { key: "ticker",          label: "Ticker",   sortValue: (h) => h.ticker },
  { key: "price",           label: "Price",    sortValue: (h) => h.price },
  { key: "day_change_pct",  label: "Day %",    sortValue: (h) => h.day_change_pct },
  { key: "day_change_abs",  label: "Day $",    sortValue: (h) => h.day_change_abs },
  { key: "gain_pct",        label: "Gain %",   sortValue: (h) => h.gain_pct },
  { key: "gain_abs",        label: "Gain $",   sortValue: (h) => h.value != null ? h.value - h.avg_basis * h.shares : null },
  { key: "value",           label: "Value",    sortValue: (h) => h.value },
  { key: "invested",        label: "Invested", sortValue: (h) => h.avg_basis * h.shares },
  { key: "avg_basis",       label: "Avg cost", sortValue: (h) => h.avg_basis },
  { key: "weight",          label: "Weight",   sortValue: (h) => h.weight },
];

function sortHoldings(holdings, sortKey, sortDir) {
  if (!sortKey) return holdings;
  const col = HOLDINGS_COLUMNS.find((c) => c.key === sortKey);
  const dir = sortDir === "desc" ? -1 : 1;
  return [...holdings].sort((a, b) => {
    const av = col.sortValue(a);
    const bv = col.sortValue(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    if (typeof av === "string") return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });
}

export default function Home() {
  const [summary, setSummary] = useState(null);
  const [holdings, setHoldings] = useState([]);
  const [names, setNames] = useState({});
  const [pending, setPending] = useState(0);
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState("asc");

  const load = () => {
    api.summary().then(setSummary);
    api.holdings().then((hs) => {
      setHoldings(hs);
      Promise.all(hs.map((h) => api.info(h.ticker).then((r) => [h.ticker, r?.name || null]))).then(
        (pairs) => setNames(Object.fromEntries(pairs))
      );
    });
    api.signals("PENDING").then((s) => setPending(s.length));
  };
  useEffect(() => { load(); }, []);

  if (!summary) return <div>Loading…</div>;

  const pricedHoldings = holdings.filter((h) => h.gain_pct != null);
  const inProfit = pricedHoldings.filter((h) => h.gain_pct > 0).length;

  const onSortClick = (key) => {
    if (key === sortKey) setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    else { setSortKey(key); setSortDir("asc"); }
  };
  const clearSort = () => { setSortKey(null); setSortDir("asc"); };
  const sortedHoldings = sortHoldings(holdings, sortKey, sortDir);

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Portfolio</h1>
        <RunButton onDone={load} />
      </div>

      {pending > 0 && (
        <Link to="/alerts" className="banner" style={{ display: "block", textDecoration: "none" }}>
          🔔 {pending} recommendation{pending > 1 ? "s" : ""} waiting — review on the Alerts page →
        </Link>
      )}

      <div className="grid cards">
        <Stat label="Total Invested"   value={usd(summary.total_cost)} />
        <Stat label="Current Value"    value={usd(summary.invested_equity)} />
        <Stat label="Total Gain $"     value={usd(summary.unrealized_gain)} klass={cls(summary.unrealized_gain)} />
        <Stat label="Total Gain %"     value={pctSign(summary.unrealized_gain_pct)} klass={cls(summary.unrealized_gain_pct)} />
        <Stat label="Reserve"          value={usd(summary.reserve_balance)} />
        <Stat label="Holdings"         value={`${summary.holdings_count} / 20`} />
        <Stat label="XIRR"             value={summary.xirr != null ? pctSign(summary.xirr) : "—"} klass={cls(summary.xirr)} />
        <Stat label="Core (ETF) weight" value={pct(summary.core_weight)} />
        <Stat label="Avg position size" value={pricedHoldings.length ? usd(summary.invested_equity / pricedHoldings.length) : "—"} />
        <Stat label="Fees paid YTD"    value={usd2(summary.fees_ytd ?? 0)} />
      </div>

      <div style={{ marginTop: 16 }}>
        <div className="card">
          <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
            <h2 style={{ marginTop: 0 }}>Holdings</h2>
            {sortKey && (
              <a href="#" className="muted" style={{ fontSize: 12 }}
                onClick={(e) => { e.preventDefault(); clearSort(); }}>
                Clear sort
              </a>
            )}
          </div>
          <table>
            <thead>
              <tr>
                {HOLDINGS_COLUMNS.map((c) => (
                  <th key={c.key} className={c.key === "ticker" ? "" : "num"}
                    onClick={() => onSortClick(c.key)} style={{ cursor: "pointer", userSelect: "none" }}
                    title={`Sort by ${c.label}`}>
                    {c.label}
                    {sortKey === c.key && <span className="muted"> {sortDir === "asc" ? "▲" : "▼"}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedHoldings.map((h) => {
                const invested = h.avg_basis * h.shares;
                const gainAbs = h.value != null ? h.value - invested : null;
                return (
                  <tr key={h.ticker}>
                    <td>
                      <Link to={`/ticker/${h.ticker}`} style={{ fontWeight: 700 }}>
                        {h.ticker}
                      </Link>
                      {names[h.ticker] && (
                        <div
                          title={names[h.ticker]}
                          style={{
                            fontSize: 11, color: "var(--muted)", marginTop: 2,
                            maxWidth: 120, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                            cursor: "default",
                          }}
                        >
                          {names[h.ticker]}
                        </div>
                      )}
                    </td>
                    <td className="num">{h.price ? usd2(h.price) : "—"}</td>
                    <td className={`num ${cls(h.day_change_pct)}`}>{pctSign(h.day_change_pct)}</td>
                    <td className={`num ${cls(h.day_change_pct)}`}>{h.day_change_abs != null ? usd2(h.day_change_abs) : "—"}</td>
                    <td className={`num ${cls(h.gain_pct)}`}>{pctSign(h.gain_pct)}</td>
                    <td className={`num ${cls(h.gain_pct)}`}>{gainAbs != null ? usd(gainAbs) : "—"}</td>
                    <td className="num">{h.value ? usd(h.value) : "—"}</td>
                    <td className="num">{usd(invested)}</td>
                    <td className="num">{usd2(h.avg_basis)}</td>
                    <td className="num">{pct(h.weight)}</td>
                  </tr>
                );
              })}
              {holdings.length === 0 && <tr><td colSpan={10} className="muted">No holdings — log a buy on the Trades page.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

    </>
  );
}
