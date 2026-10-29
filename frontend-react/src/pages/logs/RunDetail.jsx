// Side panel with everything about one run: timing, error and traceback, how a chat answer
// was reached (trace), counts, details, parameters, sub-activities and events.
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Copy, X } from "lucide-react";
import { Loading, ErrorState, useToast } from "../../components/ui.jsx";
import { copyText } from "../../lib/hooks.js";
import { activityLabel, dur, stamp, useLogs } from "./logsApi.js";
import { Badge, ClickRow, Headline, LogTable } from "./bits.jsx";

const show = v => (typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

/** Key/value list for one trace step; arrays become small tags. */
function StepValues({ step }) {
  const rows = Object.entries(step).filter(
    ([k, v]) => !["step", "summary"].includes(k) && v !== "" && v != null && v !== false && !(Array.isArray(v) && !v.length)
  );
  if (!rows.length) return null;
  return (
    <div className="kv logs-kv">
      {rows.map(([k, v]) => [
        <span key={`${k}-k`}>{k}</span>,
        <b key={`${k}-v`}>
          {Array.isArray(v)
            ? v.map((x, i) => (
                <span className="logs-tag" key={i}>
                  {typeof x === "object" && x !== null
                    ? `${x.label || x.route || x.title || JSON.stringify(x)}${x.matchedWord ? ` ("${x.matchedWord}")` : ""}${x.items != null ? `: ${x.items}` : ""}`
                    : String(x)}
                </span>
              ))
            : show(v)}
        </b>,
      ])}
    </div>
  );
}

function Trace({ trace }) {
  const items = trace.retrieved || [];
  const cited = items.filter(i => i.cited).length;
  return (
    <>
      <h3>How the answer was reached</h3>
      <ol className="logs-steps">
        {(trace.steps || []).map((s, i) => (
          <li key={i}>
            <b className="cap">{s.step}</b> <span>{s.summary}</span>
            <StepValues step={s} />
          </li>
        ))}
      </ol>
      <h3>
        Knowledge items ({items.length}, {cited} cited)
      </h3>
      <LogTable head={["Ref", "Used", "Status", "Type", "Knowledge", "Module", "Found by", "#Score"]} rows={items.length} empty="No knowledge items.">
        {items.map((it, i) => (
          <tr key={i} className={it.cited ? "" : "logs-dim"}>
            <td>
              <b>{it.ref || ""}</b>
            </td>
            <td>{it.cited ? <span className="t-ok">cited</span> : "not used"}</td>
            <td>{it.status || ""}</td>
            <td>{it.knowledgeType || it.kind || ""}</td>
            <td className="wrap">
              {it.title}
              {it.screenId && (
                <>
                  {" "}
                  <Link to={`/relations?screen=${encodeURIComponent(it.screenId)}`}>relationships</Link>
                </>
              )}
            </td>
            <td>{it.module || ""}</td>
            <td className="wrap">{it.route || ""}</td>
            <td className="num">{it.score == null ? "" : Number(it.score).toFixed(3)}</td>
          </tr>
        ))}
      </LogTable>
    </>
  );
}

function Pairs({ obj }) {
  const rows = Object.entries(obj || {});
  if (!rows.length) return <p className="muted">None.</p>;
  return (
    <div className="kv logs-kv">
      {rows.map(([k, v]) => [<span key={`${k}-k`}>{k}</span>, <b key={`${k}-v`}>{show(v)}</b>])}
    </div>
  );
}

export default function RunDetail({ id, token, onOpen, onClose }) {
  const toast = useToast();
  const res = useLogs(`runs/${encodeURIComponent(id)}`, token, 0);
  const closeBtn = useRef(null);
  const r = res.data && res.data.run_id === id ? res.data : null;

  useEffect(() => {
    const onKey = e => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    if (closeBtn.current) closeBtn.current.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const events = (r && r.events) || [];
  const traceEv = events.find(e => e.data && e.data.trace);
  const others = events.filter(e => e !== traceEv);

  const copyLink = async () => toast((await copyText(window.location.href)) ? "Link to this run copied" : "Copy failed");

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer logs-drawer" role="dialog" aria-modal="true" aria-label="Run detail">
        <div className="drawer-head">
          <h2>
            {r ? (
              <>
                <Badge status={r.status} /> <span className="cap">{activityLabel(r.activity)}</span> · <code>{r.command}</code>
              </>
            ) : (
              "Run detail"
            )}
          </h2>
          <button className="btn small" type="button" onClick={copyLink} title="Copy a link that opens this run">
            <Copy size={14} /> Link
          </button>
          <button ref={closeBtn} className="icon-btn" type="button" onClick={onClose} aria-label="Close run detail">
            <X size={18} />
          </button>
        </div>
        <div className="drawer-body">
          {!r && res.error && <ErrorState error={res.error} onRetry={res.reload} title="Could not load this run" />}
          {!r && !res.error && <Loading>Loading the run…</Loading>}
          {r && (
            <>
              {r.attrs && r.attrs.question && (
                <p className="logs-question">
                  <b>{r.attrs.question}</b>
                </p>
              )}
              <p className="muted small">
                Run <code>{r.run_id}</code>
                {r.parent_id && (
                  <>
                    {" "}
                    · part of{" "}
                    <button type="button" className="logs-link" onClick={() => onOpen(r.parent_id)}>
                      {r.parent_id.slice(0, 12)}
                    </button>
                  </>
                )}
                <br />
                {stamp(r.started_at)} → {stamp(r.finished_at)} ({dur(r.duration_ms)}) · {r.source} on {r.host} (process {r.pid})
                {r.module ? ` · module ${r.module}` : ""}
                {r.screen ? ` · screen ${r.screen}` : ""}
              </p>
              {r.error_message && (
                <>
                  <h3 className="t-bad">
                    {r.error_type ? `${r.error_type}: ` : ""}
                    {r.error_message}
                  </h3>
                  {r.traceback && <pre className="logs-pre">{r.traceback}</pre>}
                </>
              )}
              {traceEv && <Trace trace={traceEv.data.trace} />}
              <h3>Record counts</h3>
              <Pairs obj={r.counts} />
              <h3>Details</h3>
              <Pairs obj={r.attrs} />
              <h3>Parameters</h3>
              <pre className="logs-pre">{JSON.stringify(r.params, null, 1)}</pre>
              {(r.children || []).length > 0 && (
                <>
                  <h3>Sub-activities ({r.children.length})</h3>
                  <LogTable head={["Status", "Activity", "#Duration", "Counts / error"]} rows={r.children.length}>
                    {r.children.map(c => (
                      <ClickRow key={c.run_id} onOpen={() => onOpen(c.run_id)} label={`Open sub-activity ${activityLabel(c.activity)}`}>
                        <td>
                          <Badge status={c.status} />
                        </td>
                        <td className="cap">{activityLabel(c.activity)}</td>
                        <td className="num">{dur(c.duration_ms)}</td>
                        <td className="wrap small">
                          <Headline counts={c.counts} /> {c.error_message ? <span className="t-bad">{c.error_message}</span> : ""}
                        </td>
                      </ClickRow>
                    ))}
                  </LogTable>
                </>
              )}
              <h3>Events ({others.length})</h3>
              {others.length ? (
                <LogTable head={["Time", "Level", "Message"]} rows={others.length}>
                  {others.map((e, i) => (
                    <tr key={i}>
                      <td className="nowrap muted">{new Date(e.ts).toLocaleTimeString()}</td>
                      <td>
                        <Badge status={e.level} />
                      </td>
                      <td className="wrap">
                        {e.message}
                        {e.data && <span className="muted small"> {JSON.stringify(e.data).slice(0, 400)}</span>}
                      </td>
                    </tr>
                  ))}
                </LogTable>
              ) : (
                <p className="muted">None.</p>
              )}
            </>
          )}
        </div>
      </aside>
    </>
  );
}
