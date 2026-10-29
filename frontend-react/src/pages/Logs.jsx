// Activity Log: every PIE discovery, extraction, mapping, Qdrant ingestion and question, with
// status, counts, errors and how each answer was reached.
// Address: /logs?tab=runs&run=<id>  (run opens the detail panel; links from Ask PIE use it)
import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { KeyRound, RefreshCw } from "lucide-react";
import { PageHeader, useToast } from "../components/ui.jsx";
import { usePatchParams, useStored } from "../lib/hooks.js";
import { readToken, saveToken } from "./logs/logsApi.js";
import { ApiRequests, EMPTY_FILTER, Knowledge, Overview, Qdrant, Questions, Runs } from "./logs/Tabs.jsx";
import RunDetail from "./logs/RunDetail.jsx";
import "./Logs.css";

const TABS = [
  ["overview", "Overview"],
  ["runs", "Runs"],
  ["questions", "Questions"],
  ["knowledge", "Knowledge inventory"],
  ["qdrant", "Qdrant storage"],
  ["api", "API requests"],
];
const HOURS = [
  [1, "Last hour"],
  [24, "Last 24 hours"],
  [168, "Last 7 days"],
  [720, "Last 30 days"],
];

export default function Logs() {
  const toast = useToast();
  const [params] = useSearchParams();
  const patch = usePatchParams();
  const tab = TABS.some(([k]) => k === params.get("tab")) ? params.get("tab") : "overview";
  const run = params.get("run") || "";

  const [hours, setHours] = useStored("logs:hours", 24);
  const [auto, setAuto] = useStored("logs:auto", true);
  const [token, setToken] = useState(readToken);
  const [draftToken, setDraftToken] = useState(token);
  const [tick, setTick] = useState(0);
  const [refreshedAt, setRefreshedAt] = useState(() => new Date());
  const [runFilter, setRunFilter] = useState(EMPTY_FILTER);

  const refresh = useCallback(() => {
    setTick(t => t + 1);
    setRefreshedAt(new Date());
  }, []);

  // Auto-refresh every 30 s, but not on the Runs tab (it would lose loaded pages), not while a
  // run is open, and not while the browser tab is hidden.
  useEffect(() => {
    if (!auto) return undefined;
    const t = setInterval(() => {
      if (!document.hidden && tab !== "runs" && !run) refresh();
    }, 30000);
    return () => clearInterval(t);
  }, [auto, tab, run, refresh]);

  const setTab = name =>
    patch(p => {
      if (name === "overview") p.delete("tab");
      else p.set("tab", name);
    });
  const openRun = useCallback(id => patch(p => p.set("run", id)), [patch]);
  const closeRun = useCallback(() => patch(p => p.delete("run")), [patch]);
  const showActivity = activity => {
    setRunFilter({ ...EMPTY_FILTER, activity });
    setTab("runs");
  };

  const save = e => {
    e.preventDefault();
    const t = draftToken.trim();
    saveToken(t);
    setToken(t);
    toast(t ? "Log token saved" : "Log token cleared");
  };

  const common = { hours, token, tick, openRun };

  return (
    <main className="page logs-page">
      <PageHeader title="Activity Log" description="Every PIE run, question and error: status, counts, and how each answer was reached.">
        <select className="input" value={hours} onChange={e => setHours(Number(e.target.value))} aria-label="Time window">
          {HOURS.map(([h, label]) => (
            <option key={h} value={h}>
              {label}
            </option>
          ))}
        </select>
        <label className="check" title="Reload this tab every 30 seconds">
          <input type="checkbox" checked={auto} onChange={e => setAuto(e.target.checked)} /> Auto-refresh
        </label>
        <button className="btn primary" type="button" onClick={refresh}>
          <RefreshCw size={14} /> Refresh
        </button>
      </PageHeader>

      <div className="logs-bar">
        <form className="logs-token" onSubmit={save}>
          <KeyRound size={15} aria-hidden="true" className="muted" />
          <input
            className="input"
            type="password"
            placeholder="Log token"
            autoComplete="off"
            aria-label="Log token"
            value={draftToken}
            onChange={e => setDraftToken(e.target.value)}
          />
          <button className="btn" type="submit">
            Save
          </button>
          <span className="muted small">Only needed if the server asks for one.</span>
        </form>
        <span className="muted small" aria-live="polite">
          Updated {refreshedAt.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
        </span>
      </div>

      <div className="logs-tabs" role="tablist" aria-label="Activity log sections">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`logs-tab-${key}`}
            aria-selected={tab === key}
            aria-controls="logs-panel"
            className={tab === key ? "on" : ""}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      <div id="logs-panel" role="tabpanel" aria-labelledby={`logs-tab-${tab}`}>
        {tab === "overview" && <Overview {...common} showActivity={showActivity} />}
        {tab === "runs" && <Runs {...common} filter={runFilter} setFilter={setRunFilter} />}
        {tab === "questions" && <Questions {...common} />}
        {tab === "knowledge" && <Knowledge {...common} />}
        {tab === "qdrant" && <Qdrant {...common} />}
        {tab === "api" && <ApiRequests {...common} />}
      </div>

      {run && <RunDetail id={run} token={token} onOpen={openRun} onClose={closeRun} />}
    </main>
  );
}
