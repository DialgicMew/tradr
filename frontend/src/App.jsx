import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api.js";
import Home from "./pages/Home.jsx";
import Trades from "./pages/Trades.jsx";
import Config from "./pages/Config.jsx";
import Alerts from "./pages/Alerts.jsx";
import Setup from "./pages/Setup.jsx";

export default function App() {
  const [configured, setConfigured] = useState(null);
  const [pending, setPending] = useState(0);

  const refresh = () => api.setupStatus().then((r) => setConfigured(r.configured));
  useEffect(() => { refresh(); }, []);
  useEffect(() => {
    if (!configured) return;
    const poll = () => api.signals("PENDING").then((s) => setPending(s.length)).catch(() => {});
    poll();
    const t = setInterval(poll, 15000);
    return () => clearInterval(t);
  }, [configured]);

  if (configured === null) return <div className="container">Loading…</div>;
  if (!configured) return <Setup onDone={refresh} />;

  const link = ({ isActive }) => (isActive ? "active" : "");
  return (
    <>
      <nav className="nav">
        <span className="brand">tradr</span>
        <NavLink to="/" className={link} end>Home</NavLink>
        <NavLink to="/alerts" className={link}>
          Alerts{pending > 0 && <span className="badge">{pending}</span>}
        </NavLink>
        <NavLink to="/trades" className={link}>Trades</NavLink>
        <NavLink to="/config" className={link}>Config</NavLink>
        <span style={{ flex: 1 }} />
        <span className="muted nav-tag" style={{ fontSize: 12 }}>advisory only · you execute</span>
      </nav>
      <div className="container">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/trades" element={<Trades />} />
          <Route path="/config" element={<Config />} />
        </Routes>
      </div>
    </>
  );
}
