import { useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis, ReferenceLine } from "recharts";
import { api } from "../api.js";
import { cls, pct, pctSign, usd, usd2 } from "../fmt.js";

const RANGES = [
  { key: "7d",  label: "7D",  days: 7   },
  { key: "1m",  label: "1M",  days: 30  },
  { key: "6m",  label: "6M",  days: 182 },
];

function filterByDays(history, days) {
  const cutoff = new Date();
  cutoff.setDate(cutoff.getDate() - days);
  return history.filter((d) => new Date(d.as_of) >= cutoff);
}

function DetailRow({ label, value, sub, klass, mobile }) {
  return (
    <div style={{
      background: "var(--panel2)", borderRadius: 12,
      padding: mobile ? "14px 16px" : "20px 24px", display: "flex", flexDirection: "column", gap: 6,
    }}>
      <div style={{ color: "var(--muted)", fontSize: 12, textTransform: "uppercase", letterSpacing: "0.6px", fontWeight: 500 }}>
        {label}
      </div>
      <div className={klass || ""} style={{ fontSize: mobile ? 20 : 26, fontWeight: 700, lineHeight: 1.1 }}>
        {value}
      </div>
      {sub && (
        <div className={klass || "muted"} style={{ fontSize: 14, fontWeight: 500 }}>
          {sub}
        </div>
      )}
    </div>
  );
}

export default function Ticker() {
  const { ticker } = useParams();
  const sym = ticker.toUpperCase();

  const [history, setHistory] = useState([]);
  const [holding, setHolding] = useState(null);
  const [name, setName] = useState(null);
  const [range, setRange] = useState("1m");

  useEffect(() => {
    api.history(sym).then(setHistory);
    api.holdings().then((hs) => setHolding(hs.find((h) => h.ticker === sym) || null));
    api.info(sym).then((r) => setName(r?.name || null));
  }, [sym]);

  const rangeOpt = RANGES.find((r) => r.key === range);
  const data = filterByDays(history, rangeOpt.days);
  const first = data[0]?.price;
  const last = data[data.length - 1]?.price;
  const changePct = first && last ? (last / first - 1) : null;
  const isPos = changePct == null ? null : changePct >= 0;
  const lineColor = isPos === false ? "var(--red)" : "var(--green)";

  const fmtTick = (v) => {
    const d = new Date(v);
    if (rangeOpt.days > 60) {
      const mon = d.toLocaleDateString("en-US", { month: "short" });
      return `${mon} '${String(d.getFullYear()).slice(-2)}`;
    }
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  };

  const isMobile = window.innerWidth < 600;
  const chartHeight = Math.min(400, Math.round(window.innerWidth * 0.65));

  // Fewer tick labels on narrow screens to avoid crowding.
  const xInterval = data.length <= 14
    ? 1
    : rangeOpt.days > 60
    ? Math.max(1, Math.floor(data.length / (isMobile ? 4 : 6)))
    : Math.max(1, Math.floor(data.length / (isMobile ? 4 : 8)));

  const invested = holding ? holding.avg_basis * holding.shares : null;
  const gainAbs = (holding?.value != null && invested != null) ? holding.value - invested : null;
  const high52w = history.length > 0 ? history[history.length - 1]?.high_52w : null;

  return (
    <div>
      <div style={{ marginBottom: 20 }}>
        <Link to="/" style={{ color: "var(--muted)", fontSize: 13, display: "inline-flex", alignItems: "center", gap: 4 }}>
          ← Portfolio
        </Link>
      </div>

      {/* Header */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 30 }}>{sym}</h1>
            {name && <div style={{ color: "var(--muted)", fontSize: 14, marginTop: 3, fontWeight: 400 }}>{name}</div>}
          </div>
          {last && <span style={{ fontSize: 24, fontWeight: 600 }}>{usd2(last)}</span>}
          {changePct != null && (
            <span className={isPos ? "pos" : "neg"} style={{ fontSize: 17 }}>
              {pctSign(changePct)} ({rangeOpt.label})
            </span>
          )}
        </div>
        <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
          {RANGES.map((r) => (
            <button key={r.key} className={range === r.key ? "" : "ghost"}
              style={{ padding: "6px 16px", fontSize: 13 }}
              onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
        </div>
      </div>

      {/* Chart */}
      <div className="card" style={{ padding: "20px 16px", marginBottom: 20 }}>
        {data.length === 0 ? (
          <div className="muted" style={{ textAlign: "center", padding: "80px 0", fontSize: 14 }}>
            No price history in this window — run the engine to fetch data.
          </div>
        ) : (
          <>
            <ResponsiveContainer width="100%" height={chartHeight}>
              <LineChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--line)" />
                <XAxis dataKey="as_of" tick={{ fill: "var(--muted)", fontSize: isMobile ? 11 : 12 }}
                  tickFormatter={fmtTick} interval={xInterval} />
                <YAxis domain={["auto", "auto"]} tick={{ fill: "var(--muted)", fontSize: isMobile ? 11 : 12 }}
                  tickFormatter={(v) => `$${v.toFixed(0)}`} width={isMobile ? 50 : 62} />
                {holding?.avg_basis && (
                  <ReferenceLine y={holding.avg_basis} stroke="var(--amber)"
                    strokeDasharray="5 3" strokeWidth={1.5}
                    label={{ value: "avg cost", fill: "var(--amber)", fontSize: 11, position: "insideTopRight" }} />
                )}
                <Tooltip
                  contentStyle={{ background: "var(--panel2)", border: "1px solid var(--line)", borderRadius: 10, fontSize: 13 }}
                  labelStyle={{ color: "var(--muted)", marginBottom: 4 }}
                  labelFormatter={fmtTick}
                  formatter={(v) => [usd2(v), "Price"]} />
                <Line type="monotone" dataKey="price" stroke={lineColor}
                  strokeWidth={2.5} dot={false} activeDot={{ r: 5, fill: lineColor }} />
              </LineChart>
            </ResponsiveContainer>
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--muted)", textAlign: "right" }}>
              {data.length} trading day{data.length !== 1 ? "s" : ""}
            </div>
          </>
        )}
      </div>

      {/* Position details — only shown when this ticker is held */}
      {holding && (
        <div style={{ display: "grid", gridTemplateColumns: isMobile ? "repeat(2, 1fr)" : "repeat(auto-fit, minmax(180px, 1fr))", gap: isMobile ? 10 : 16 }}>
          <DetailRow label="Avg cost"        value={usd2(holding.avg_basis)} mobile={isMobile} />
          <DetailRow label="Current price"   value={holding.price ? usd2(holding.price) : "—"} mobile={isMobile} />
          <DetailRow label="Invested"        value={usd(invested)} mobile={isMobile} />
          <DetailRow label="Current value"   value={holding.value ? usd(holding.value) : "—"} mobile={isMobile} />
          <DetailRow label="Gain $"
            value={gainAbs != null ? usd(gainAbs) : "—"}
            klass={cls(holding.gain_pct)} mobile={isMobile} />
          <DetailRow label="Gain %"
            value={pctSign(holding.gain_pct)}
            klass={cls(holding.gain_pct)} mobile={isMobile} />
          <DetailRow label="Day $"
            value={holding.day_change_abs != null ? usd2(holding.day_change_abs) : "—"}
            klass={cls(holding.day_change_pct)} mobile={isMobile} />
          <DetailRow label="Day %"
            value={pctSign(holding.day_change_pct)}
            klass={cls(holding.day_change_pct)} mobile={isMobile} />
          <DetailRow label="Weight"          value={pct(holding.weight)} mobile={isMobile} />
          <DetailRow label="52-week High"    value={high52w ? usd2(high52w) : "—"} mobile={isMobile} />
        </div>
      )}
    </div>
  );
}
