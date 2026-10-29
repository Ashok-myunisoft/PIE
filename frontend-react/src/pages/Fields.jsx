// Field Explorer: every field of an ERP screen with its type, rules, dependencies, database and
// code mapping, usage across the ERP and source evidence.
// Address parameters: ?modules=a,b  ?screen=<screen>  ?field=<field key>  ?q=<search>
import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Download, FileSpreadsheet, Filter, Search, X } from "lucide-react";
import { api, API } from "../lib/api.js";
import { copyText, downloadText, useApi, useDebounced, useModuleSelection, usePatchParams, useStored } from "../lib/hooks.js";
import { moduleLabel, num, toCsv } from "../lib/format.js";
import { EmptyState, ErrorState, Loading, PageHeader, Stat, useToast } from "../components/ui.jsx";
import ModulePicker from "../components/ModulePicker.jsx";
import FieldDrawer, { RuleTags } from "./fields/FieldDrawer.jsx";
import { FLAGS, csvRows, fieldKey, filterFields, groupScreens, haystack, normalizeField, summaryText, where } from "./fields/logic.js";
import "./Fields.css";

/* ---------- loading one screen's field dictionary (kept in memory once loaded) ---------- */
const dictCache = new Map();

function useScreenDict(screen) {
  const [state, setState] = useState({ screen: null, data: null, error: null });
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (!screen) return undefined;
    if (dictCache.has(screen)) {
      setState({ screen, data: dictCache.get(screen), error: null });
      return undefined;
    }
    const ctrl = new AbortController();
    let timer;
    setState({ screen, data: null, error: null });
    api("api/fields/" + encodeURIComponent(screen), { signal: ctrl.signal })
      .then(raw => {
        const fields = (Array.isArray(raw.fields) ? raw.fields : []).map(f => {
          const r = normalizeField(f);
          r._hay = haystack(r);
          return r;
        });
        const data = { ...raw, fields, statistics: raw.statistics || {} };
        dictCache.set(screen, data);
        setState({ screen, data, error: null });
      })
      .catch(error => {
        if (error.name === "AbortError") return;
        setState({ screen, data: null, error });
        if (error.status === 0) timer = setTimeout(() => setNonce(n => n + 1), 5000); // backend down: try again
      });
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [screen, nonce]);
  const reload = useCallback(() => setNonce(n => n + 1), []);
  const current = state.screen === screen;
  return { data: current ? state.data : null, error: current ? state.error : null, loading: !!screen && current && !state.data && !state.error, reload };
}

function shortDate(t) {
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString();
}

export default function Fields() {
  const [params] = useSearchParams();
  const patch = usePatchParams();
  const [modules, setModules] = useModuleSelection("fields");
  const [storedScreen, setStoredScreen] = useStored("fields:screen", "");
  const list = useApi("api/fields");
  const screens = useMemo(() => (Array.isArray(list.data) ? list.data.filter(s => s && s.screen) : []), [list.data]);

  // Change address parameters (an empty value removes it); replaces the history entry.
  const setParam = useCallback(changes => patch(p => Object.entries(changes).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)))), [patch]);

  /* ----- modules and screens ----- */
  const allModules = useMemo(() => [...new Set(screens.map(s => s.module || "other"))].sort(), [screens]);
  const moduleOpts = useMemo(() => {
    const count = {};
    screens.forEach(s => (count[s.module || "other"] = (count[s.module || "other"] || 0) + 1));
    return allModules.map(m => ({ value: m, label: moduleLabel(m), note: `${count[m]} screens` }));
  }, [screens, allModules]);
  const selMods = useMemo(() => modules.filter(m => !allModules.length || allModules.includes(m)), [modules, allModules]);
  const visible = useMemo(() => screens.filter(s => !selMods.length || selMods.includes(s.module || "other")), [screens, selMods]);
  const grouped = useMemo(() => groupScreens(visible.length ? visible : screens), [visible, screens]);

  // The screen asked for in the address wins (a shared link), then the last one used here,
  // as long as it belongs to the chosen modules; otherwise the first screen in the list.
  const urlScreen = params.get("screen") || "";
  const screen = useMemo(() => {
    if (!screens.length) return "";
    if (urlScreen && screens.some(s => s.screen === urlScreen)) return urlScreen;
    const pool = visible.length ? visible : screens;
    if (storedScreen && pool.some(s => s.screen === storedScreen)) return storedScreen;
    return grouped.length ? grouped[0][1][0].screen : pool[0].screen;
  }, [screens, visible, grouped, urlScreen, storedScreen]);
  const screenInfo = screens.find(s => s.screen === screen);

  function chooseScreen(next) {
    setStoredScreen(next);
    setParam({ screen: next, field: "" });
  }
  function chooseModules(next) {
    setModules(next);
    const pool = screens.filter(s => !next.length || next.includes(s.module || "other"));
    if (pool.length && !pool.some(s => s.screen === screen)) {
      const first = groupScreens(pool)[0][1][0].screen;
      setStoredScreen(first);
      setParam({ screen: first, field: "" });
    }
  }

  // Screen list filter (there are hundreds of screens).
  const [screenTerm, setScreenTerm] = useState("");
  const shownGroups = useMemo(() => {
    const t = screenTerm.trim().toLowerCase();
    if (!t) return grouped;
    return grouped
      .map(([m, ss]) => [m, ss.filter(s => `${s.screenName || ""} ${s.screen} ${m}`.toLowerCase().includes(t))])
      .filter(([, ss]) => ss.length);
  }, [grouped, screenTerm]);
  const screenInShown = shownGroups.some(([, ss]) => ss.some(s => s.screen === screen));

  /* ----- search ----- */
  const [q, setQ] = useState(() => params.get("q") || "");
  const dq = useDebounced(q.trim(), 150);
  useEffect(() => {
    if ((params.get("q") || "") !== dq) setParam({ q: dq });
  }, [dq]); // eslint-disable-line react-hooks/exhaustive-deps

  const dict = useScreenDict(screen);

  /* ----- render ----- */
  return (
    <main className="page fx-page">
      <PageHeader
        title="Field Explorer"
        description="Every field of a GoodBooks ERP screen: type, rules, dependencies, database and code mapping, usage and source evidence."
      >
        {dict.data && screenInfo && screenInfo.excel !== false && (
          <a className="btn" href={`${API}api/fields/${encodeURIComponent(screen)}/excel`} title="Excel workbook with every attribute of every field on this screen">
            <FileSpreadsheet size={15} /> Download Excel
          </a>
        )}
      </PageHeader>

      {list.error && !screens.length ? (
        <ErrorState error={list.error} onRetry={list.reload} title="Could not load the field dictionaries" />
      ) : list.loading && !screens.length ? (
        <Loading>Loading the list of screens…</Loading>
      ) : !screens.length ? (
        <EmptyState title="No field dictionary has been built yet">
          Build it on the server with <code>python -m pie_agent.cli fields-build --enrich</code>, then reload this page.
        </EmptyState>
      ) : (
        <>
          <div className="toolbar fx-pick">
            <ModulePicker title="Screens of" allLabel="All modules" options={moduleOpts} selected={selMods} onChange={chooseModules} />
            <label className="search fx-screen-find">
              <Search size={15} aria-hidden="true" />
              <input
                className="input"
                type="search"
                placeholder={`Find a screen (${visible.length || screens.length})…`}
                value={screenTerm}
                onChange={e => setScreenTerm(e.target.value)}
                aria-label="Find a screen"
              />
            </label>
            <label className="fx-screen-sel">
              <span className="sr-only">Screen</span>
              <select className="input" value={screen} onChange={e => chooseScreen(e.target.value)} aria-label="Screen">
                {!screenInShown && screenInfo && (
                  <option value={screen}>
                    {screenInfo.screenName || screen}
                  </option>
                )}
                {shownGroups.map(([m, ss]) => (
                  <optgroup key={m} label={moduleLabel(m)}>
                    {ss.map(s => (
                      <option key={s.screen} value={s.screen}>
                        {s.screenName || s.screen}
                        {s.statistics && s.statistics.fields ? ` (${s.statistics.fields})` : ""}
                      </option>
                    ))}
                  </optgroup>
                ))}
              </select>
            </label>
            {screenTerm && !shownGroups.length && <span className="muted">No screen matches “{screenTerm}”.</span>}
          </div>

          {dict.error ? (
            <ErrorState error={dict.error} onRetry={dict.reload} title={`Could not load the fields of ${screenInfo ? screenInfo.screenName : screen}`} />
          ) : !dict.data ? (
            <Loading>Loading the fields of {screenInfo ? screenInfo.screenName : screen}…</Loading>
          ) : (
            <ScreenView key={screen} screen={screen} dict={dict.data} q={dq} qInput={q} setQ={setQ} params={params} setParam={setParam} />
          )}
        </>
      )}
    </main>
  );
}

/* ---------- one screen: stats, filters, table, detail panel ---------- */
function ScreenView({ screen, dict, q, qInput, setQ, params, setParam }) {
  const toast = useToast();
  const [page, setPage] = useState("");
  const [flags, setFlags] = useState([]);
  const [filtersOpen, setFiltersOpen] = useState(false);
  const fields = dict.fields;
  const s = dict.statistics;

  const byName = useMemo(() => {
    const m = new Map();
    fields.forEach(r => !m.has(r.fieldName) && m.set(r.fieldName, r));
    return m;
  }, [fields]);
  const pages = useMemo(() => {
    const c = new Map();
    fields.forEach(r => c.set(where(r), (c.get(where(r)) || 0) + 1));
    return [...c.entries()];
  }, [fields]);
  const flagCounts = useMemo(() => Object.fromEntries(FLAGS.map(([k, , fn]) => [k, fields.filter(fn).length])), [fields]);
  const shown = useMemo(() => filterFields(fields, { page, flags, q }), [fields, page, flags, q]);
  const conflicts = useMemo(() => [...new Set(fields.map(r => r.duplicateDefinition).filter(Boolean))], [fields]);

  // The open field lives in the address (?field=), so a copied address opens it again.
  const fieldParam = params.get("field") || "";
  const open = useMemo(() => (fieldParam ? fields.find(r => fieldKey(r) === fieldParam) || byName.get(fieldParam) || null : null), [fieldParam, fields, byName]);
  const openField = useCallback(k => setParam({ field: k }), [setParam]);
  const closeField = useCallback(() => setParam({ field: "" }), [setParam]);

  const toggleFlag = k => setFlags(f => (f.includes(k) ? f.filter(x => x !== k) : [...f, k]));
  const filtered = page || flags.length || q;
  const clearAll = () => {
    setPage("");
    setFlags([]);
    setQ("");
  };

  async function copy(text, done = "Copied") {
    toast((await copyText(text)) ? done : "Copy failed");
  }
  function copyLink() {
    if (!open) return;
    const base = window.location.href.split("#")[0];
    const p = new URLSearchParams({ screen, field: fieldKey(open) });
    copy(`${base}#/fields?${p}`, "Link copied");
  }
  function downloadCsv() {
    downloadText(`${screen}-fields.csv`, toCsv(csvRows(shown)), "text/csv");
  }

  const stats = [
    [s.fields ?? fields.length, "Field definitions"],
    [s.uniqueFields || s.fields || fields.length, "Unique fields"],
    [s.mandatory, "Mandatory"],
    [s.mappedToColumn, dict.table ? `Stored in ${dict.table}` : "Stored in a table column"],
    [s.lookups, "Lookups"],
    [s.calculated, "Calculated"],
    [s.usedByOtherModules, "Used by other modules"],
    [s.explained, "With AI explanation"],
  ];
  const flagged = s.explanationsFlagged || 0;

  return (
    <>
      <div className="statusline fx-sub">
        <span>
          <b>{dict.screen || screen}</b>
        </span>
        {dict.module && <span>Module <b>{moduleLabel(dict.module)}</b></span>}
        <span>Table <b>{dict.table || "not found"}</b></span>
        {dict.generatedAt && <span>Built {shortDate(dict.generatedAt)}</span>}
      </div>

      <div className="stats">
        {stats.map(([v, l]) => (
          <Stat key={l} value={num(v)} label={l} />
        ))}
      </div>

      <div className="fx-notes">
        {conflicts.map(x => (
          <div className="note" key={x}>
            <b>Source conflict:</b> {x}.
          </div>
        ))}
        <div className="note info">
          Facts in every record come straight from the ERP sources, with file and line evidence. Text in the dashed <b>AI-derived</b> box is written by AI from
          those facts only{flagged ? `; ${flagged} of these explanations carry a review note` : ""}. Check it before using it outside the team.
        </div>
      </div>

      <div className="fx-layout">
        <aside id="fx-filters" className={`fx-filters${filtersOpen ? " open" : ""}`} aria-label="Filters">
          <div className="fx-fgroup" role="group" aria-labelledby="fx-fg-page">
            <h3 id="fx-fg-page">Tab / section</h3>
            <FilterOpt label="All" n={fields.length} pressed={!page} onClick={() => setPage("")} />
            {pages.map(([p, n]) => (
              <FilterOpt key={p} label={p} n={n} pressed={page === p} onClick={() => setPage(page === p ? "" : p)} />
            ))}
          </div>
          <div className="fx-fgroup" role="group" aria-labelledby="fx-fg-flags">
            <h3 id="fx-fg-flags">Show only</h3>
            {FLAGS.map(([k, label]) => (
              <FilterOpt key={k} label={label} n={flagCounts[k]} pressed={flags.includes(k)} disabled={!flagCounts[k] && !flags.includes(k)} onClick={() => toggleFlag(k)} />
            ))}
          </div>
        </aside>

        <div className="fx-main">
          <div className="toolbar fx-tools">
            <button className="btn small fx-filters-toggle" type="button" aria-expanded={filtersOpen} aria-controls="fx-filters" onClick={() => setFiltersOpen(o => !o)}>
              <Filter size={14} /> Filters{page || flags.length ? ` (${(page ? 1 : 0) + flags.length})` : ""}
            </button>
            <label className="search fx-q">
              <Search size={15} aria-hidden="true" />
              <input
                className="input"
                type="search"
                placeholder="Search label, technical name, column…"
                value={qInput}
                onChange={e => setQ(e.target.value)}
                autoComplete="off"
                aria-label="Search fields"
              />
            </label>
            <span className="muted fx-count" aria-live="polite">
              {shown.length === fields.length ? `${num(fields.length)} fields` : `${num(shown.length)} of ${num(fields.length)} fields`}
            </span>
            {filtered && (
              <button className="btn small ghost" type="button" onClick={clearAll}>
                <X size={14} /> Clear filters
              </button>
            )}
            <button className="btn small fx-csv" type="button" onClick={downloadCsv} disabled={!shown.length} title="CSV of the fields currently shown">
              <Download size={14} /> Download CSV (shown fields)
            </button>
          </div>

          {fields.length === 0 ? (
            <EmptyState title="No fields found for this screen">The field dictionary for this screen is empty.</EmptyState>
          ) : (
            <div className="table-wrap fx-table-wrap">
              <table className="table fx-table">
                <thead>
                  <tr>
                    <th scope="col">Field</th>
                    <th scope="col">Technical name</th>
                    <th scope="col" className="fx-col-hide">Tab</th>
                    <th scope="col" className="fx-col-hide">Type</th>
                    <th scope="col">Rules</th>
                    <th scope="col" className="fx-col-hide">Stored in</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.length ? (
                    shown.map(r => <Row key={fieldKey(r) + ":" + r.order} r={r} selected={open === r} onOpen={openField} />)
                  ) : (
                    <tr>
                      <td colSpan={6}>
                        <EmptyState title="No fields match these filters" action={<button className="btn small" type="button" onClick={clearAll}>Clear filters</button>} />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {open && (
        <FieldDrawer
          field={open}
          dict={dict}
          byName={byName}
          onOpenField={openField}
          onClose={closeField}
          onCopySummary={() => copy(summaryText(open, dict.screen || screen), "Summary copied")}
          onCopyLink={copyLink}
        />
      )}
    </>
  );
}

function FilterOpt({ label, n, pressed, onClick, disabled }) {
  return (
    <button className="fx-fopt" type="button" aria-pressed={pressed} onClick={onClick} disabled={disabled}>
      <span>{label}</span>
      <span className="fx-n">{n}</span>
    </button>
  );
}

function StoredIn({ r }) {
  const db = r.database;
  if (db.column)
    return (
      <code>
        {db.table}.{db.column}
      </code>
    );
  if (db.lookupDisplay)
    return (
      <>
        <code>
          {db.lookupDisplay.table}.{db.lookupDisplay.column}
        </code>{" "}
        <span className="muted">(lookup)</span>
      </>
    );
  if (db.childTable)
    return (
      <>
        <code>{db.childTable.table}</code> <span className="muted">(grid rows)</span>
      </>
    );
  return <span className="muted">{db.storage || ""}</span>;
}

// Rows are plain (no hooks), and only re-render when their own field or selection changes.
const Row = memo(function Row({ r, selected, onOpen }) {
  const k = fieldKey(r);
  return (
    <tr className={`clickable${selected ? " fx-sel" : ""}`} onClick={() => onOpen(k)}>
      <td>
        <button type="button" className="fx-rowbtn" onClick={e => (e.stopPropagation(), onOpen(k))} aria-label={`Open details of ${r.label}`}>
          {r.label}
        </button>
        {r.section && <div className="muted fx-small">{r.section}</div>}
      </td>
      <td>
        <code>{r.fieldName}</code>
      </td>
      <td className="fx-col-hide">{where(r)}</td>
      <td className="fx-col-hide">{r.type.ui || r.type.form}</td>
      <td>
        <div className="fx-tags">
          <RuleTags r={r} />
        </div>
      </td>
      <td className="fx-col-hide fx-stored">
        <StoredIn r={r} />
      </td>
    </tr>
  );
});
