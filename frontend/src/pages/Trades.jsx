import { useEffect, useState } from "react";
import { api } from "../api.js";
import { usd, usd2 } from "../fmt.js";

export default function Trades() {
  const [trades, setTrades] = useState([]);
  const [reserve, setReserve] = useState({ balance: 0, entries: [] });
  // brokerage: "tiered" | "fixed" | "actual" | "none" — how this fill's fee is decided.
  // "actual" is the honest choice for an old trade: type the number off the statement.
  const [form, setForm] = useState({ ticker: "", side: "buy", mode: "shares", qty: "", amount: "",
                                     price: "", trade_date: "", brokerage: "tiered", fee: "",
                                     fundFromReserve: false });
  const [res, setRes] = useState({ amount: "", note: "" });
  const [err, setErr] = useState("");

  const load = () => { api.trades().then(setTrades); api.reserve().then(setReserve); };
  useEffect(() => { load(); }, []);

  // Resolve the share quantity from whichever entry mode is active.
  const price = parseFloat(form.price);
  const computedQty = form.mode === "amount"
    ? (form.amount && price ? parseFloat(form.amount) / price : NaN)
    : parseFloat(form.qty);
  const qtyValid = Number.isFinite(computedQty) && computedQty > 0;

  const addTrade = async () => {
    setErr("");
    if (!qtyValid) { setErr("Enter a valid quantity/amount and price."); return; }
    try {
      await api.addTrade({
        ticker: form.ticker, side: form.side,
        qty: computedQty, price,
        trade_date: form.trade_date || null,
        // An explicit fee wins server-side; otherwise the plan is estimated from.
        ...(form.brokerage === "actual"
          ? { fee: parseFloat(form.fee) || 0 }
          : { commission_plan: form.brokerage }),
        fund_from_reserve: form.fundFromReserve,
      });
      setForm({ ...form, ticker: "", qty: "", amount: "", price: "", fee: "", fundFromReserve: false });
      load();
    } catch (e) { setErr(String(e)); }
  };

  const addReserve = async () => {
    await api.addReserve({ amount: parseFloat(res.amount), note: res.note, kind: "external" });
    setRes({ amount: "", note: "" });
    load();
  };

  return (
    <>
      <h1>Trades</h1>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Log a buy / sell</h2>
        <div className="row">
          <input placeholder="Ticker" value={form.ticker}
            onChange={(e) => setForm({ ...form, ticker: e.target.value.toUpperCase() })} style={{ width: 100 }} />
          <select value={form.side} onChange={(e) => setForm({ ...form, side: e.target.value })}>
            <option value="buy">Buy</option><option value="sell">Sell</option>
          </select>
          <select value={form.mode} onChange={(e) => setForm({ ...form, mode: e.target.value })}>
            <option value="shares">by Shares</option>
            <option value="amount">by $ Amount</option>
          </select>
          {form.mode === "shares" ? (
            <input placeholder="Shares (e.g. 1.2334)" value={form.qty}
              onChange={(e) => setForm({ ...form, qty: e.target.value })} style={{ width: 150 }} />
          ) : (
            <input placeholder="Amount $ (e.g. 50)" value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })} style={{ width: 150 }} />
          )}
          <input placeholder="Price / share" value={form.price} onChange={(e) => setForm({ ...form, price: e.target.value })} style={{ width: 110 }} />
          <input type="date" value={form.trade_date} onChange={(e) => setForm({ ...form, trade_date: e.target.value })} />
          <select value={form.brokerage} title="Which IBKR plan this fill was traded under"
            onChange={(e) => setForm({ ...form, brokerage: e.target.value })}>
            <option value="tiered">Tiered (est.)</option>
            <option value="fixed">Fixed (est.)</option>
            <option value="actual">Actual fee…</option>
            <option value="none">No fee</option>
          </select>
          {form.brokerage === "actual" && (
            <input placeholder="Fee $" value={form.fee}
              onChange={(e) => setForm({ ...form, fee: e.target.value })} style={{ width: 90 }} />
          )}
          <button onClick={addTrade} disabled={!form.ticker || !qtyValid}>Add</button>
        </div>
        <label className="row" style={{ marginTop: 10, gap: 6, alignItems: "center", fontSize: 13 }}
          title="Only for a fill you placed yourself outside the Alerts approve flow — a dip-buy
funded from dry powder, or a sale whose proceeds should recycle back into it. This also
settles the alert it stands in for (marks its band FIRED, retires the stale
recommendation) so it won't keep nagging you — it re-arms later the same way a confirmed
one would. Leave unchecked for a regular contribution; nothing here is touched.">
          <input type="checkbox" checked={form.fundFromReserve}
            onChange={(e) => setForm({ ...form, fundFromReserve: e.target.checked })} />
          <span className="muted">
            {form.side === "buy" ? "Fund this buy from reserve (dry powder)" : "Recycle proceeds into reserve"}
          </span>
        </label>
        {qtyValid && (
          <div className="muted" style={{ marginTop: 8 }}>
            {form.mode === "amount"
              ? <>${parseFloat(form.amount).toLocaleString()} ÷ {usd2(price)} ≈ <strong>{computedQty.toFixed(6)} shares</strong></>
              : <><strong>{computedQty} shares</strong> × {usd2(price)} ≈ {usd2(computedQty * price)}</>}
            {form.side === "buy" && (
              <> · brokerage {form.brokerage === "actual" ? "as entered" : form.brokerage === "none" ? "not tracked" : `estimated on ${form.brokerage}`}
                {form.brokerage !== "none" && " and included in your cost basis"}</>
            )}
            {form.fundFromReserve && (
              <> · reserve will {form.side === "buy" ? "decrease" : "increase"} by ~{usd2(computedQty * price)}
                {form.side === "buy" ? " + fee" : " − fee"}</>
            )}
          </div>
        )}
        {err && <div className="neg" style={{ marginTop: 8 }}>{err}</div>}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2 style={{ marginTop: 0 }}>Reserve — balance {usd(reserve.balance)}</h2>
        <div className="row">
          <input placeholder="Amount (+ / −)" value={res.amount} onChange={(e) => setRes({ ...res, amount: e.target.value })} style={{ width: 130 }} />
          <input placeholder="note" value={res.note} onChange={(e) => setRes({ ...res, note: e.target.value })} />
          <button onClick={addReserve} disabled={!res.amount}>Add external money</button>
        </div>
        <table style={{ marginTop: 12 }}>
          <thead><tr><th>Kind</th><th className="num">Amount</th><th>Note</th></tr></thead>
          <tbody>
            {reserve.entries.map((e) => (
              <tr key={e.id}><td><span className="pill">{e.kind}</span></td>
                <td className="num">{usd2(e.amount)}</td><td className="muted">{e.note}</td></tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>History</h2>
      <div className="card">
        <table>
          <thead><tr><th>Date</th><th>Ticker</th><th>Side</th><th className="num">Qty</th>
            <th className="num">Price</th><th className="num">Fee</th><th></th></tr></thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.id} style={{ opacity: t.voided ? 0.4 : 1 }}>
                <td>{t.trade_date}</td><td><strong>{t.ticker}</strong></td>
                <td className={t.side === "buy" ? "pos" : "neg"}>{t.side}</td>
                <td className="num">{t.qty}</td><td className="num">{usd2(t.price)}</td>
                <td className="num muted">{t.fee ? usd2(t.fee) : "—"}</td>
                <td className="num">{t.voided ? <span className="muted">voided</span> :
                  <button className="ghost" onClick={() => api.voidTrade(t.id).then(load)}>Remove</button>}</td>
              </tr>
            ))}
            {trades.length === 0 && <tr><td colSpan={7} className="muted">No trades yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
