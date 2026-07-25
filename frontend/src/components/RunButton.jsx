import { useState } from "react";
import { api } from "../api.js";

// "Run engine now" with pressed + loading states. Disabled & shows a spinner
// while the request is in flight; calls onDone(result) when it returns.
export default function RunButton({ onDone }) {
  const [running, setRunning] = useState(false);

  const run = async () => {
    if (running) return;
    setRunning(true);
    try {
      const res = await api.runNow(true);
      onDone?.(res);
    } catch (e) {
      console.error("run failed", e);
    } finally {
      setRunning(false);
    }
  };

  return (
    <button className={`ghost run-btn${running ? " is-running" : ""}`} onClick={run} disabled={running}>
      {running && <span className="spinner" aria-hidden="true" />}
      {running ? "Running…" : "Run engine now"}
    </button>
  );
}
