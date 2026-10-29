// One PIE answer: the text with [K1] citations, sources, "How PIE answered" and actions.
import { useRef } from "react";
import { AlertTriangle, Copy, RefreshCw, ExternalLink } from "lucide-react";
import { markdown } from "../../lib/markdown.js";
import { Chip } from "../../components/ui.jsx";

const GROUNDING = {
  grounded: ["Grounded in PIE knowledge", "ok"],
  partial: ["Partly grounded: some knowledge missing", "warn"],
  unverified: ["No citations: verify before use", "warn"],
  not_found: ["Not found in knowledge base", "bad"],
};

const unconfirmed = st => st === "REVIEW" || st === "AI";

/** Link to the Relationships page for a screen (opens in a new tab). */
export function RelLink({ screenId }) {
  if (!screenId) return null;
  return (
    <a className="rel" href={`#/relations?screen=${encodeURIComponent(screenId)}`} target="_blank" rel="noopener" title="Screen → API → backend → tables">
      Relationships <ExternalLink size={11} />
    </a>
  );
}

function Source({ s, idx }) {
  const where = [s.module, s.capability].filter(Boolean).join(" · ");
  const text = s.text || "";
  return (
    <div className="src" id={`src-${idx}-${s.ref}`}>
      <div className="row">
        <span className="ref">{s.ref}</span>
        <span className={`st ${s.status}`}>{unconfirmed(s.status) ? "⚠ unconfirmed" : "✓ " + String(s.status || "").toLowerCase()}</span>
        <b>{s.title}</b>
        {s.kind && <Chip>{String(s.kind).replace("_", " ")}</Chip>}
        {where && <Chip>{where}</Chip>}
        <RelLink screenId={s.screenId} />
      </div>
      <div className="txt">
        {text.slice(0, 320)}
        {text.length > 320 ? "…" : ""}
      </div>
      {(s.evidence || []).length > 0 && (
        <div className="txt">
          {s.evidence.slice(0, 3).map((e, i) => (
            <div key={i}>
              <code>{e.source}</code> {e.detail || ""}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- "How PIE answered": the answer trace ---------- */
function KV({ rows }) {
  const shown = rows.filter(([, v]) => v !== "" && v != null && v !== false && !(Array.isArray(v) && !v.length));
  if (!shown.length) return null;
  return (
    <div className="kv">
      {shown.map(([k, v]) => [
        <span key={k + "k"}>{k}</span>,
        <b key={k + "v"}>
          {Array.isArray(v)
            ? v.map((x, i) => (
                <span className="q" key={i}>
                  {String(x)}
                </span>
              ))
            : String(v)}
        </b>,
      ])}
    </div>
  );
}

function StepDetail({ s }) {
  switch (s.step) {
    case "understand":
      return (
        <KV
          rows={[
            ["Intents", (s.intents || []).map(i => i.label + (i.matchedWord ? ` ("${i.matchedWord}")` : ""))],
            ["Modules", (s.modules || []).length ? s.modules : s.moduleSource],
            ["Module from", (s.modules || []).length ? s.moduleSource : ""],
            ["Answer written for", s.audience],
            ["Industry", s.industry],
            ["Persona in the question", s.persona],
            ["Follow-up", s.followUp ? "yes: combined with the previous question" : ""],
            ["Read as", s.contextMessage],
          ]}
        />
      );
    case "plan":
      return (
        <KV
          rows={[
            ["Planner", s.planner],
            ["Keyword query", s.keywordQuery],
            ["AI planner queries", s.plannerQueries],
            ["All search queries", s.searchQueries],
          ]}
        />
      );
    case "retrieve":
      return (
        <>
          <KV
            rows={[
              ["Candidates", s.candidates],
              ["Duplicates removed", s.duplicates],
              ["Kept for the answer", s.kept],
              ["Order given to AI", s.ordering],
              ["Screens named", s.namedScreens],
              ["Unreviewed knowledge", s.reviewKnowledgeIncluded ? "included (toggle on)" : "excluded"],
            ]}
          />
          {(s.routes || []).length > 0 && (
            <div className="ttable">
              <table>
                <thead>
                  <tr>
                    <th>Retrieval route</th>
                    <th className="num">Items</th>
                  </tr>
                </thead>
                <tbody>
                  {s.routes.map((r, i) => (
                    <tr key={i}>
                      <td>{r.route}</td>
                      <td className="num">{r.items}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {(s.droppedOverLimit || []).length > 0 && (
            <div className="txt">Left out (over the limit): {s.droppedOverLimit.map(d => d.title).join("; ")}</div>
          )}
        </>
      );
    case "coverage":
      return (
        <>
          <KV rows={[["Not in the knowledge base", s.missing]]} />
          {(s.notes || []).length > 0 && (
            <ul className="txt">
              {s.notes.map((n, i) => (
                <li key={i}>{n}</li>
              ))}
            </ul>
          )}
        </>
      );
    case "answer":
      return (
        <KV
          rows={[
            ["Written by", s.generator],
            ["Written for", s.audience],
            ["Audience instruction", s.audienceNote],
            ["Rule", s.rule],
            ["Items given", s.itemsGiven],
            ["Earlier turns sent", s.historyTurns],
            ["Answer guide", s.answerGuide],
            ["Also", s.extraGuides],
            ["Prompt size", s.contextChars ? s.contextChars.toLocaleString() + " characters" : ""],
            ["Model error", s.llmError],
          ]}
        />
      );
    case "validate":
      return (
        <KV
          rows={[
            ["Cited", s.cited],
            ["Invalid citations removed", s.invalidCitations],
            ["Claim words not in knowledge", s.unsupportedTerms],
            ["Citations filled in", s.citationFallback ? "yes: the model cited nothing, its field records are shown" : ""],
            ["Result", s.status],
          ]}
        />
      );
    default:
      return <pre>{JSON.stringify(s, null, 1)}</pre>;
  }
}

function Trace({ r }) {
  const tr = r.trace;
  if (!tr || !(tr.steps || []).length) return null;
  const items = tr.retrieved || [];
  const cited = items.filter(i => i.cited).length;
  return (
    <details className="trace">
      <summary>
        How PIE answered · {tr.steps.length} steps · {items.length} knowledge items, {cited} cited
      </summary>
      <ol className="steps">
        {tr.steps.map((s, i) => (
          <li key={i}>
            <div className="sname">{s.step}</div>
            <div className="ssum">{s.summary}</div>
            <StepDetail s={s} />
          </li>
        ))}
      </ol>
      <h4>Knowledge the answer could use ({items.length}), in the order the AI saw it</h4>
      {items.length ? (
        <div className="ttable">
          <table>
            <thead>
              <tr>
                <th>Ref</th>
                <th>Used</th>
                <th>Status</th>
                <th>Type</th>
                <th>Knowledge</th>
                <th>Module</th>
                <th>Found by</th>
                <th className="num">Score</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((i, n) => (
                <tr key={n} className={i.cited ? "" : "dim"}>
                  <td>
                    <b>{i.ref || ""}</b>
                  </td>
                  <td>{i.cited ? <span className="yes">✓ cited</span> : "not used"}</td>
                  <td>
                    <span className={`st ${i.status}`}>{i.status || ""}</span>
                  </td>
                  <td>{i.knowledgeType || i.kind || ""}</td>
                  <td title={i.text || ""}>{i.title}</td>
                  <td>{i.module || ""}</td>
                  <td>{i.route || ""}</td>
                  <td className="num">{i.score == null ? "" : Number(i.score).toFixed(3)}</td>
                  <td>
                    <RelLink screenId={i.screenId} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="txt">No knowledge items.</div>
      )}
      {tr.context && (
        <details className="inner">
          <summary>Exact text given to the AI ({tr.context.length.toLocaleString()} characters)</summary>
          <h4>System instructions</h4>
          <pre>{tr.systemPrompt || ""}</pre>
          <h4>Question, answer guide, knowledge items and coverage notes</h4>
          <pre>{tr.context}</pre>
        </details>
      )}
      {r.runId && (
        <div className="txt">
          Saved in the{" "}
          <a href={`#/logs?run=${encodeURIComponent(r.runId)}`} target="_blank" rel="noopener">
            Activity Log
          </a>{" "}
          as run {r.runId.slice(0, 12)}.
        </div>
      )}
    </details>
  );
}

export default function AnswerCard({ message, idx, isLast, onCopy, onRetry }) {
  const sourcesRef = useRef(null);

  if (message.error) {
    return (
      <div className="answer error">
        <div className="answer-error">
          <AlertTriangle size={18} />
          <span>{message.error}</span>
        </div>
        <div className="answer-foot">
          <button className="btn small" type="button" onClick={onRetry}>
            <RefreshCw size={13} /> Try again
          </button>
        </div>
      </div>
    );
  }

  const r = message.response || {};
  const g = r.grounding || {};
  const [gLabel, gTone] = GROUNDING[g.status] || [g.status, ""];
  const missing = g.missing || [];
  const notes = (g.notes || []).filter(n => /unavailable|Removed|Check before use|did not cite/.test(n));
  const sources = r.sources || [];

  // Clicking [K3] in the answer opens the sources and highlights K3.
  function onAnswerClick(e) {
    const c = e.target.closest(".cite");
    if (!c || !sourcesRef.current) return;
    sourcesRef.current.open = true;
    const el = sourcesRef.current.querySelector(`#src-${idx}-${c.dataset.ref}`);
    if (el) {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
      el.classList.add("flash");
      setTimeout(() => el.classList.remove("flash"), 900);
    }
  }

  return (
    <div className="answer">
      <div className="answer-head">
        {r.audience && (
          <Chip tone="accent" title="Written for this audience">
            for: {r.audience.label}
          </Chip>
        )}
        {(r.intentLabel || r.intent) && <Chip>{r.intentLabel || r.intent}</Chip>}
        {g.status && <Chip tone={gTone}>{gLabel}</Chip>}
        {r.plan && r.plan.modules && r.plan.modules.length > 0 && <Chip>{r.plan.modules.join(", ")}</Chip>}
        {r.plan && r.plan.industry && <Chip>industry: {r.plan.industry}</Chip>}
        {r.plan && r.plan.persona && <Chip>audience: {r.plan.persona}</Chip>}
      </div>
      <div className="md" onClick={onAnswerClick} dangerouslySetInnerHTML={{ __html: markdown(r.answer || "") }} />
      {missing.length > 0 && (
        <div className="note">
          <b>Not in the PIE knowledge base:</b> {missing.join(", ")}.
        </div>
      )}
      {notes.length > 0 && (
        <div className="note">
          <ul>
            {notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        </div>
      )}
      {sources.length > 0 && (
        <details className="sources" ref={sourcesRef} open={isLast}>
          <summary>
            Sources used ({sources.length} of {g.retrievedItems || sources.length} retrieved)
          </summary>
          {sources.map(s => (
            <Source key={s.ref} s={s} idx={idx} />
          ))}
        </details>
      )}
      <Trace r={r} />
      <div className="answer-foot">
        <button className="btn small" type="button" onClick={onCopy}>
          <Copy size={13} /> Copy answer
        </button>
        <span className="spacer" />
        <span>
          {r.plan ? r.plan.generator : ""}
          {r.timings ? ` · ${r.timings.totalSeconds}s` : ""}
        </span>
      </div>
    </div>
  );
}
