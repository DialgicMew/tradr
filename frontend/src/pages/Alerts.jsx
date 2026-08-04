import { useEffect, useState } from "react";
import { api } from "../api.js";
import { costLine, describe } from "../describe.js";
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
  const cost = costLine(sig);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>{d.headline}</h3>
        <div className="muted" style={{ marginBottom: cost ? 4 : 14 }}>{d.why}</div>
        {cost && (
          <div className="rec-cost muted" style={{ marginBottom: 14 }} title={cost.detail}>
            {cost.text}
          </div>
        )}

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
  buy: "Buy", trim: "Take profit",
  core_build: "Build index core", cash_floor: "Idle cash", replace: "At holdings cap",
};

// "Opportunistic" ranking: how unusual the move is for THIS ticker (opportunity_score,
// set by the engine as move-size ÷ the ticker's own trailing volatility), decayed by age
// so a big alert from days ago eventually gives way to a smaller, fresher one.
const FRESHNESS_HALF_LIFE_HOURS = 36;

function opportunityRank(sig) {
  const score = sig.metrics?.opportunity_score ?? 0;
  const ageHours = sig.created_at ? (Date.now() - new Date(sig.created_at).getTime()) / 36e5 : 0;
  const freshness = Math.pow(0.5, Math.max(0, ageHours) / FRESHNESS_HALF_LIFE_HOURS);
  return score * freshness;
}
const byOpportunity = (a, b) => opportunityRank(b) - opportunityRank(a);

function Rec({ sig, onApprove, onDecline }) {
  const d = describe(sig);
  const cost = costLine(sig);
  return (
    <div className={`card signal ${sig.action}`} style={{ marginBottom: 12 }}>
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start" }}>
        <div className="rec-headline">{d.headline}</div>
        <span className="pill">{RULE_LABEL[sig.rule] || sig.rule}</span>
      </div>
      {d.sub && <div className="rec-sub">{d.sub}</div>}
      <div className="rec-why"><span className="why-label">Why</span>{d.why}</div>
      {cost && <div className="rec-cost muted" title={cost.detail}>{cost.text}</div>}
      {sig.warnings?.map((w, i) => <div key={i} className="warn">⚠️ {w}</div>)}
      <div className="row" style={{ marginTop: 14 }}>
        <button onClick={() => onApprove(sig)}>Approve</button>
        <button className="ghost" onClick={() => onDecline(sig)}>Decline</button>
      </div>
    </div>
  );
}

function RecGroup({ title, subtitle, sigs, onApprove, onDecline }) {
  if (sigs.length === 0) return null;
  return (
    <div style={{ marginBottom: 24 }}>
      <h2 style={{ marginBottom: 2 }}>{title}</h2>
      {subtitle && <p className="muted" style={{ marginTop: 0 }}>{subtitle}</p>}
      {[...sigs].sort(byOpportunity).map((s) => (
        <Rec key={s.id} sig={s} onApprove={onApprove} onDecline={onDecline} />
      ))}
    </div>
  );
}

export default function Alerts() {
  const [signals, setSignals] = useState([]);
  const [heldTickers, setHeldTickers] = useState(new Set());
  const [active, setActive] = useState(null);

  const load = () => {
    api.signals("PENDING").then(setSignals);
    api.holdings().then((hs) => setHeldTickers(new Set(hs.filter((h) => h.shares > 0).map((h) => h.ticker))));
  };
  useEffect(() => { load(); }, []);

  const decline = async (sig) => { await api.skipSignal(sig.id); load(); };
  const done = () => { setActive(null); load(); };

  const held = signals.filter((s) => heldTickers.has(s.ticker));
  const watchlist = signals.filter((s) => !heldTickers.has(s.ticker));

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
        <>
          <RecGroup title="Holdings" subtitle="Stocks you currently own"
                    sigs={held} onApprove={setActive} onDecline={decline} />
          <RecGroup title="Watchlist" subtitle="Stocks you don't own yet"
                    sigs={watchlist} onApprove={setActive} onDecline={decline} />
        </>
      )}

      {active && <ApproveModal sig={active} onClose={() => setActive(null)} onDone={done} />}
    </>
  );
}
