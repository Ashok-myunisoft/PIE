// Feature Catalog: evidence-backed product features for the Marketing team.
// URL parameters (shareable): ?modules=ess,inventory&tab=features&capability=...&q=...&feature=SCREEN_ID
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Check, Download, Filter, LayoutGrid, List, Search, X } from "lucide-react";
import { api } from "../lib/api.js";
import { downloadText, useApi, useDebounced, useModuleSelection, usePatchParams, useStored } from "../lib/hooks.js";
import { moduleOptions, num, toCsv } from "../lib/format.js";
import { Chip, EmptyState, ErrorState, Loading, PageHeader, Stat } from "../components/ui.jsx";
import ModulePicker from "../components/ModulePicker.jsx";
import FeatureDrawer, { Mark } from "./catalog/FeatureDrawer.jsx";
import {
  READINESS,
  READINESS_HELP,
  READINESS_TONE,
  combine,
  csvRows,
  matches,
  normalizeCatalog,
  rankByQuery,
} from "./catalog/catalogData.js";
import "./Catalog.css";

/** Load (and cache) the catalogs of the given modules. Retries on its own while the server is down. */
function useCatalogs(mods) {
  const [cache, setCache] = useState({});
  const [failed, setFailed] = useState({});
  const [nonce, setNonce] = useState(0);
  const key = mods.join(",");

  useEffect(() => {
    const missing = mods.filter(m => !cache[m]);
    if (!missing.length) return undefined;
    const ctrl = new AbortController();
    let timer;
    Promise.allSettled(missing.map(m => api(`api/catalog/${encodeURIComponent(m)}`, { signal: ctrl.signal }))).then(results => {
      if (ctrl.signal.aborted) return;
      const ok = {};
      const bad = {};
      results.forEach((r, i) => {
        if (r.status === "fulfilled") ok[missing[i]] = normalizeCatalog(r.value, missing[i]);
        else bad[missing[i]] = r.reason;
      });
      setCache(c => ({ ...c, ...ok }));
      setFailed(f => {
        const next = { ...f };
        Object.keys(ok).forEach(m => delete next[m]);
        return { ...next, ...bad };
      });
      if (Object.values(bad).some(e => e.status === 0 || e.status >= 500)) timer = setTimeout(() => setNonce(x => x + 1), 5000);
    });
    return () => {
      ctrl.abort();
      clearTimeout(timer);
    };
  }, [key, nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const pending = mods.some(m => !cache[m] && !failed[m]);
  const errors = mods.filter(m => failed[m] && !cache[m]).map(m => [m, failed[m]]);
  const list = mods.filter(m => cache[m]).map(m => cache[m]);
  const retry = useCallback(() => setNonce(x => x + 1), []);
  return { list, pending, errors, retry };
}

/** Capability overview card with the ready / partial / needs-evidence bar. */
function CapabilityCard({ cp, features, onBrowse }) {
  const total = cp.features || 0;
  const w = v => (total ? `${((100 * v) / total).toFixed(1)}%` : "0");
  const top = features.filter(f => f.capability.name === cp.name && f.readiness === "READY");
  return (
    <div className="card cat-cap">
      <div className="cat-meta">
        {cp.needsReview ? (
          <Chip tone="warn" title="Grouping comes from a keyword taxonomy; confirm it in capability_map.json">
            Candidate grouping
          </Chip>
        ) : (
          <Chip tone="ok">Confirmed grouping</Chip>
        )}
        {Object.entries(cp.categories).map(([k, v]) => (
          <Chip key={k}>
            {k} {v}
          </Chip>
        ))}
      </div>
      <h3>{cp.name}</h3>
      <div>
        <b>{num(cp.features)}</b> features · <b>{num(cp.verifiedClaims)}</b> verified highlights
      </div>
      <div className="cat-bar" role="img" aria-label={`${cp.ready} ready, ${cp.partial} partial, ${cp.needsEvidence} needing evidence`}>
        <i className="r" style={{ width: w(cp.ready) }} />
        <i className="p" style={{ width: w(cp.partial) }} />
        <i className="e" style={{ width: w(cp.needsEvidence) }} />
      </div>
      <div className="cat-legend">
        <span className="r">{cp.ready} ready</span>
        <span className="p">{cp.partial} partial</span>
        <span className="e">{cp.needsEvidence} need evidence</span>
      </div>
      {top.length > 0 && (
        <div className="muted cat-small">
          Ready to feature: {top.slice(0, 5).map(f => f.name).join(", ")}
          {top.length > 5 ? "…" : ""}
        </div>
      )}
      <div className="cat-cap-foot">
        <button className="btn small" type="button" onClick={() => onBrowse(cp.name)}>
          Browse {cp.features === 1 ? "feature" : "features"}
        </button>
      </div>
    </div>
  );
}

/** One feature in the grid; opens the detail panel. */
function FeatureCard({ f, multi, onOpen }) {
  const verified = f.claims.filter(x => x.status === "VERIFIED").slice(0, 3);
  return (
    <button type="button" className="card cat-card" onClick={() => onOpen(f.screenId)}>
      <div className="cat-meta">
        <Chip tone={READINESS_TONE[f.readiness]} title={READINESS_HELP[f.readiness]}>
          {READINESS[f.readiness]}
        </Chip>
        <Chip>{f.category}</Chip>
        {multi && <Chip tone="accent">{f.moduleName}</Chip>}
      </div>
      <h3>{f.name}</h3>
      <Chip className="cat-capchip">{f.capability.name}</Chip>
      {f.summary && <div className="cat-desc">{f.summary.text}</div>}
      {verified.length ? (
        <ul className="cat-claims">
          {verified.map((x, i) => (
            <li key={i}>
              <Mark status="VERIFIED" />
              <span>{x.text}</span>
            </li>
          ))}
        </ul>
      ) : (
        <div className="cat-desc">No verified highlights yet.</div>
      )}
      <div className="cat-foot">
        {f.verifiedClaims} verified · {f.reviewClaims} to review
        {f.migrationGaps.length ? " · backend check pending" : ""}
      </div>
    </button>
  );
}

function FilterGroup({ title, value, options, onPick }) {
  return (
    <div className="cat-fgroup" role="group" aria-label={title}>
      <h4>{title}</h4>
      {options.map(o => (
        <button key={o.value || "_all"} type="button" className="cat-fopt" aria-pressed={value === o.value} onClick={() => onPick(o.value)}>
          <span>{o.label}</span>
          <span className="cat-n">{o.n}</span>
        </button>
      ))}
    </div>
  );
}

export default function Catalog() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useModuleSelection("catalog");
  const [storedTab, setStoredTab] = useStored("catalog:tab", "caps");
  const [storedCap, setStoredCap] = useStored("catalog:capability", "");
  const [readyOnly, setReadyOnly] = useStored("catalog:readyOnly", false);
  const [category, setCategory] = useState("");
  const [readiness, setReadiness] = useState("");
  const [filtersOpen, setFiltersOpen] = useState(false);

  // URL wins over what was remembered, so shared links open exactly as sent.
  const tab = params.get("tab") ? (params.get("tab") === "features" ? "feats" : "caps") : storedTab;
  const capability = params.has("capability") ? params.get("capability") : storedCap;
  const openId = params.get("feature") || "";
  const [qInput, setQInput] = useState(() => params.get("q") || "");
  const q = useDebounced(qInput.trim(), 150);

  // Several changes in the same moment (e.g. capability + tab) build on each other.
  const patch = usePatchParams();
  const setParam = useCallback(
    changes => patch(p => Object.entries(changes).forEach(([k, v]) => (v ? p.set(k, v) : p.delete(k)))),
    [patch]
  );

  const setTab = t => {
    setStoredTab(t);
    setParam({ tab: t === "feats" ? "features" : "capabilities" });
  };
  const setCapability = c => {
    setStoredCap(c);
    setParam({ capability: c });
  };

  // Keep ?q= in step with the search box; typing a search jumps to the feature list.
  const lastQ = useRef(q);
  useEffect(() => {
    if (lastQ.current === q) return;
    lastQ.current = q;
    setParam(q ? { q, tab: "features" } : { q: "" });
    if (q) setStoredTab("feats");
  }, [q]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- modules and catalogs
  const modsApi = useApi("api/modules");
  const modules = useMemo(() => (Array.isArray(modsApi.data) ? modsApi.data.filter(m => m && m.module) : []), [modsApi.data]);
  const options = useMemo(
    () => moduleOptions(modules.map(m => m.module), [], Object.fromEntries(modules.map(m => [m.module, m.moduleName || ""]).filter(([, v]) => v))),
    [modules]
  );
  const known = useMemo(() => new Set(modules.map(m => m.module)), [modules]);
  const validSelected = useMemo(() => selected.filter(m => known.has(m)), [selected, known]);
  const mods = useMemo(() => (validSelected.length ? validSelected : modules.map(m => m.module)), [validSelected, modules]);
  const { list, pending, errors, retry } = useCatalogs(mods);
  const catalog = useMemo(() => (pending ? null : combine(list)), [list, pending]);

  // Drop a capability filter that does not exist in the chosen modules.
  const capValid = !capability || !catalog || catalog.capabilities.some(c => c.name === capability);
  const activeCap = capValid ? capability : "";

  const filters = { capability: activeCap, category, readiness, readyOnly, q };
  const shown = useMemo(
    () => (catalog ? rankByQuery(catalog.features.filter(f => matches(f, filters)), q) : []),
    [catalog, activeCap, category, readiness, readyOnly, q] // eslint-disable-line react-hooks/exhaustive-deps
  );
  const openFeature = catalog && openId ? catalog.features.find(f => f.screenId === openId) : null;

  const shareLink = useCallback(
    f => {
      const p = new URLSearchParams();
      if (validSelected.length) p.set("modules", validSelected.join(","));
      p.set("tab", "features");
      p.set("feature", f.screenId);
      const base = window.location.href.split("#")[0];
      return `${base}#/catalog?${p.toString().replace(/%2C/g, ",")}`;
    },
    [validSelected]
  );

  // Opening a feature adds a history entry, so the browser Back button closes the panel.
  const open = id =>
    setParams(
      prev => {
        const p = new URLSearchParams(prev);
        p.set("feature", id);
        return p;
      },
      { replace: false }
    );
  const close = useCallback(() => setParam({ feature: "" }), [setParam]);

  function browse(name) {
    setCapability(name);
    setTab("feats");
  }

  function exportCsv() {
    const name = `${(catalog.module || "catalog").replace(/,/g, "-")}-feature-sheet.csv`;
    downloadText(name, toCsv(csvRows(shown, catalog.multi)), "text/csv");
  }

  const resetFilters = () => {
    setCapability("");
    setCategory("");
    setReadiness("");
    setReadyOnly(false);
    setQInput("");
  };
  const anyFilter = activeCap || category || readiness || readyOnly || q;

  // ---- header
  const subtitle = catalog
    ? [catalog.product, catalog.moduleName, catalog.generatedAt && `generated ${new Date(catalog.generatedAt).toLocaleDateString()}`]
        .filter(Boolean)
        .join(" · ")
    : "";

  const header = (
    <PageHeader
      title="Feature Catalog"
      description={
        subtitle ||
        "Evidence-backed product features for marketing. Every highlight is either verified from the source or flagged for review."
      }
    >
      <ModulePicker title="Catalog of" options={options} selected={validSelected} onChange={setSelected} loading={modsApi.loading} />
      <label className="search cat-search">
        <Search size={15} aria-hidden="true" />
        <span className="sr-only">Search features</span>
        <input
          className="input"
          type="search"
          placeholder="Search features, capabilities, highlights…"
          autoComplete="off"
          value={qInput}
          onChange={e => setQInput(e.target.value)}
        />
      </label>
    </PageHeader>
  );

  // ---- states before there is anything to show
  let body = null;
  if (modsApi.error && !modules.length) body = <ErrorState error={modsApi.error} onRetry={modsApi.reload} title="Could not load the module list" />;
  else if (modsApi.loading && !modules.length) body = <Loading>Loading modules…</Loading>;
  else if (!modules.length)
    body = (
      <EmptyState title="No usage catalog has been built yet">
        Build one on the server with <code>python -m pie_agent.cli usage-build --module inventory</code>, then reload this page.
      </EmptyState>
    );
  else if (pending) body = <Loading>Loading the {mods.length === 1 ? options.find(o => o.value === mods[0])?.label || mods[0] : `${mods.length} module`} catalog…</Loading>;
  else if (!catalog)
    body = <ErrorState error={errors[0] && errors[0][1]} onRetry={retry} title="Could not load the catalog" />;

  if (body)
    return (
      <main className="page">
        {header}
        {body}
      </main>
    );

  const s = catalog.statistics;
  const pct = s.features ? Math.round((100 * (s.ready || 0)) / s.features) : 0;
  const all = catalog.features;
  const countBy = (k, v) => all.filter(f => f[k] === v).length;
  const cats = [...new Set(all.map(f => f.category))].sort();

  return (
    <main className="page cat-page">
      {header}

      {errors.length > 0 && (
        <div className="note cat-gap" role="alert">
          Could not load {errors.map(([m]) => options.find(o => o.value === m)?.label || m).join(", ")}: {errors[0][1].message}{" "}
          <button className="btn small" type="button" onClick={retry}>
            Try again
          </button>
        </div>
      )}

      <div className="cat-policy" role="note">
        <span>
          <Mark status="VERIFIED" /> <b>Verified</b>: traced to source (forms, business logic, services, data). OK for copy with normal review.
        </span>
        <span>
          <Mark status="REVIEW" /> <b>Needs review</b>: inferred or AI-drafted. Get product sign-off before publishing.
        </span>
      </div>

      <div className="stats">
        <Stat value={num(s.features)} label="Features" />
        <Stat value={num(s.capabilities)} label="Capabilities" />
        <Stat value={num(s.ready)} label={`Marketing-ready (${pct}%)`} tone="ok" />
        <Stat value={num(s.verifiedClaims)} label="Verified highlights" />
        <Stat
          value={num((s.reviewClaims || 0) + (s.aiDraftsToReview || 0))}
          label={`Items needing review (incl. ${num(s.aiDraftsToReview || 0)} AI drafts)`}
          tone="warn"
        />
      </div>

      <div className="cat-tabs" role="tablist" aria-label="Catalog views">
        <button type="button" role="tab" id="cat-tab-caps" aria-selected={tab === "caps"} aria-controls="cat-view-caps" onClick={() => setTab("caps")}>
          <LayoutGrid size={15} aria-hidden="true" /> Capabilities <span className="cat-n">{catalog.capabilities.length}</span>
        </button>
        <button type="button" role="tab" id="cat-tab-feats" aria-selected={tab === "feats"} aria-controls="cat-view-feats" onClick={() => setTab("feats")}>
          <List size={15} aria-hidden="true" /> Features <span className="cat-n">{all.length}</span>
        </button>
      </div>

      {tab === "caps" ? (
        <div id="cat-view-caps" role="tabpanel" aria-labelledby="cat-tab-caps">
          {catalog.capabilities.length ? (
            <div className="grid cat-caps">
              {catalog.capabilities.map((cp, i) => (
                <CapabilityCard key={`${cp.module || ""}:${cp.name}:${i}`} cp={cp} features={all} onBrowse={browse} />
              ))}
            </div>
          ) : (
            <EmptyState title="No capabilities in this catalog" />
          )}
        </div>
      ) : (
        <div id="cat-view-feats" role="tabpanel" aria-labelledby="cat-tab-feats" className="cat-layout">
          <aside className={`card cat-filters${filtersOpen ? " open" : ""}`} id="cat-filters" aria-label="Filters">
            <FilterGroup
              title="Capability"
              value={activeCap}
              onPick={setCapability}
              options={[{ value: "", label: "All capabilities", n: all.length }, ...catalog.capabilities.map(cp => ({ value: cp.name, label: cp.name, n: cp.features }))]}
            />
            <FilterGroup
              title="Readiness"
              value={readiness}
              onPick={setReadiness}
              options={[{ value: "", label: "Any", n: all.length }, ...Object.keys(READINESS).map(k => ({ value: k, label: READINESS[k], n: countBy("readiness", k) }))]}
            />
            <FilterGroup
              title="Screen type"
              value={category}
              onPick={setCategory}
              options={[{ value: "", label: "All types", n: all.length }, ...cats.map(k => ({ value: k, label: k, n: countBy("category", k) }))]}
            />
          </aside>

          <div className="cat-main">
            <div className="toolbar cat-toolbar">
              <button
                className="btn small cat-filters-toggle"
                type="button"
                aria-expanded={filtersOpen}
                aria-controls="cat-filters"
                onClick={() => setFiltersOpen(o => !o)}
              >
                <Filter size={14} /> Filters
              </button>
              <span className="muted" aria-live="polite">
                {shown.length} feature{shown.length === 1 ? "" : "s"}
              </span>
              {anyFilter && (
                <button className="btn ghost small" type="button" onClick={resetFilters}>
                  <X size={14} /> Clear filters
                </button>
              )}
              <span className="cat-spacer" />
              <label className="check">
                <input type="checkbox" checked={readyOnly} onChange={e => setReadyOnly(e.target.checked)} /> Marketing-ready only
              </label>
              <button className="btn small" type="button" onClick={exportCsv} disabled={!shown.length} title="Download the features shown below as a spreadsheet">
                <Download size={14} /> Download feature sheet (CSV)
              </button>
            </div>
            {activeCap && (
              <div className="cat-active">
                <Chip tone="accent">
                  <Check size={12} /> {activeCap}
                </Chip>
              </div>
            )}
            {shown.length ? (
              <div className="grid cat-grid">
                {shown.map(f => (
                  <FeatureCard key={`${f.module}:${f.screenId}`} f={f} multi={catalog.multi} onOpen={open} />
                ))}
              </div>
            ) : (
              <EmptyState
                title="No features match these filters"
                action={
                  anyFilter ? (
                    <button className="btn" type="button" onClick={resetFilters}>
                      Clear filters
                    </button>
                  ) : null
                }
              >
                Try a shorter search or another capability.
              </EmptyState>
            )}
          </div>
        </div>
      )}

      {openFeature && <FeatureDrawer feature={openFeature} catalog={catalog} shareLink={shareLink} onClose={close} />}
    </main>
  );
}
