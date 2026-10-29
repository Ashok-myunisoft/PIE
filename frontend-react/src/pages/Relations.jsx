// Relationships: how each ERP screen connects to APIs, backend services and database tables,
// with the evidence for every link and the PIE knowledge held for the screen.
// Address: /relations?screen=<id>&modules=a,b
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ChevronRight, MessageSquare, Network, Search, Table2 } from "lucide-react";
import { useApi, useModuleSelection, usePatchParams, useStored } from "../lib/hooks.js";
import { dateTime, moduleLabel, num } from "../lib/format.js";
import { Chip, EmptyState, ErrorState, Loading, PageHeader } from "../components/ui.jsx";
import ModulePicker from "../components/ModulePicker.jsx";
import Diagram from "./relations/Diagram.jsx";
import "./Relations.css";

const plural = (n, word) => `${num(n)} ${word}${n === 1 ? "" : "s"}`;

/** OBSERVED / CANDIDATE label with the confidence. */
function LinkStatus({ status, confidence }) {
  const observed = status === "OBSERVED";
  return (
    <Chip
      tone={observed ? "ok" : "warn"}
      title={observed ? "Seen directly in code or configuration" : "Inferred from code (SQL text): likely, but not proven"}
    >
      {(status || "?").toLowerCase()}
      {confidence != null ? ` ${Math.round(confidence * 100)}%` : ""}
    </Chip>
  );
}

/** First few evidence lines: where it was found and what was found. */
function Evidence({ items, max = 3 }) {
  const list = (items || []).slice(0, max);
  if (!list.length) return null;
  return list.map((e, i) => (
    <div className="rel-ev" key={i}>
      {e.where && <code>{e.where}</code>} {e.what || e.fact || ""}
      {(e.operations || []).length > 0 && <Chip>{e.operations.join("/")}</Chip>}
    </div>
  ));
}

export default function Relations() {
  const [params] = useSearchParams();
  const patch = usePatchParams();
  const current = params.get("screen") || "";
  const [modules, setModules] = useModuleSelection("relations");
  const [term, setTerm] = useState("");
  const [onlyMapped, setOnlyMapped] = useStored("relations:onlyMapped", true);

  const list = useApi("api/relations/screens");
  const screens = (list.data && list.data.screens) || [];
  const allModules = (list.data && list.data.modules) || [];
  const graphs = (list.data && list.data.graphs) || [];

  const options = useMemo(
    () =>
      allModules.map(m => ({
        value: m,
        label: moduleLabel(m),
        note: plural(screens.filter(s => s.module === m).length, "screen"),
      })),
    [allModules, screens]
  );

  const shown = useMemo(() => {
    const q = term.trim().toLowerCase();
    return screens.filter(
      s =>
        (!modules.length || modules.includes(s.module)) &&
        (!onlyMapped || s.apis > 0) &&
        (!q || (s.label || "").toLowerCase().includes(q) || (s.screenId || "").toLowerCase().includes(q))
    );
  }, [screens, modules, onlyMapped, term]);

  const open = sid => patch(p => p.set("screen", sid));

  // Keep the chosen screen visible in the list.
  const listRef = useRef(null);
  useEffect(() => {
    const el = listRef.current && listRef.current.querySelector('[aria-current="true"]');
    if (el) el.scrollIntoView({ block: "nearest" });
  }, [current, shown.length]);

  return (
    <main className="page rel-page">
      <PageHeader
        title="Relationships"
        description="How each ERP screen connects to APIs, backend services and database tables, with the evidence for every link and the PIE knowledge held for the screen."
      />
      <div className="rel-layout">
        <aside className="rel-list card" aria-label="Screens">
          <div className="rel-filters">
            <label className="search">
              <Search size={15} aria-hidden="true" />
              <input
                className="input"
                type="search"
                placeholder="Search screens…"
                aria-label="Search screens"
                value={term}
                onChange={e => setTerm(e.target.value)}
              />
            </label>
            <ModulePicker
              title="Show screens of"
              options={options}
              selected={modules}
              onChange={setModules}
              loading={list.loading}
            />
            <label className="check">
              <input type="checkbox" checked={onlyMapped} onChange={e => setOnlyMapped(e.target.checked)} /> Only screens with APIs
            </label>
            {list.data && (
              <span className="muted small" aria-live="polite">
                {num(shown.length)} of {plural(screens.length, "screen")}
              </span>
            )}
          </div>
          <div className="rel-slist" ref={listRef}>
            {list.loading && !list.data && <Loading>Loading screens…</Loading>}
            {list.error && !list.data && <ErrorState error={list.error} onRetry={list.reload} title="Could not load the screens" />}
            {list.data && !shown.length && (
              <EmptyState title="No screens match">
                {onlyMapped ? "Try clearing the search, choosing all modules, or showing screens without APIs." : "Try another search or module."}
              </EmptyState>
            )}
            <ScreenList rows={shown} current={current} onOpen={open} />
          </div>
        </aside>
        <section className="rel-main" aria-label="Screen relationships">
          {current ? (
            <ScreenDetail sid={current} onOpen={open} />
          ) : list.data && !graphs.length ? (
            <div className="card">
              <EmptyState icon={Network} title="No relationship graph yet">
                Run <code>build-evidence-graph</code> for a module first.
              </EmptyState>
            </div>
          ) : (
            <div className="card">
              <EmptyState icon={Network} title="Choose a screen">
                Pick a screen on the left to see how it connects to the backend and the database.
              </EmptyState>
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function ScreenList({ rows, current, onOpen }) {
  const out = [];
  let last = null;
  for (const s of rows) {
    if (s.module !== last) {
      out.push(
        <div className="rel-grp" key={`g-${s.module}`}>
          {moduleLabel(s.module)}
        </div>
      );
      last = s.module;
    }
    out.push(
      <button type="button" key={s.screenId} aria-current={s.screenId === current} onClick={() => onOpen(s.screenId)}>
        {s.label || s.screenId}
        <small>
          {plural(s.apis || 0, "API")} · {plural(s.tables || 0, "table")}
          {s.hasFields ? " · fields" : ""}
        </small>
      </button>
    );
  }
  return out;
}

function ScreenDetail({ sid, onOpen }) {
  const { data, error, loading, reload } = useApi(`api/relations/screen/${encodeURIComponent(sid)}`);
  const top = useRef(null);
  const [onlyKey, setOnlyKey] = useState(false);
  const [highlight, setHighlight] = useState("");

  // Large screens start with only the tables that matter most (written, or holding this screen's fields).
  useEffect(() => {
    if (data && data.screen && data.screen.screenId === sid) {
      setOnlyKey((data.tables || []).length > 18);
      setHighlight("");
    }
  }, [data, sid]);

  const allTables = (data && data.tables) || [];
  const tables = useMemo(
    () => allTables.filter(t => !onlyKey || (t.fields || []).length || (t.operations || []).includes("WRITE")),
    [allTables, onlyKey]
  );

  const stale = data && data.screen && data.screen.screenId !== sid;
  if (error && (!data || stale)) return <div className="card"><ErrorState error={error} onRetry={reload} title="Could not load this screen" /></div>;
  if (!data || stale) return <div className="card"><Loading>Loading relationships…</Loading></div>;

  const s = data.screen || {};
  const st = data.stats || {};
  const kn = data.knowledge || {};
  const knCount = Object.values(kn).reduce((n, g) => n + (g.count || 0), 0);

  const showTable = name => {
    setHighlight(name);
    const row = document.getElementById(`rel-t-${name}`);
    if (row) row.scrollIntoView({ block: "nearest", behavior: "smooth" });
  };
  const openOther = id => {
    onOpen(id);
    if (top.current) top.current.scrollIntoView({ block: "start" });
  };

  return (
    <>
      <div className="card rel-panel" ref={top}>
        <div className="rel-title">
          <h2>
            {s.label || s.screenId} <span className="muted">{s.screenId}</span>
          </h2>
          <div className="rel-actions">
            {data.fieldDictionary && (
              <Link className="btn small" to={`/fields?screen=${encodeURIComponent(s.screenId)}`}>
                <Table2 size={14} /> Fields
              </Link>
            )}
            <Link className="btn small" to={`/chat?q=${encodeURIComponent(`What does the ${s.label || s.screenId} screen do?`)}`}>
              <MessageSquare size={14} /> Ask PIE
            </Link>
          </div>
        </div>
        <div className="rel-chips">
          <Chip>module: {moduleLabel(s.module)}</Chip>
          {s.status && <Chip tone={s.status === "CONFIRMED" ? "ok" : "warn"}>screen {s.status.toLowerCase()}</Chip>}
          <Chip>{plural(st.apis || 0, "API")}</Chip>
          <Chip>{plural(st.endpoints || 0, "backend endpoint")}</Chip>
          <Chip>{plural(st.services || 0, "service")}</Chip>
          <Chip>
            {plural(st.tables || 0, "table")} ({num(st.writes || 0)} written)
          </Chip>
          <Chip>{plural(st.fieldsMapped || 0, "field")} mapped to columns</Chip>
          {data.knowledge && <Chip tone="accent">{plural(knCount, "PIE knowledge item")}</Chip>}
        </div>
        <div className="rel-legend">
          <span>
            <i /> observed link
          </span>
          <span>
            <i className="cand" /> candidate link (inferred from SQL)
          </span>
          <span>Hover, focus or click a box to follow its links.</span>
          <label className="check">
            <input type="checkbox" checked={onlyKey} onChange={e => setOnlyKey(e.target.checked)} /> Only tables this screen writes or stores fields in
            {onlyKey && ` (${tables.length} of ${allTables.length})`}
          </label>
        </div>
        <Diagram data={data} tables={tables} onTable={showTable} />
      </div>

      <div className="card rel-panel">
        <h3>Database tables</h3>
        {allTables.length ? (
          <div className="table-wrap">
            <table className="table rel-table">
              <thead>
                <tr>
                  <th>Table</th>
                  <th>Access</th>
                  <th>Link</th>
                  <th>Reached through</th>
                  <th>This screen's fields stored here</th>
                  <th>Also used by</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {allTables.map(t => (
                  <tr key={t.id || t.name} id={`rel-t-${t.name}`} className={highlight === t.name ? "hl" : ""}>
                    <td>
                      <b>{t.name}</b>
                      <div className="rel-ev">
                        {t.schema} {t.objectType}
                      </div>
                      {(t.definedIn || []).length > 0 && (
                        <div className="rel-ev" title={t.definedIn.join("\n")}>
                          defined in <code>{t.definedIn[0]}</code>
                          {t.definedIn.length > 1 ? ` +${t.definedIn.length - 1} more` : ""}
                        </div>
                      )}
                    </td>
                    <td>
                      {(t.operations || []).length
                        ? t.operations.map(o => (
                            <Chip key={o} tone={o === "WRITE" ? "warn" : undefined}>
                              {o.toLowerCase()}
                            </Chip>
                          ))
                        : "-"}
                    </td>
                    <td>
                      {(t.statuses || []).map(x => (
                        <LinkStatus key={x} status={x} confidence={t.confidence} />
                      ))}
                    </td>
                    <td className="nowrap">
                      {(t.via || []).map(v => (
                        <div key={v}>
                          <code>{v}</code>
                        </div>
                      ))}
                    </td>
                    <td>
                      {(t.fields || []).length ? (
                        <>
                          {t.fields.slice(0, 12).map((f, i) => (
                            <div key={i}>
                              {f.label}{" "}
                              <span className="rel-ev">
                                <code>{f.column}</code>
                                {f.dataType ? ` ${f.dataType}` : ""}
                              </span>
                            </div>
                          ))}
                          {t.fields.length > 12 && <div className="rel-ev">+{t.fields.length - 12} more</div>}
                        </>
                      ) : (
                        <span className="muted">-</span>
                      )}
                    </td>
                    <td>
                      {(t.alsoUsedBy || []).slice(0, 6).map(o => (
                        <div key={o.screenId}>
                          <button type="button" className="rel-linkish" onClick={() => openOther(o.screenId)}>
                            {o.label || o.screenId}
                          </button>{" "}
                          <span className="rel-ev">{moduleLabel(o.module)}</span>
                        </div>
                      ))}
                      {t.alsoUsedByCount > 6 && <div className="rel-ev">+{t.alsoUsedByCount - 6} more</div>}
                      {!t.alsoUsedByCount && <span className="muted">only this screen</span>}
                    </td>
                    <td>
                      <Evidence items={t.evidence} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted">PIE found no database table for this screen.</p>
        )}
      </div>

      <div className="rel-two">
        <div className="card rel-panel">
          <h3>APIs and backend</h3>
          <div className="table-wrap">
            <table className="table rel-table">
              <thead>
                <tr>
                  <th>API</th>
                  <th>Implemented by</th>
                  <th>Evidence</th>
                </tr>
              </thead>
              <tbody>
                {(data.apis || []).map(a => (
                  <tr key={a.id}>
                    <td>
                      <b className="rel-route">
                        {a.method} {a.route}
                      </b>
                      <div>
                        <LinkStatus status={a.status} confidence={a.confidence} />
                      </div>
                    </td>
                    <td>
                      {(a.endpoints || []).length ? (
                        a.endpoints.map(ep => (
                          <div key={ep.id} className="rel-ep">
                            <b>{ep.name}</b> <LinkStatus status={ep.status} confidence={ep.confidence} />
                            {ep.file && (
                              <div className="rel-ev">
                                <code>{ep.file}</code>
                              </div>
                            )}
                            {(ep.services || []).map(x => (
                              <div className="rel-ev" key={x.id || x.name}>
                                {x.relationship === "USES_SERVICE" ? "uses" : "calls"} <b>{x.name}</b> {x.file && <code>{x.file}</code>}
                              </div>
                            ))}
                          </div>
                        ))
                      ) : (
                        <span className="muted">not found</span>
                      )}
                    </td>
                    <td>
                      <Evidence items={a.evidence} />
                      <Evidence items={(a.apiEvidence || []).map(e => ({ where: e.source, what: e.fact }))} max={2} />
                    </td>
                  </tr>
                ))}
                {!(data.apis || []).length && (
                  <tr>
                    <td colSpan={3} className="muted">
                      This screen calls no API that PIE could map.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="card rel-panel">
          <h3>Why PIE knows this screen exists</h3>
          {(data.evidence || []).length ? (
            data.evidence.map((e, i) => (
              <div className="rel-ev rel-evline" key={i}>
                <code>{e.source}</code> {e.fact}
              </div>
            ))
          ) : (
            <p className="muted">No evidence recorded.</p>
          )}

          <h3 className="rel-h3gap">PIE knowledge stored for this screen</h3>
          {data.knowledge == null ? (
            <p className="muted">The knowledge base (Qdrant) could not be reached.</p>
          ) : Object.keys(kn).length ? (
            <div className="rel-kn">
              {Object.entries(kn).map(([type, g]) => (
                <details key={type}>
                  <summary>
                    <ChevronRight size={14} aria-hidden="true" className="rel-caret" />
                    <b>{type}</b> <span className="muted">({num(g.count)})</span>
                  </summary>
                  <ul>
                    {(g.items || []).map((it, i) => (
                      <li key={it.knowledgeId || i}>
                        {it.title}{" "}
                        {it.status && it.status !== "PUBLISHED" && <Chip tone="warn">needs review</Chip>}
                        {it.readiness && <Chip>{it.readiness.toLowerCase()}</Chip>}
                      </li>
                    ))}
                  </ul>
                  {g.count > (g.items || []).length && <div className="rel-ev">+{g.count - g.items.length} more</div>}
                </details>
              ))}
            </div>
          ) : (
            <p className="muted">No knowledge items for this screen.</p>
          )}

          {data.fieldDictionary && (
            <p>
              <Link to={`/fields?screen=${encodeURIComponent(s.screenId)}`}>Open the Field Explorer</Link> for all{" "}
              {num(data.fieldDictionary.fields)} fields of this screen.
            </p>
          )}
          {data.graph && (
            <p className="rel-ev">
              Source: <code>{data.graph.file}</code>
              {data.graph.generatedAt ? ` · built ${dateTime(data.graph.generatedAt)}` : ""}
            </p>
          )}
        </div>
      </div>
    </>
  );
}
