import { useState } from "react";
import { api } from "../api.js";

// First-run wizard — collects the Appendix-A.10 blanks the repo intentionally ships
// empty (base_unit, tier weights, emergency flag). Engine stays fail-closed until set.
export default function Setup({ onDone }) {
  const [baseUnit, setBaseUnit] = useState("");
  const [t1, setT1] = useState("2.0");
  const [t2, setT2] = useState("1.0");
  const [etf, setEtf] = useState("18.0");
  const [emergency, setEmergency] = useState(false);
  const [err, setErr] = useState("");

  const submit = async () => {
    setErr("");
    try {
      await api.setup({
        base_unit: parseFloat(baseUnit),
        tier_weight: { T1: parseFloat(t1), T2: parseFloat(t2), ETF: parseFloat(etf) },
        emergency_fund_ok: emergency,
      });
      onDone();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <div className="container" style={{ maxWidth: 560 }}>
      <h1>Welcome to tradr</h1>
      <p className="muted">
        Advisory only — this app never trades. Set the values only you can decide.
        Everything else uses documented defaults you can tune later.
      </p>
      <div className="card grid" style={{ gap: 14 }}>
        <label>
          <div className="muted">BASE_UNIT — your painless monthly unit ($)</div>
          <input type="number" value={baseUnit} onChange={(e) => setBaseUnit(e.target.value)}
            placeholder="e.g. 1000" style={{ width: "100%" }} />
        </label>
        <div className="row">
          <label>T1 weight<br /><input type="number" value={t1} onChange={(e) => setT1(e.target.value)} /></label>
          <label>T2 weight<br /><input type="number" value={t2} onChange={(e) => setT2(e.target.value)} /></label>
          <label>ETF weight<br /><input type="number" value={etf} onChange={(e) => setEtf(e.target.value)} /></label>
        </div>
        <p className="muted" style={{ fontSize: 12, margin: 0 }}>
          ETF weight is a solved number (Appendix A.2): (p/(1−p))×Σ individual weights. Recompute when your holding count changes.
        </p>
        <label className="row">
          <input type="checkbox" checked={emergency} onChange={(e) => setEmergency(e.target.checked)} style={{ width: "auto" }} />
          Emergency fund is funded (6–12 months). If unchecked, buys carry a warning.
        </label>
        {err && <div className="neg">{err}</div>}
        <button onClick={submit} disabled={!baseUnit}>Finish setup</button>
      </div>
    </div>
  );
}
