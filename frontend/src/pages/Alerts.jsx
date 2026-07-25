import { useEffect, useState } from "react";
import { api } from "../api.js";
import { describe } from "../describe.js";
import { usd, usd2 } from "../fmt.js";
import RunButton from "../components/RunButton.jsx";

// Modal shown when the user taps Approve. First asks whether they actually traded;
// only "Yes" reveals the fill entry and creates the trade. "No" leaves the signal put.
function ApproveModal({ sig, onClose, onDone }) {
  const [step, setStep] = useState("ask"); // ask -> fill
  const [mode, setMode] = useState("shares");
  const [qty, setQty] = useState(sig.size_shares ? String(Math.round(sig.size_shares)) : "");
  const [amount, setAmount] = useState("");
  const [price, setPrice] = useState("");
  const [err, setErr] = useState("");

  const px = parseFloat(price);
  const computedQty = mode === "amount" ? (amount && px ? parseFloat(amount) / px : NaN) : parseFloat(qty);
  const valid = Number.isFinite(computedQty) && computedQty > 0 && px > 0;

  const create = async () => {
    setErr("");
    try {
      await api.confirmSignal(sig.id, { fill_qty: computedQty, fill_price: px });
      onDone();
    } catch (e) { setErr(String(e)); }
  };

  const d = describe(sig);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>{d.headline}</h3>
        <div className="muted" style={{ marginBottom: 14 }}>{d.why}</div>

        {step === "ask" ? (
          <>
            <p>Have you already placed this trade with your broker?</p>
            <div className="row">
              <button onClick={() => setStep("fill")}>Yes — record it</button>
              <button className="ghost" onClick={onClose}>No — not yet</button>
            </div>
            <p className="muted" style={{ fontSize: 12, marginBottom: 0 }}>
              "No" leaves this recommendation here untouched. tradr never trades for you.
            </p>
          </>
        ) : (
          <>
            <p>Enter the fill you actually got:</p>
            <div className="row">
              <select value={mode} onChange={(e) => setMode(e.target.value)}>
                <option value="shares">by Shares</option>
                <option value="amount">by $ Amount</option>
              </select>
              {mode === "shares" ? (
                <input placeholder="Shares" value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 130 }} />
              ) : (
                <input placeholder="Amount $" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: 130 }} />
              )}
              <input placeholder="Price / share" value={price} onChange={(e) => setPrice(e.target.value)} style={{ width: 120 }} />
            </div>
            {valid && (
              <div className="muted" style={{ marginTop: 8 }}>
                → {computedQty.toFixed(6)} sh × {usd2(px)} ≈ {usd2(computedQty * px)}
              </div>
            )}
            {err && <div className="neg" style={{ marginTop: 8 }}>{err}</div>}
            <div className="row" style={{ marginTop: 14 }}>
              <button onClick={create} disabled={!valid}>Create trade</button>
              <button className="ghost" onClick={onClose}>Cancel</button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

const RULE_LABEL = {
  double_down: "Buy the dip", trim: "Take profit", base_dca: "Monthly plan",
  core_build: "Build index core", cash_floor: "Idle cash", replace: "At holdings cap",
};

// Index/core actions surface first, then trims, then dip-buys, then the rest.
const RULE_ORDER = { core_build: 0, cash_floor: 1, base_dca: 2, trim: 3, double_down: 4, replace: 5 };
const byPriority = (a, b) => (RULE_ORDER[a.rule] ?? 9) - (RULE_ORDER[b.rule] ?? 9);

function Rec({ sig, onApprove, onDecline }) {
  const d = describe(sig);
  return (
    <div className={`card signal ${sig.action}`} style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="rec-headline">{d.headline}</div>
        <span className="pill">{RULE_LABEL[sig.rule] || sig.rule}</span>
      </div>
      {d.sub && <div className="rec-sub">{d.sub}</div>}
      <div className="rec-why"><span className="why-label">Why</span>{d.why}</div>
      {sig.warnings?.map((w, i) => <div key={i} className="warn">⚠️ {w}</div>)}
      <div className="row" style={{ marginTop: 14 }}>
        <button onClick={() => onApprove(sig)}>Approve</button>
        <button className="ghost" onClick={() => onDecline(sig)}>Decline</button>
      </div>
    </div>
  );
}

export default function Alerts() {
  const [signals, setSignals] = useState([]);
  const [active, setActive] = useState(null);

  const load = () => api.signals("PENDING").then(setSignals);
  useEffect(() => { load(); }, []);

  const decline = async (sig) => { await api.skipSignal(sig.id); load(); };
  const done = () => { setActive(null); load(); };

  return (
    <>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Recommendations</h1>
        <RunButton onDone={load} />
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        Advisory only. <strong>Approve</strong> records a trade you've already made; <strong>Decline</strong> dismisses it.
      </p>

      {signals.length === 0 ? (
        <div className="card muted">No recommendations right now. Run the engine, or check back after market hours.</div>
      ) : (
        [...signals].sort(byPriority).map((s) => <Rec key={s.id} sig={s} onApprove={setActive} onDecline={decline} />)
      )}

      {active && <ApproveModal sig={active} onClose={() => setActive(null)} onDone={done} />}
    </>
  );
}
