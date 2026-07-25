import { useEffect, useState } from "react";
import { api } from "../api.js";
import { PARAM_INFO } from "../paramInfo.js";

function ParamRow({ p, onSave }) {
  const isJson = typeof p.value === "object";
  const [val, setVal] = useState(isJson ? JSON.stringify(p.value) : String(p.value));
  const locked = p.status === "LOCKED";
  const info = PARAM_INFO[p.key] || {};
  const save = () => {
    let parsed;
    try { parsed = isJson || val.trim().startsWith("[") || val.trim().startsWith("{") ? JSON.parse(val) : Number.isNaN(Number(val)) ? val : Number(val); }
    catch { parsed = val; }
    onSave(p.key, parsed);
  };
  return (
    <tr>
      <td style={{ maxWidth: 340 }}>
        <div className="row" style={{ gap: 6 }}>
          {info.example && (
            <span className="info" tabIndex={0} role="button" aria-label={`Example for ${p.key}`}>
              <svg width="15" height="15" viewBox="0 0 20 20" aria-hidden="true">
                <circle cx="10" cy="10" r="9" fill="none" stroke="currentColor" strokeWidth="1.6" />
                <circle cx="10" cy="6" r="1.1" fill="currentColor" />
                <rect x="9.1" y="8.5" width="1.8" height="6" rx="0.9" fill="currentColor" />
              </svg>
              <span className="tip"><strong>Example</strong><br />{info.example}</span>
            </span>
          )}
          <code>{p.key}</code>
        </div>
        {info.desc && <div className="keydesc">{info.desc}</div>}
      </td>
      <td><span className={`pill ${locked ? "warn" : ""}`}>{p.status}</span></td>
      <td><input value={val} disabled={locked} onChange={(e) => setVal(e.target.value)} style={{ width: "100%" }} /></td>
      <td><button className="ghost" disabled={locked} onClick={save}>Save</button></td>
    </tr>
  );
}

export default function Config() {
  const [params, setParams] = useState([]);
  const [flags, setFlags] = useState({});
  const [macro, setMacro] = useState({ sp_fwd_pe: "", sp_10yr_avg_fwd_pe: "" });
  const [universe, setUniverse] = useState([]);
  const [u, setU] = useState({ ticker: "", tier: "T1", asset_class: "STOCK", is_avoid: false, subscribed: true, notify: true });
  const [msg, setMsg] = useState("");

  const load = () => {
    api.params().then(setParams);
    api.flags().then(setFlags);
    api.macro().then((m) => setMacro({ sp_fwd_pe: m.sp_fwd_pe ?? "", sp_10yr_avg_fwd_pe: m.sp_10yr_avg_fwd_pe ?? "" }));
    api.universe().then(setUniverse);
  };
  useEffect(() => { load(); }, []);

  const saveParam = (k, v) => api.setParam(k, v).then(() => { setMsg(`Saved ${k}`); load(); }).catch((e) => setMsg(String(e)));
  const saveMacro = () => api.setMacro({
    sp_fwd_pe: macro.sp_fwd_pe === "" ? null : parseFloat(macro.sp_fwd_pe),
    sp_10yr_avg_fwd_pe: macro.sp_10yr_avg_fwd_pe === "" ? null : parseFloat(macro.sp_10yr_avg_fwd_pe),
  }).then(() => setMsg("Macro saved"));

  return (
    <>
      <h1>Configuration</h1>
      {msg && <div className="banner">{msg}</div>}

      <h2>Universe — subscriptions, tiers & avoid-list</h2>
      <div className="card">
        <div className="row">
          <input placeholder="Ticker" value={u.ticker} onChange={(e) => setU({ ...u, ticker: e.target.value.toUpperCase() })} style={{ width: 90 }} />
          <select value={u.tier} onChange={(e) => setU({ ...u, tier: e.target.value })}>
            <option value="T1">T1 (stock)</option>
            <option value="T2">T2 (stock)</option>
            <option value="ETF">ETF core — primary (T1)</option>
            <option value="ETFC">ETF core — secondary</option>
            <option value="ETF2">ETF satellite (T2)</option>
          </select>
          <select value={u.asset_class} onChange={(e) => setU({ ...u, asset_class: e.target.value })}>
            <option>STOCK</option><option>ETF</option>
          </select>
          <label className="row"><input type="checkbox" checked={u.is_avoid} onChange={(e) => setU({ ...u, is_avoid: e.target.checked })} style={{ width: "auto" }} /> avoid</label>
          <button onClick={() => api.upsertUniverse(u).then(() => { setU({ ...u, ticker: "" }); load(); })} disabled={!u.ticker}>Add / update</button>
        </div>
        <table style={{ marginTop: 12 }}>
          <thead><tr><th>Ticker</th><th>Tier</th><th>Class</th><th>Subscribed</th><th>Avoid</th><th></th></tr></thead>
          <tbody>
            {universe.map((row) => (
              <tr key={row.ticker}>
                <td><strong>{row.ticker}</strong></td><td>{row.tier}</td><td>{row.asset_class}</td>
                <td>{row.subscribed ? "yes" : "no"}</td><td>{row.is_avoid ? "⛔" : "—"}</td>
                <td><button className="danger" onClick={() => api.deleteUniverse(row.ticker).then(load)}>Delete</button></td>
              </tr>
            ))}
            {universe.length === 0 && <tr><td colSpan={6} className="muted">No tickers yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <h2>Market macro (manual monthly entry)</h2>
      <div className="card row">
        <label>S&P fwd P/E<br /><input value={macro.sp_fwd_pe} onChange={(e) => setMacro({ ...macro, sp_fwd_pe: e.target.value })} /></label>
        <label>S&P 10-yr avg fwd P/E<br /><input value={macro.sp_10yr_avg_fwd_pe} onChange={(e) => setMacro({ ...macro, sp_10yr_avg_fwd_pe: e.target.value })} /></label>
        <button onClick={saveMacro} style={{ alignSelf: "flex-end" }}>Save macro</button>
      </div>

      <h2>Flags</h2>
      <div className="card row">
        <label className="row">
          <input type="checkbox" checked={!!flags.emergency_fund_ok}
            onChange={(e) => api.setFlag("emergency_fund_ok", e.target.checked).then(load)} style={{ width: "auto" }} />
          Emergency fund funded
        </label>
        <label>Idle cash ($)<br />
          <input defaultValue={flags.idle_cash ?? 0} onBlur={(e) => api.setFlag("idle_cash", parseFloat(e.target.value) || 0).then(load)} />
        </label>
      </div>

      <h2>Engine parameters</h2>
      <p className="muted" style={{ marginTop: 0 }}>LOCKED values are ruleset non-negotiables and can't be edited here.</p>
      <div className="card">
        <table>
          <thead><tr><th>Key</th><th>Status</th><th>Value</th><th></th></tr></thead>
          <tbody>
            {params.map((p) => <ParamRow key={p.key} p={p} onSave={saveParam} />)}
          </tbody>
        </table>
      </div>
    </>
  );
}
