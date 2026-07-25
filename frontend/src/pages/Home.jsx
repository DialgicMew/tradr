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

export default function Home() {
  const [summary, setSummary] = useState(null);
  const [holdings, setHoldings] = useState([]);
  const [pending, setPending] = useState(0);

  const load = () => {
    api.summary().then(setSummary);
    api.holdings().then(setHoldings);
    api.signals("PENDING").then((s) => setPending(s.length));
  };
  useEffect(() => { load(); }, []);

  if (!summary) return <div>Loading…</div>;
  const alloc = (summary.allocation || []).filter((a) => a.value > 0);

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
          <h2 style={{ marginTop: 0 }}>Holdings</h2>
          <table>
            <thead>
              <tr><th>Ticker</th><th>Tier</th><th className="num">Shares</th><th className="num">Avg</th>
                <th className="num">Price</th><th className="num">Gain</th><th className="num">Weight</th></tr>
            </thead>
            <tbody>
              {holdings.map((h) => (
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
