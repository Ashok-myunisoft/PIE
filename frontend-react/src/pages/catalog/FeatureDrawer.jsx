// Side panel with everything known about one feature (screen).
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, Check, Copy, Link2, MessageSquare, Network, Table2, X } from "lucide-react";
import { Chip, useToast } from "../../components/ui.jsx";
import { copyText } from "../../lib/hooks.js";
import { READINESS, READINESS_HELP, READINESS_TONE, blurb } from "./catalogData.js";

export function Mark({ status }) {
  const ok = status === "VERIFIED";
  return (
    <span className={`cat-mark ${ok ? "ok" : "review"}`} role="img" aria-label={ok ? "Verified" : "Needs review"} title={ok ? "Verified" : "Needs review"}>
      {ok ? <Check size={10} strokeWidth={3.5} /> : "!"}
    </span>
  );
}

function ClaimRow({ claim }) {
  return (
    <div className="cat-claim-row">
      <div className="cat-claim-text">
        <Mark status={claim.status} />
        <span>{claim.text}</span>
      </div>
      {claim.evidence.length > 0 && (
        <ul className="cat-ev">
          {claim.evidence.map((e, i) => (
            <li key={i}>
              {e.detail}
              {e.source && (
                <>
                  {" — "}
                  <code>{e.source}</code>
                </>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export default function FeatureDrawer({ feature: f, catalog, shareLink, onClose }) {
  const toast = useToast();
  const [includeAi, setIncludeAi] = useState(false);
  const closeBtn = useRef(null);

  useEffect(() => {
    setIncludeAi(false);
    closeBtn.current && closeBtn.current.focus();
  }, [f.screenId]);

  useEffect(() => {
    const onKey = e => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const copy = async (text, what) => toast((await copyText(text)) ? `${what} copied to clipboard` : "Copy failed");

  const verified = f.claims.filter(x => x.status === "VERIFIED");
  const review = f.claims.filter(x => x.status !== "VERIFIED");
  const t = f.technical;
  const screen = encodeURIComponent(f.screenId);
  const question = encodeURIComponent(`What does the ${f.name} screen do${f.moduleName ? ` in ${f.moduleName}` : ""}?`);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer cat-drawer" role="dialog" aria-modal="true" aria-labelledby="cat-d-title">
        <div className="drawer-head">
          <div className="cat-dhead">
            <h2 id="cat-d-title">{f.name}</h2>
            <div className="cat-meta">
              <Chip tone={READINESS_TONE[f.readiness]}>{READINESS[f.readiness]}</Chip>
              <Chip title={f.capability.needsReview ? "Grouping comes from a keyword taxonomy; not yet confirmed" : undefined}>
                {f.capability.name}
                {f.capability.needsReview ? " (candidate)" : ""}
              </Chip>
              <Chip>{f.category}</Chip>
              {f.group && <Chip>{f.group}</Chip>}
              {catalog.multi && <Chip tone="accent">{f.moduleName}</Chip>}
            </div>
          </div>
          <button ref={closeBtn} className="btn ghost small" type="button" aria-label="Close" onClick={onClose}>
            <X size={16} />
          </button>
        </div>

        <div className="drawer-body">
          <div className="note info cat-gap">{READINESS_HELP[f.readiness]}</div>
          {f.migrationGaps.length > 0 && (
            <div className="note cat-gap">
              <AlertTriangle size={14} aria-hidden="true" /> Some actions on this screen still point to legacy services that were not found in the current
              backend ({[...new Set(f.migrationGaps.map(g => g.kind || "unknown"))].join(", ")}). Check with product before promoting them.
            </div>
          )}

          {f.summary && (
            <section className="cat-block">
              <h3>
                Description <Mark status="REVIEW" /> AI draft
              </h3>
              <p className="cat-summary">{f.summary.text}</p>
            </section>
          )}

          <section className="cat-block">
            <h3>Verified highlights ({verified.length})</h3>
            {verified.length ? verified.map((x, i) => <ClaimRow key={i} claim={x} />) : <p className="muted cat-summary">None yet.</p>}
          </section>

          {review.length > 0 && (
            <section className="cat-block">
              <h3>Needs review ({review.length})</h3>
              {review.map((x, i) => (
                <ClaimRow key={i} claim={x} />
              ))}
            </section>
          )}

          <section className="cat-block">
            <h3>Explore further</h3>
            <div className="cat-links">
              <Link className="btn small" to={`/chat?q=${question}`}>
                <MessageSquare size={14} /> Ask PIE about it
              </Link>
              {f.screenId && (
                <Link className="btn small" to={`/fields?screen=${screen}`}>
                  <Table2 size={14} /> Fields
                </Link>
              )}
              {f.screenId && (
                <Link className="btn small" to={`/relations?screen=${screen}`}>
                  <Network size={14} /> Relationships
                </Link>
              )}
            </div>
          </section>

          <section className="cat-block">
            <details className="cat-tech">
              <summary>Technical details</summary>
              <div className="kv cat-kv">
                {t.componentPath && (
                  <>
                    <span>Screen</span>
                    <b>
                      <code>{t.componentPath}</code>
                    </b>
                  </>
                )}
                {t.formDefinitionPath && (
                  <>
                    <span>Form</span>
                    <b>
                      <code>{t.formDefinitionPath}</code>
                      {t.fieldCount != null && ` (${t.fieldCount} fields)`}
                    </b>
                  </>
                )}
                <span>Screen ID</span>
                <b>
                  <code>{f.screenId}</code>
                </b>
                {f.aliasScreenIds.length > 0 && (
                  <>
                    <span>Aliases</span>
                    <b>
                      <code>{f.aliasScreenIds.join(", ")}</code>
                    </b>
                  </>
                )}
              </div>
              {t.apis.length > 0 && (
                <div className="table-wrap cat-gap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Service</th>
                        <th>Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.apis.map((a, i) => (
                        <tr key={i}>
                          <td>
                            <code>
                              {a.method} {a.route}
                            </code>
                          </td>
                          <td>
                            {a.confidenceLevel || "—"}
                            {a.confidence != null && ` (${a.confidence})`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {t.tables.length > 0 && (
                <div className="table-wrap cat-gap">
                  <table className="table">
                    <thead>
                      <tr>
                        <th>Table</th>
                        <th>Confidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {t.tables.map((x, i) => (
                        <tr key={i}>
                          <td>
                            <code>
                              {x.schema ? `${x.schema}.` : ""}
                              {x.name}
                            </code>
                            {x.shared && <Chip className="cat-ml">platform</Chip>}
                          </td>
                          <td>{x.confidence ?? "—"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </details>
          </section>
        </div>

        <div className="cat-dfoot">
          <button className="btn primary" type="button" onClick={() => copy(blurb(f, catalog, includeAi), "Blurb")}>
            <Copy size={14} /> Copy verified blurb
          </button>
          <button className="btn" type="button" onClick={() => copy(shareLink(f), "Link")}>
            <Link2 size={14} /> Copy link
          </button>
          {f.summary && (
            <label className="check">
              <input type="checkbox" checked={includeAi} onChange={e => setIncludeAi(e.target.checked)} /> Include AI-drafted description
            </label>
          )}
        </div>
      </aside>
    </>
  );
}
