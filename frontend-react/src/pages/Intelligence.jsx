// Department Knowledge: working briefs, product evidence, source screens, field facts and open
// questions for each department, read live from the shared knowledge base (Qdrant).
// Address: /intelligence?module=inventory&department=marketing&kind=brief
import { useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Building2, ClipboardList, Copy, RefreshCw, Search } from "lucide-react";
import { api, qs } from "../lib/api.js";
import { copyText, useApi, useDebounced, usePatchParams } from "../lib/hooks.js";
import { moduleLabel, num } from "../lib/format.js";
import { EmptyState, ErrorState, Loading, PageHeader, Stat, useToast } from "../components/ui.jsx";
import RecordCard from "./intelligence/RecordCard.jsx";
import "./Intelligence.css";

const KINDS = [
  ["brief", "Working briefs"],
  ["claim", "Product evidence"],
  ["screen", "Source screens"],
  ["field", "Field facts"],
  ["gap", "Owner input needed"],
  ["", "All records"],
];
const PAGE = 30;
const deptLabel = k => k.replace(/_/g, " ").replace(/^./, c => c.toUpperCase());

export default function Intelligence() {
  const toast = useToast();
  const [params] = useSearchParams();
  const patch = usePatchParams();
  const module = (params.get("module") || "inventory").toLowerCase();
  const base = `api/intelligence/${encodeURIComponent(module)}/`;

  const summary = useApi(`${base}summary`);
  const s = summary.data;
  const depts = Object.keys((s && s.departments) || {});
  const department = depts.includes(params.get("department")) ? params.get("department") : depts[0] || "";
  const kind = params.has("kind") && KINDS.some(([k]) => k === params.get("kind")) ? params.get("kind") : "brief";

  const [term, setTerm] = useState("");
  const q = useDebounced(term.trim(), 300);
  const [records, setRecords] = useState([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [nonce, setNonce] = useState(0);
  const [context, setContext] = useState(null); // {busy, data, error}
  const ticket = useRef(0);

  const setParam = (key, value) => patch(p => (value === "" && key !== "kind" ? p.delete(key) : p.set(key, value)));

  async function load(offset) {
    const mine = ++ticket.current;
    setLoading(true);
    if (!offset) setError(null);
    try {
      const d = await api(`${base}records?${qs({ department, kind, q, offset, limit: PAGE })}`);
      if (mine !== ticket.current) return;
      setTotal(d.total || 0);
      setRecords(r => (offset ? [...r, ...(d.records || [])] : d.records || []));
      setError(null);
    } catch (e) {
      if (mine === ticket.current) {
        setError(e);
        if (!offset) setRecords([]);
      }
    } finally {
      if (mine === ticket.current) setLoading(false);
    }
  }

  // New filters: start again from the first page and hide the old context.
  useEffect(() => {
    if (!s || !department) return;
    setContext(null);
    load(0);
  }, [s, department, kind, q, nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  async function showContext() {
    setContext({ busy: true });
    try {
      const data = await api(`${base}context?${qs({ department, q, limit: 12, audience: "internal" })}`);
      setContext({ data });
    } catch (e) {
      setContext({ error: e });
    }
  }
  const contextText = context && context.data ? JSON.stringify(context.data, null, 2) : "";

  const header = (
    <PageHeader
      title={s ? `${s.product || "GoodBooks ERP"}: ${moduleLabel(module)} department knowledge` : "Department Knowledge"}
      description="Briefs, product evidence and open questions for each department, read live from the shared knowledge base (Qdrant)."
    />
  );

  if (!s) {
    return (
      <main className="page intel-page">
        {header}
        {summary.error ? (
          <ErrorState
            error={summary.error}
            onRetry={summary.reload}
            title={summary.error.status === 404 ? `No department knowledge for ${moduleLabel(module)} yet` : "Could not load department knowledge"}
          />
        ) : (
          <Loading>Connecting to the shared knowledge base…</Loading>
        )}
      </main>
    );
  }

  const dept = (s.departments || {})[department] || {};
  const stats = s.statistics || {};
  return (
    <main className="page intel-page">
      {header}
      <p className="note">
        Source evidence and working drafts need product review before publication. Tip: choose Marketing and search “brochure” to find the{" "}
        {moduleLabel(module)} brochure draft.
      </p>

      <div className="statusline intel-status" role="status">
        <span>
          <span className="dot ok" />
          Connected to Qdrant · <b>{num(s.record_count)}</b> shared records
        </span>
        {s.policy && <span>{s.policy}</span>}
      </div>

      <div className="stats">
        <Stat value={num((stats.catalog || {}).features)} label="source features" />
        <Stat value={num(stats.field_dictionaries)} label="field dictionaries" />
        <Stat value={num(s.record_count)} label="Qdrant records" />
        <Stat value={num((stats.statuses || {}).APPROVED || 0)} label="human-approved records" tone={(stats.statuses || {}).APPROVED ? "ok" : ""} />
      </div>

      <div className="intel-controls">
        <label className="field-label">
          Department
          <select className="input" value={department} onChange={e => setParam("department", e.target.value)}>
            {depts.map(k => (
              <option key={k} value={k}>
                {deptLabel(k)}
              </option>
            ))}
          </select>
        </label>
        <label className="field-label">
          Knowledge type
          <select className="input" value={kind} onChange={e => setParam("kind", e.target.value)}>
            {KINDS.map(([k, label]) => (
              <option key={k || "all"} value={k}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label className="field-label">
          Search
          <span className="search">
            <Search size={15} aria-hidden="true" />
            <input
              className="input"
              type="search"
              placeholder="brochure, item, migration, validation…"
              value={term}
              onChange={e => setTerm(e.target.value)}
            />
          </span>
        </label>
      </div>

      {(dept.purpose || dept.instruction) && (
        <p className="intel-purpose">
          <Building2 size={15} aria-hidden="true" />
          <span>
            <b>{deptLabel(department)}:</b> {dept.purpose}
            {dept.purpose && dept.instruction ? ". " : ""}
            {dept.instruction}
          </span>
        </p>
      )}

      <div className="toolbar intel-actions">
        <button className="btn" type="button" onClick={() => setNonce(n => n + 1)} disabled={loading}>
          <RefreshCw size={14} /> Refresh from Qdrant
        </button>
        <button className="btn" type="button" onClick={showContext} disabled={context && context.busy}>
          <ClipboardList size={14} /> {context && context.busy ? "Building context…" : "Show context for this request"}
        </button>
        <span className="muted" aria-live="polite">
          {loading && !records.length ? "Loading from Qdrant…" : error ? "" : `${num(total)} matching record${total === 1 ? "" : "s"}`}
        </span>
      </div>

      {context && context.error && <ErrorState error={context.error} onRetry={showContext} title="Could not build the context" />}
      {context && context.data && (
        <details className="card intel-context" open>
          <summary>Department task context</summary>
          <p className="muted small">
            What PIE would hand to a writer or an AI for this department and search: instructions, missing information and the matching records.
          </p>
          <button
            className="btn small"
            type="button"
            onClick={async () => toast((await copyText(contextText)) ? "Context copied" : "Copy failed")}
          >
            <Copy size={14} /> Copy
          </button>
          <pre>{contextText}</pre>
        </details>
      )}

      {error && <ErrorState error={error} onRetry={() => setNonce(n => n + 1)} title="Could not load the records" />}
      {!error && loading && !records.length && <Loading>Loading from Qdrant…</Loading>}
      {!error && !loading && !records.length && (
        <EmptyState title="No matching knowledge">Change the filters or ask the content owner.</EmptyState>
      )}

      <div className="intel-results">
        {records.map(r => (
          <RecordCard key={r.id} record={r} />
        ))}
      </div>

      {records.length > 0 && records.length < total && (
        <p className="intel-more">
          <button className="btn" type="button" onClick={() => load(records.length)} disabled={loading}>
            {loading ? "Loading…" : `Show ${Math.min(PAGE, total - records.length)} more`}
          </button>
          <span className="muted small">
            {num(records.length)} of {num(total)} shown
          </span>
        </p>
      )}

      <p className="muted small intel-foot">
        Knowledge is retrieved from Qdrant. This page holds no embedded knowledge, browser storage, local fallback or download cache.
      </p>
    </main>
  );
}
