import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import { api } from "../api.js";
import { cls, pct, pctSign, usd, usd2 } from "../fmt.js";
import RunButton from "../components/RunButton.jsx";

const COLORS = ["#4f8cff", "#35c26a", "#f5a623", "#f2585b", "#a06bff", "#2ec5c5", "#e0679a", "#8892a6"];

function Stat({ label, value, klass }) {
  return (
    <div className="card stat">
      <div className="label">{label}</div>
      <div className={`value ${klass || ""}`}>{value}</div>
    </div>
  );
}

// Holdings table columns — each `sortValue` returns the raw comparable for that row;
// missing values (e.g. no cached price yet) always sort to the bottom regardless of
// direction, so a null price/gain/weight never masquerades as the smallest or largest.
const HOLDINGS_COLUMNS = [
  { key: "ticker", label: "Ticker", sortValue: (h) => h.ticker },
  { key: "tier", label: "Tier", sortValue: (h) => h.tier },
  { key: "shares", label: "Shares", sortValue: (h) => h.shares },
  { key: "avg_basis", label: "Avg", sortValue: (h) => h.avg_basis },
  { key: "price", label: "Price", sortValue: (h) => h.price },
  { key: "gain_pct", label: "Gain", sortValue: (h) => h.gain_pct },
  { key: "weight", label: "Weight", sortValue: (h) => h.weight },
];

function sortHoldings(holdings, sortKey, sortDir) {
  if (!sortKey) return holdings;
  const col = HOLDINGS_COLUMNS.find((c) => c.key === sortKey);
  const dir = sortDir === "desc" ? -1 : 1;
  return [...holdings].sort((a, b) => {
    const av = col.sortValue(a);
    const bv = col.sortValue(b);
    if (av == null && bv == null) return 0;
    if (av == null) return 1;   // nulls always last, independent of direction
    if (bv == null) return -1;
    if (typeof av === "string") return dir * av.localeCompare(bv);
    return dir * (av - bv);
  });
}

export default function Home() {
  const [summary, setSummary] = useState(null);
  const [holdings, setHoldings] = useState([]);
  const [pending, setPending] = useState(0);
  const [sortKey, setSortKey] = useState(null);
  const [sortDir, setSortDir] = useState("asc");

  const load = () => {
    api.summary().then(setSummary);
    api.holdings().then(setHoldings);
    api.signals("PENDING").then((s) => setPending(s.length));
  };
  useEffect(() => { load(); }, []);

  if (!summary) return <div>Loading…</div>;
  const alloc = (summary.allocation || []).filter((a) => a.value > 0);

  // First click on a column sorts ascending; clicking the SAME column again flips to
  // descending; clicking a DIFFERENT column starts fresh at ascending.
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
        <Stat label="Invested equity" value={usd(summary.invested_equity)} />
        <Stat label="Unrealized" value={pctSign(summary.unrealized_gain_pct)} klass={cls(summary.unrealized_gain_pct)} />
        <Stat label="Reserve" value={usd(summary.reserve_balance)} />
        <Stat label="Core (ETF) weight" value={pct(summary.core_weight)} />
        <Stat label="Holdings" value={`${summary.holdings_count} / 20`} />
        {/* What the trading itself has cost — the feedback loop on order count (Rule 8). */}
        <Stat label="Fees paid YTD" value={usd2(summary.fees_ytd ?? 0)} />
      </div>

      <div className="two-col" style={{ marginTop: 16 }}>
        <div className="card">
          <h2 style={{ marginTop: 0 }}>Allocation</h2>
          {alloc.length === 0 ? <p className="muted">No priced holdings yet — run the engine to fetch prices.</p> : (
            <ResponsiveContainer width="100%" height={280}>
              <PieChart>
                <Pie data={alloc} dataKey="value" nameKey="ticker" outerRadius="70%">
                  {alloc.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
                </Pie>
                <Tooltip formatter={(v) => usd(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          )}
        </div>
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
                  <th key={c.key} className={c.key === "ticker" || c.key === "tier" ? "" : "num"}
                    onClick={() => onSortClick(c.key)} style={{ cursor: "pointer", userSelect: "none" }}
                    title={`Sort by ${c.label}`}>
                    {c.label}
                    {sortKey === c.key && <span className="muted"> {sortDir === "asc" ? "▲" : "▼"}</span>}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sortedHoldings.map((h) => (
                <tr key={h.ticker}>
                  <td><strong>{h.ticker}</strong></td>
                  <td><span className="pill">{h.tier}</span></td>
                  <td className="num">{h.shares}</td>
                  <td className="num">{usd2(h.avg_basis)}</td>
                  <td className="num">{h.price ? usd2(h.price) : "—"}</td>
                  <td className={`num ${cls(h.gain_pct)}`}>{pctSign(h.gain_pct)}</td>
                  <td className="num">{pct(h.weight)}</td>
                </tr>
              ))}
              {holdings.length === 0 && <tr><td colSpan={7} className="muted">No holdings — log a buy on the Trades page.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>
    </>
  );
}
