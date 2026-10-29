// The six tabs of the Activity Log. Each gets: hours, token, tick (refresh counter), openRun(id).
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Database, RefreshCw, Search } from "lucide-react";
import { Stat, useToast } from "../../components/ui.jsx";
import { moduleLabel, num } from "../../lib/format.js";
import { qs } from "../../lib/api.js";
import { ACTIVITIES, activityLabel, dur, logApi, stamp, toneOf, useLogs } from "./logsApi.js";
import { Badge, ClickRow, Headline, LogTable, StaleError, TabState } from "./bits.jsx";

/* ---------------- Overview ---------------- */
export function Overview({ hours, token, tick, openRun, showActivity }) {
  const res = useLogs(`summary?hours=${hours}`, token, tick);
  const s = res.data;
  if (!s) return <TabState res={res} what="the summary" />;
  const t = s.totals || {};
  const c = s.chat || {};
  const a = s.api || {};
  const q = s.qdrant;
  return (
    <>
      <StaleError res={res} />
      <div className="stats">
        <Stat value={num(t.runs)} label="runs" />
        <Stat value={num(t.SUCCESS)} label="succeeded" tone="ok" />
        <Stat value={num(t.WARNING)} label="warnings" tone={t.WARNING ? "warn" : ""} />
        <Stat value={num(t.FAILED)} label="failed" tone={t.FAILED ? "bad" : ""} />
        <Stat value={num(t.RUNNING)} label="running now" />
        <Stat value={num(c.questions)} label="chat questions" />
        <Stat value={c.avg_seconds == null ? "—" : `${Number(c.avg_seconds).toFixed(1)} s`} label="average answer time" />
        <Stat
          value={c.questions ? `${Math.round((100 * (c.grounded || 0)) / c.questions)}%` : "—"}
          label="answers fully grounded"
          title="Answers where every statement was backed by knowledge items"
        />
        <Stat value={num(a.requests)} label="API requests" />
        <Stat value={num(a.server_errors)} label="API server errors (5xx)" tone={a.server_errors ? "bad" : ""} />
        <Stat
          value={q ? num(q.points) : "—"}
          label={q ? `Qdrant points (${q.status})` : "Qdrant not checked yet"}
          tone={q ? toneOf(q.status) : ""}
        />
        <Stat
          value={num(s.staleRunning)}
          label="stuck in RUNNING for over 6 h"
          tone={s.staleRunning ? "warn" : ""}
          title="Runs that started but never reported an end, usually because the process was stopped"
        />
      </div>

      <section className="card logs-section">
        <h2>By activity</h2>
        <p className="muted small">Choose an activity to see its runs.</p>
        <LogTable
          head={["Activity", "#Runs", "#Success", "#Warning", "#Failed", "#Running", "#Avg time", "Last run", "Last success", "Last failure"]}
          rows={(s.activities || []).length}
          empty="No activity in this time window."
        >
          {(s.activities || []).map(x => (
            <ClickRow key={x.activity} onOpen={() => showActivity(x.activity)} label={`Show ${activityLabel(x.activity)} runs`}>
              <td className="cap">{activityLabel(x.activity)}</td>
              <td className="num">{num(x.total)}</td>
              <td className="num t-ok">{num(x.SUCCESS)}</td>
              <td className="num t-warn">{num(x.WARNING)}</td>
              <td className="num t-bad">{num(x.FAILED)}</td>
              <td className="num">{num(x.RUNNING)}</td>
              <td className="num">{dur(x.avgMs)}</td>
              <td className="nowrap">{stamp(x.lastRun)}</td>
              <td className="nowrap">{stamp(x.lastSuccess)}</td>
              <td className="nowrap">{stamp(x.lastFailure)}</td>
            </ClickRow>
          ))}
        </LogTable>
      </section>

      <section className="card logs-section">
        <h2>Recent failures</h2>
        <LogTable
          head={["Started", "Activity", "Command", "Module", "Screen", "Error", "Host"]}
          rows={(s.recentFailures || []).length}
          empty="No failures in this time window."
        >
          {(s.recentFailures || []).map(r => (
            <ClickRow key={r.run_id} onOpen={() => openRun(r.run_id)} label={`Open failed run ${r.command}`}>
              <td className="nowrap">{stamp(r.started_at)}</td>
              <td className="cap">{activityLabel(r.activity)}</td>
              <td>
                <code>{r.command}</code>
              </td>
              <td>{r.module || ""}</td>
              <td>{r.screen || ""}</td>
              <td className="wrap t-bad">
                {r.error_type ? `${r.error_type}: ` : ""}
                {r.error_message || ""}
              </td>
              <td>{r.host || ""}</td>
            </ClickRow>
          ))}
        </LogTable>
      </section>
    </>
  );
}

/* ---------------- Runs ---------------- */
export const EMPTY_FILTER = { activity: "", status: "", module: "", q: "", top: false };

export function RunRow({ r, openRun }) {
  return (
    <ClickRow onOpen={() => openRun(r.run_id)} label={`Open run ${r.command}`}>
      <td className="nowrap">{stamp(r.started_at)}</td>
      <td>
        <Badge status={r.status} />
      </td>
      <td className="cap">{activityLabel(r.activity)}</td>
      <td>
        <code>{r.command}</code>
        {r.parent_id && <span className="muted"> (sub-step)</span>}
      </td>
      <td>{r.module || ""}</td>
      <td className="wrap">{r.screen || (r.attrs && r.attrs.question) || ""}</td>
      <td className="num">{dur(r.duration_ms)}</td>
      <td className="wrap small">
        <Headline counts={r.counts} />
      </td>
      <td className="wrap t-bad">{r.error_message || ""}</td>
      <td className="muted nowrap">
        {r.source}
        {r.host ? ` @ ${r.host}` : ""}
      </td>
    </ClickRow>
  );
}

export function Runs({ hours, token, tick, openRun, filter, setFilter }) {
  const [draft, setDraft] = useState(filter);
  const [rows, setRows] = useState(null);
  const [total, setTotal] = useState(0);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setDraft(filter), [filter]);

  const query = offset => {
    const f = filter;
    return `runs?${qs({ limit: 100, offset, hours, activity: f.activity, status: f.status, module: f.module.trim(), q: f.q.trim(), top: f.top ? "true" : "" })}`;
  };

  useEffect(() => {
    const ctrl = new AbortController();
    setBusy(true);
    logApi(query(0), token, { signal: ctrl.signal })
      .then(res => {
        setRows(res.runs || []);
        setTotal(res.total || 0);
        setError(null);
      })
      .catch(e => e.name !== "AbortError" && setError(e))
      .finally(() => setBusy(false));
    return () => ctrl.abort();
  }, [filter, hours, token, tick]); // eslint-disable-line react-hooks/exhaustive-deps

  const more = async () => {
    setBusy(true);
    try {
      const res = await logApi(query(rows.length), token);
      setRows(r => [...r, ...(res.runs || [])]);
      setTotal(res.total || 0);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const apply = e => {
    e.preventDefault();
    setFilter({ ...draft });
  };
  const set = (k, v) => setDraft(d => ({ ...d, [k]: v }));
  const changed = JSON.stringify(draft) !== JSON.stringify(filter);
  const anyFilter = JSON.stringify(filter) !== JSON.stringify(EMPTY_FILTER);

  return (
    <section className="card logs-section">
      <form className="toolbar logs-filters" onSubmit={apply}>
        <select className="input" value={draft.activity} onChange={e => set("activity", e.target.value)} aria-label="Activity">
          <option value="">All activities</option>
          {ACTIVITIES.map(a => (
            <option key={a} value={a}>
              {activityLabel(a)}
            </option>
          ))}
        </select>
        <select className="input" value={draft.status} onChange={e => set("status", e.target.value)} aria-label="Status">
          <option value="">All statuses</option>
          {["SUCCESS", "WARNING", "FAILED", "RUNNING"].map(s => (
            <option key={s} value={s}>
              {s.toLowerCase()}
            </option>
          ))}
        </select>
        <input className="input logs-module" placeholder="Module" value={draft.module} onChange={e => set("module", e.target.value)} aria-label="Module" />
        <label className="search logs-q">
          <Search size={15} aria-hidden="true" />
          <input
            className="input"
            type="search"
            placeholder="Search command, screen, error, question"
            value={draft.q}
            onChange={e => set("q", e.target.value)}
            aria-label="Search runs"
          />
        </label>
        <label className="check">
          <input type="checkbox" checked={draft.top} onChange={e => set("top", e.target.checked)} /> Top-level only
        </label>
        <button className={`btn${changed ? " primary" : ""}`} type="submit">
          Apply
        </button>
        {anyFilter && (
          <button className="btn ghost" type="button" onClick={() => setFilter({ ...EMPTY_FILTER })}>
            Clear filters
          </button>
        )}
        <span className="muted small" aria-live="polite">
          {rows ? `${num(rows.length)} of ${num(total)} runs` : ""}
        </span>
      </form>
      {error && <p className="note logs-gap" role="alert">{error.message}</p>}
      {rows ? (
        <>
          <LogTable
            head={["Started", "Status", "Activity", "Command", "Module", "Screen / question", "#Duration", "Counts", "Error", "Source"]}
            rows={rows.length}
            empty="No runs match."
          >
            {rows.map(r => (
              <RunRow key={r.run_id} r={r} openRun={openRun} />
            ))}
          </LogTable>
          {rows.length < total && (
            <p>
              <button className="btn" type="button" onClick={more} disabled={busy}>
                {busy ? "Loading…" : `Load ${Math.min(100, total - rows.length)} more`}
              </button>
            </p>
          )}
        </>
      ) : (
        !error && <TabState res={{ data: null }} what="runs" />
      )}
    </section>
  );
}

/* ---------------- Questions ---------------- */
export function Questions({ hours, token, tick, openRun }) {
  const res = useLogs(`runs?activity=retrieval&limit=200&hours=${hours}`, token, tick);
  if (!res.data) return <TabState res={res} what="questions" />;
  const rows = (res.data.runs || []).filter(r => r.command === "chat");
  return (
    <section className="card logs-section">
      <StaleError res={res} />
      <h2>Questions asked in Ask PIE</h2>
      <p className="muted small">Choose a question to see how the answer was reached.</p>
      <LogTable
        head={["Asked", "Question", "For", "Intent", "Grounding", "Written by", "#Retrieved", "#Cited", "#Seconds", "Status"]}
        rows={rows.length}
        empty="No questions in this time window."
      >
        {rows.map(r => {
          const a = r.attrs || {};
          const c = r.counts || {};
          return (
            <ClickRow key={r.run_id} onOpen={() => openRun(r.run_id)} label={`How the answer to "${a.question || ""}" was reached`}>
              <td className="nowrap">{stamp(r.started_at)}</td>
              <td className="wrap">
                <b>{a.question || ""}</b>
                {a.answerPreview && <div className="muted small">{a.answerPreview.slice(0, 160)}…</div>}
              </td>
              <td>{a.audience || "Marketing Team"}</td>
              <td>{a.intent || ""}</td>
              <td>
                <Badge status={a.grounding} />
              </td>
              <td>{a.generator || ""}</td>
              <td className="num">{num(c.retrievedItems)}</td>
              <td className="num">{num(c.citedItems)}</td>
              <td className="num">{c.totalSeconds == null ? "—" : c.totalSeconds}</td>
              <td>
                <Badge status={r.status} />
                {r.error_message && <div className="t-bad small">{r.error_message}</div>}
              </td>
            </ClickRow>
          );
        })}
      </LogTable>
    </section>
  );
}

/* ---------------- Knowledge inventory ---------------- */
const SCREEN_PAGE = 200;

export function Knowledge({ token, tick }) {
  const toast = useToast();
  const res = useLogs("inventory", token, tick);
  const [mod, setMod] = useState("");
  const [gapsOnly, setGapsOnly] = useState(false);
  const [limit, setLimit] = useState(SCREEN_PAGE);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const inv = res.data;

  const gap = s => s.qdrant_fields != null && s.qdrant_fields < (s.unique_fields || s.fields);
  const screens = useMemo(
    () => ((inv && inv.screens) || []).filter(s => (!mod || s.module === mod) && (!gapsOnly || gap(s) || !s.backend_found)),
    [inv, mod, gapsOnly]
  );
  useEffect(() => setLimit(SCREEN_PAGE), [mod, gapsOnly]);

  const rebuild = async () => {
    setBusy(true);
    setErr(null);
    try {
      await logApi("inventory/refresh", token, { method: "POST" });
      toast("Inventory rebuilt");
      res.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };

  if (!inv) return <TabState res={res} what="the knowledge inventory" />;
  const st = (m, k) => (m.stats || {})[k];
  const mods = inv.modules || [];
  return (
    <>
      <StaleError res={res} />
      <section className="card logs-section">
        <div className="logs-h2row">
          <h2>Modules</h2>
          <button className="btn small" type="button" onClick={rebuild} disabled={busy}>
            <RefreshCw size={14} className={busy ? "logs-spin" : ""} /> {busy ? "Rebuilding…" : "Rebuild inventory"}
          </button>
          <span className="muted small">{inv.updatedAt ? `built ${stamp(inv.updatedAt)}` : "Not built yet: press Rebuild inventory."}</span>
        </div>
        {err && <p className="note logs-gap" role="alert">{err.message}</p>}
        <LogTable
          head={["Module", "#Screens", "#Screens with APIs", "#API links to DB", "DB metadata", "#Features", "#Capabilities", "#Ready", "#Field screens", "#Fields", "#Qdrant points", "Updated"]}
          rows={mods.length}
          empty="No module outputs found."
        >
          {mods.map(m => (
            <tr key={m.module}>
              <td>
                <b>{moduleLabel(m.module)}</b>
              </td>
              <td className="num">{num(st(m, "api.screens"))}</td>
              <td className="num">{num(st(m, "api.screensWithApis"))}</td>
              <td className="num nowrap">
                {num(st(m, "db.apiMappingsWithDatabase"))} / {num(st(m, "db.apiMappings"))}
              </td>
              <td>{(m.db_source || "—").replace(/_/g, " ")}</td>
              <td className="num">{num(st(m, "usage.features"))}</td>
              <td className="num">{num(st(m, "usage.capabilities"))}</td>
              <td className="num">{num(st(m, "usage.ready"))}</td>
              <td className="num">{num(m.field_screens)}</td>
              <td className="num">{num(m.fields)}</td>
              <td className="num">{num(m.qdrant_points)}</td>
              <td className="nowrap">{stamp(m.updated_at)}</td>
            </tr>
          ))}
        </LogTable>
      </section>

      <section className="card logs-section">
        <h2>Screens, sections and fields</h2>
        <div className="toolbar">
          <select className="input" value={mod} onChange={e => setMod(e.target.value)} aria-label="Module">
            <option value="">All modules</option>
            {mods.map(m => (
              <option key={m.module} value={m.module}>
                {moduleLabel(m.module)}
              </option>
            ))}
          </select>
          <label className="check">
            <input type="checkbox" checked={gapsOnly} onChange={e => setGapsOnly(e.target.checked)} /> Only screens with gaps (missing in Qdrant or no
            backend)
          </label>
          <span className="muted small" aria-live="polite">
            {num(screens.length)} screens · highlighted rows have fields missing in Qdrant
          </span>
        </div>
        <LogTable
          head={["Module", "Screen", "Entity / table", "Backend", "#Sections", "#Sections in Qdrant", "#Fields", "#Unique", "#Fields in Qdrant", "#Mandatory", "#Mapped to column", "#Explained", "#Flagged", "Generated", ""]}
          rows={screens.length}
          empty="No field dictionaries found."
        >
          {screens.slice(0, limit).map((s, i) => (
            <tr key={`${s.module}-${s.form || s.screen_id}-${i}`} className={gap(s) ? "logs-gaprow" : ""}>
              <td>{moduleLabel(s.module)}</td>
              <td>
                <b>{s.screen}</b>
                <div className="muted small">{s.screen_id || ""}</div>
              </td>
              <td>
                {s.entity || "—"}
                {s.tbl ? ` / ${s.tbl}` : ""}
              </td>
              <td>{s.backend_found ? "found" : <span className="t-warn">not found</span>}</td>
              <td className="num">{num(s.sections)}</td>
              <td className="num">{num(s.qdrant_sections)}</td>
              <td className="num">{num(s.fields)}</td>
              <td className="num">{num(s.unique_fields)}</td>
              <td className="num">{num(s.qdrant_fields)}</td>
              <td className="num">{num(s.mandatory)}</td>
              <td className="num">{num(s.mapped_to_column)}</td>
              <td className="num">{num(s.explained)}</td>
              <td className="num">{num(s.flagged)}</td>
              <td className="nowrap">{stamp(s.generated_at)}</td>
              <td className="nowrap">
                {s.screen_id && (
                  <>
                    <Link to={`/relations?screen=${encodeURIComponent(s.screen_id)}`}>relationships</Link>
                    {" · "}
                    <Link to={`/fields?screen=${encodeURIComponent(s.screen_id)}`}>fields</Link>
                  </>
                )}
              </td>
            </tr>
          ))}
        </LogTable>
        {screens.length > limit && (
          <p>
            <button className="btn" type="button" onClick={() => setLimit(l => l + SCREEN_PAGE)}>
              Show {Math.min(SCREEN_PAGE, screens.length - limit)} more ({num(screens.length - limit)} hidden)
            </button>
          </p>
        )}
      </section>
    </>
  );
}

/* ---------------- Qdrant storage ---------------- */
export function Qdrant({ token, tick }) {
  const toast = useToast();
  const res = useLogs("qdrant?limit=48", token, tick);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const check = async () => {
    setBusy(true);
    setErr(null);
    try {
      await logApi("qdrant/refresh", token, { method: "POST" });
      toast("Qdrant checked");
      res.reload();
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  const checkBtn = (
    <button className="btn small" type="button" onClick={check} disabled={busy}>
      <Database size={14} /> {busy ? "Checking…" : "Check Qdrant now"}
    </button>
  );
  if (!res.data) return <TabState res={res} what="the Qdrant status" />;
  const q = res.data.latest;
  const history = res.data.history || [];
  if (!q) {
    return (
      <section className="card logs-section">
        <h2>No Qdrant status yet</h2>
        <p className="muted">PIE has not checked the knowledge store yet.</p>
        {err && <p className="note logs-gap" role="alert">{err.message}</p>}
        {checkBtn}
      </section>
    );
  }
  const types = Object.keys(q.by_type || {});
  const mods = Object.entries(q.by_module || {}).sort((a, b) => (b[1].points || 0) - (a[1].points || 0));
  return (
    <>
      <StaleError res={res} />
      <div className="stats">
        <Stat value={q.status} label={`collection ${q.collection || ""}`} tone={toneOf(q.status)} />
        <Stat value={num(q.points)} label="points (knowledge items)" />
        <Stat value={num(q.indexed_vectors)} label="indexed vectors" />
        <Stat value={num(q.segments)} label="segments" />
        <Stat value={<span className="logs-statsmall">{stamp(q.ts)}</span>} label="last checked" />
      </div>
      {q.error && <p className="note logs-gap t-bad">{q.error}</p>}
      <section className="card logs-section">
        <div className="logs-h2row">
          <h2>Points by module and knowledge type</h2>
          {checkBtn}
        </div>
        {err && <p className="note logs-gap" role="alert">{err.message}</p>}
        <LogTable head={["Module", "#Total", ...types.map(t => `#${t}`)]} rows={mods.length} empty="No points by module.">
          {mods.map(([m, v]) => (
            <tr key={m}>
              <td>
                <b>{m ? moduleLabel(m) : "(no module)"}</b>
              </td>
              <td className="num">{num(v.points)}</td>
              {types.map(t => (
                <td key={t} className="num">
                  {num((v.types || {})[t])}
                </td>
              ))}
            </tr>
          ))}
          {mods.length > 0 && (
            <tr className="logs-total">
              <td>All</td>
              <td className="num">{num(q.points)}</td>
              {types.map(t => (
                <td key={t} className="num">
                  {num(q.by_type[t])}
                </td>
              ))}
            </tr>
          )}
        </LogTable>
      </section>
      <section className="card logs-section">
        <h2>Status history</h2>
        <LogTable head={["Checked", "Status", "#Points", "Error"]} rows={history.length}>
          {history.map((x, i) => (
            <tr key={i}>
              <td className="nowrap">{stamp(x.ts)}</td>
              <td>
                <Badge status={x.status} />
              </td>
              <td className="num">{num(x.points)}</td>
              <td className="wrap">{x.error || ""}</td>
            </tr>
          ))}
        </LogTable>
      </section>
    </>
  );
}

/* ---------------- API requests ---------------- */
export function ApiRequests({ hours, token, tick }) {
  const res = useLogs(`api-requests?hours=${hours}`, token, tick);
  if (!res.data) return <TabState res={res} what="API requests" />;
  const paths = res.data.paths || [];
  return (
    <section className="card logs-section">
      <StaleError res={res} />
      <h2>Requests by endpoint</h2>
      <LogTable head={["Method", "Path", "#Requests", "#Errors", "#Avg", "#Max", "Last"]} rows={paths.length} empty="No API requests in this time window.">
        {paths.map((p, i) => (
          <tr key={`${p.method} ${p.path} ${i}`}>
            <td>{p.method}</td>
            <td className="wrap">
              <code>{p.path}</code>
            </td>
            <td className="num">{num(p.requests)}</td>
            <td className={`num${p.errors ? " t-bad" : ""}`}>{num(p.errors)}</td>
            <td className="num">{dur(p.avg_ms)}</td>
            <td className="num">{dur(p.max_ms)}</td>
            <td className="nowrap">{stamp(p.last)}</td>
          </tr>
        ))}
      </LogTable>
    </section>
  );
}
