// Talking to the PIE backend. The address comes from public/config.js (window.PIE_CONFIG.apiBase),
// e.g. "/usage/" (same server) or "http://192.168.0.90:8000/usage/".

const raw = (window.__PIE_CHAT__ && window.__PIE_CHAT__.apiBase) || (window.PIE_CONFIG && window.PIE_CONFIG.apiBase) || "/usage/";

/** Base for the usage API: API + "api/chat" -> /usage/api/chat */
export const API = raw.endsWith("/") ? raw : raw + "/";
/** Backend root (without /usage/), for the activity-log API at ROOT + "logs/api/". */
export const ROOT = API.replace(/usage\/?$/, "");

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
  /** True when the backend could not be reached at all (not running, network down). */
  get offline() {
    return this.status === 0;
  }
}

function detailText(data, status) {
  const d = data && data.detail;
  if (typeof d === "string") return d;
  // FastAPI validation errors are a list of {loc, msg}.
  if (Array.isArray(d)) return d.map(x => `${(x.loc || []).slice(1).join(" ")}: ${x.msg}`).join("; ");
  return `Request failed (${status})`;
}

/** fetch + JSON with readable errors. `body` is sent as JSON. */
export async function request(url, { method = "GET", body, headers, signal } = {}) {
  let r;
  try {
    r = await fetch(url, {
      method,
      signal,
      cache: "no-store",
      headers: { ...(body !== undefined ? { "Content-Type": "application/json" } : {}), ...(headers || {}) },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError(`The PIE server (${API}) did not respond. Check that the backend is running.`, 0);
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(detailText(data, r.status), r.status);
  return data;
}

/** Usage API: api("api/modules"), api("api/chat", {method: "POST", body}). */
export const api = (path, opts) => request(API + path, opts);

/** Query string from an object, skipping empty values: qs({a: 1, b: ""}) -> "a=1" */
export function qs(obj) {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(obj || {})) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  return p.toString();
}
