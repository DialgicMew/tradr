// Thin API client for the tradr backend (proxied to 127.0.0.1:8000 in dev).
const BASE = "/api";

async function req(path, opts = {}) {
  const res = await fetch(BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`${res.status}: ${body}`);
  }
  return res.status === 204 ? null : res.json();
}

export const api = {
  setupStatus: () => req("/setup/status"),
  setup: (b) => req("/setup", { method: "POST", body: JSON.stringify(b) }),

  holdings: () => req("/holdings"),
  summary: () => req("/portfolio/summary"),
  info: (t) => req(`/info/${t}`),
  history: (t) => req(`/history/${t}`),

  trades: () => req("/trades"),
  addTrade: (b) => req("/trades", { method: "POST", body: JSON.stringify(b) }),
  voidTrade: (id) => req(`/trades/${id}/void`, { method: "POST" }),

  reserve: () => req("/reserve"),
  addReserve: (b) => req("/reserve", { method: "POST", body: JSON.stringify(b) }),

  params: () => req("/params"),
  setParam: (k, value) => req(`/params/${k}`, { method: "PUT", body: JSON.stringify({ value }) }),
  flags: () => req("/flags"),
  setFlag: (k, value) => req(`/flags/${k}`, { method: "PUT", body: JSON.stringify({ value }) }),
  macro: () => req("/macro"),
  setMacro: (b) => req("/macro", { method: "POST", body: JSON.stringify(b) }),

  universe: () => req("/universe"),
  upsertUniverse: (b) => req("/universe", { method: "POST", body: JSON.stringify(b) }),
  deleteUniverse: (t) => req(`/universe/${t}`, { method: "DELETE" }),

  signals: (state) => req(`/signals${state ? `?state=${state}` : ""}`),
  confirmSignal: (id, b) => req(`/signals/${id}/confirm`, { method: "POST", body: JSON.stringify(b) }),
  skipSignal: (id) => req(`/signals/${id}/skip`, { method: "POST" }),

  runNow: (force) => req(`/run?force=${force ? "true" : "false"}`, { method: "POST" }),
};
