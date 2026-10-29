// Activity-log API (ROOT + "logs/api/"), protected by an optional token sent as X-PIE-Log-Token.
import { useCallback, useEffect, useState } from "react";
import { ApiError, ROOT, request } from "../../lib/api.js";

export const LOGS = ROOT + "logs/api/";
const TOKEN_KEY = "pieLogToken"; // same key as the old logs.html

export function readToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || "";
  } catch (e) {
    return "";
  }
}
export function saveToken(token) {
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch (e) {
    /* storage unavailable */
  }
}

/** GET or POST a log endpoint. A 401 becomes a plain-English message about the token. */
export async function logApi(path, token, { method = "GET", signal } = {}) {
  try {
    return await request(LOGS + path, { method, signal, headers: token ? { "X-PIE-Log-Token": token } : {} });
  } catch (e) {
    if (e.status === 401 || e.status === 403) {
      const err = new ApiError("Wrong or missing log token. Enter it under “Log token” and press Save.", e.status);
      err.auth = true;
      throw err;
    }
    throw e;
  }
}

/**
 * Load a log endpoint: {data, error, loading, reload}. Reloads when `path`, `token` or `tick`
 * (the page's refresh counter) change. Old data stays visible while a refresh is running.
 */
export function useLogs(path, token, tick) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!path) return undefined;
    const ctrl = new AbortController();
    setState(s => ({ ...s, loading: true }));
    logApi(path, token, { signal: ctrl.signal })
      .then(data => setState({ data, error: null, loading: false }))
      .catch(error => {
        if (error.name !== "AbortError") setState(s => ({ data: s.data, error, loading: false }));
      });
    return () => ctrl.abort();
  }, [path, token, tick, nonce]);
  const reload = useCallback(() => setNonce(n => n + 1), []);
  return { ...state, reload };
}

/* ---------- display helpers ---------- */
export const ACTIVITIES = [
  "module_discovery", "screen_discovery", "field_discovery", "field_knowledge", "knowledge_extraction",
  "evidence_mapping", "qdrant_ingestion", "qdrant_status", "knowledge_inventory", "retrieval", "export", "other",
];
export const activityLabel = a => String(a || "").replace(/_/g, " ");
export const dur = ms =>
  ms == null ? "—" : ms < 1000 ? `${ms} ms` : ms < 120000 ? `${(ms / 1000).toFixed(1)} s` : `${(ms / 60000).toFixed(1)} min`;
export const stamp = t => {
  if (!t) return "—";
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
};

const TONES = {
  SUCCESS: "ok", GREEN: "ok", grounded: "ok",
  WARNING: "warn", YELLOW: "warn", partial: "warn", WARN: "warn",
  FAILED: "bad", RED: "bad", UNREACHABLE: "bad", ERROR: "bad", unverified: "bad", not_found: "bad",
  RUNNING: "accent",
};
export const toneOf = s => TONES[s];
