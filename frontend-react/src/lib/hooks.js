import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api } from "./api.js";

/** Small localStorage wrapper; never throws (private mode, blocked storage). */
export function store(ns) {
  return {
    get(k, d) {
      try {
        const v = localStorage.getItem(`${ns}:${k}`);
        return v === null ? d : JSON.parse(v);
      } catch (e) {
        return d;
      }
    },
    set(k, v) {
      try {
        localStorage.setItem(`${ns}:${k}`, JSON.stringify(v));
      } catch (e) {
        /* storage unavailable */
      }
    },
  };
}

/** A value kept in localStorage under `pie-react:<key>`. */
export function useStored(key, initial) {
  const s = store("pie-react");
  const [value, setValue] = useState(() => s.get(key, initial));
  const set = useCallback(
    v => {
      setValue(prev => {
        const next = typeof v === "function" ? v(prev) : v;
        s.set(key, next);
        return next;
      });
    },
    [key] // eslint-disable-line react-hooks/exhaustive-deps
  );
  return [value, set];
}

/**
 * Load JSON from the usage API: const {data, error, loading, reload} = useApi("api/modules").
 * When the backend cannot be reached (not started yet, restarting) it retries on its own
 * every few seconds, so a page opened too early fills in once the backend is up.
 * Pass null as path to load nothing.
 */
export function useApi(path, { retry = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: !!path });
  const [nonce, setNonce] = useState(0);
  const attempts = useRef(0);

  useEffect(() => {
    if (!path) {
      setState({ data: null, error: null, loading: false });
      return undefined;
    }
    const ctrl = new AbortController();
    let timer;
    setState(s => ({ ...s, loading: true }));
    api(path, { signal: ctrl.signal })
      .then(data => {
        attempts.current = 0;
        setState({ data, error: null, loading: false });
      })
      .catch(error => {
        if (error.name === "AbortError") return;
        setState(s => ({ data: s.data, error, loading: false }));
        // Retry when the server was unreachable or briefly failing (5xx), not for 4xx.
        if (retry && (error.status === 0 || error.status >= 500)) {
          const wait = Math.min(3000 * 2 ** attempts.current, 30000);
          attempts.current += 1;
          timer = setTimeout(() => setNonce(n => n + 1), wait);
        }
      });
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [path, nonce, retry]);

  const reload = useCallback(() => {
    attempts.current = 0;
    setNonce(n => n + 1);
  }, []);
  return { ...state, reload };
}

// Changes to the address made in the same moment (e.g. modules and audience when a chat opens)
// build on each other instead of the last one overwriting the first.
let pendingParams = null;

/** const patch = usePatchParams(); patch(p => p.set("screen", id)); replaces the history entry. */
export function usePatchParams() {
  const [params, setParams] = useSearchParams();
  return useCallback(
    fn => {
      const p = new URLSearchParams(pendingParams || params);
      fn(p);
      pendingParams = p;
      queueMicrotask(() => {
        pendingParams = null;
      });
      setParams(p, { replace: true });
    },
    [params, setParams]
  );
}

/** Parse ?modules=a,b (or the older ?module=a); null when absent. */
export function modulesFromParams(params) {
  const v = params.has("modules") ? params.get("modules") : params.get("module");
  return v == null ? null : [...new Set(v.split(",").map(s => s.trim().toLowerCase()).filter(Boolean))];
}

/**
 * The chosen modules, kept in the page address (?modules=ess,inventory) so a copied link opens
 * the page with the same choice, and remembered per page for the next visit.
 * An empty list means "all modules".
 */
export function useModuleSelection(storeKey) {
  const [params] = useSearchParams();
  const patch = usePatchParams();
  const [saved, setSaved] = useStored(`modules:${storeKey}`, []);
  const fromUrl = modulesFromParams(params);
  const selected = fromUrl || saved;

  const setSelected = useCallback(
    next => {
      setSaved(next);
      patch(p => {
        p.delete("module");
        if (next.length) p.set("modules", next.join(","));
        else p.delete("modules");
      });
    },
    [patch, setSaved]
  );
  return [selected, setSelected];
}

/** Run `fn` at most once per `ms` while the value keeps changing (search boxes). */
export function useDebounced(value, ms = 300) {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** Copy text to the clipboard; returns true on success. */
export async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (e) {
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch (_) {
      /* ignore */
    }
    ta.remove();
    return ok;
  }
}

/** Download text as a file (CSV, JSON, Markdown). */
export function downloadText(filename, text, type = "text/plain") {
  const blob = new Blob([text], { type: `${type};charset=utf-8` });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(() => {
    URL.revokeObjectURL(a.href);
    a.remove();
  }, 0);
}
