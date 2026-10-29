// Field Explorer: the side panel with everything known about one field.
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Copy, Link2, MessageSquare, X } from "lucide-react";
import { Chip } from "../../components/ui.jsx";
import { fieldKey, json } from "./logic.js";

const NF = <span className="muted">Not found in the analysed sources</span>;
const has = v => v !== "" && v !== null && v !== undefined && v !== false;

/** Key/value list; rows whose value is empty are left out. */
function KV({ rows }) {
  const shown = rows.filter(([, v]) => has(v));
  if (!shown.length) return NF;
  return (
    <dl className="fx-kv">
      {shown.map(([k, v]) => (
        <div className="fx-kv-row" key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Block({ n, title, extra, children }) {
  return (
    <section className="fx-blk" aria-labelledby={`fx-blk-${n}`}>
      <h3 id={`fx-blk-${n}`}>
        <span className="fx-n">{n}</span> {title} {extra}
      </h3>
      {children}
    </section>
  );
}

const Code = ({ children }) => <code>{children}</code>;
const Src = ({ source, line }) => (
  <code>
    {source}
    {line ? ":" + line : ""}
  </code>
);

/** Tags shown in the table and the panel head. */
export function RuleTags({ r }) {
  return (
    <>
      {r.mandatory.required && <Chip tone="bad">{r.mandatory.conditional ? "Mandatory (conditional)" : "Mandatory"}</Chip>}
      {!r.mandatory.required && r.mandatory.conditional && <Chip tone="bad">Conditionally mandatory</Chip>}
      {r.calculation && <Chip tone="accent">Calculated</Chip>}
      {r.allowedValues.length > 0 && <Chip>{r.allowedValues.length} options</Chip>}
      {r.dependencies.length > 0 && <Chip>{r.dependencies.length} {r.dependencies.length === 1 ? "dependency" : "dependencies"}</Chip>}
      {(r.duplicateDefinition || r.labelNote) && <Chip tone="warn" title={[r.duplicateDefinition, r.labelNote].filter(Boolean).join(". ")}>note</Chip>}
    </>
  );
}

function List({ items, render }) {
  return (
    <ul className="fx-list">
      {items.map((x, i) => (
        <li key={i}>{render(x)}</li>
      ))}
    </ul>
  );
}

/** A long list that shows the first `max` items and the rest on demand. */
function MoreList({ items, max = 10, render }) {
  if (items.length <= max) return <List items={items} render={render} />;
  return (
    <>
      <List items={items.slice(0, max)} render={render} />
      <details className="fx-more">
        <summary>Show {items.length - max} more</summary>
        <List items={items.slice(max)} render={render} />
      </details>
    </>
  );
}

export default function FieldDrawer({ field: r, dict, byName, onOpenField, onClose, onCopySummary, onCopyLink }) {
  const closeRef = useRef(null);
  const bodyRef = useRef(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  // Escape closes; focus goes to the close button and back to where it was afterwards.
  useEffect(() => {
    const before = document.activeElement;
    const onKey = e => e.key === "Escape" && onCloseRef.current();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (before && before.focus && document.contains(before)) before.focus();
    };
  }, []);
  useEffect(() => {
    if (closeRef.current) closeRef.current.focus();
    if (bodyRef.current) bodyRef.current.scrollTop = 0;
  }, [r]);

  const screenName = dict.screen || "";
  const m = r.mandatory, db = r.database, fe = r.frontend, be = r.backend, u = r.usage, e = r.explanation;

  // A field name that exists on this screen becomes a button that opens it.
  const FieldRef = ({ name }) => {
    const t = byName.get(name);
    return t ? (
      <span className="fx-ref">
        <button type="button" className="fx-link" onClick={() => onOpenField(fieldKey(t))}>
          {t.label}
        </button>{" "}
        <code>{name}</code>
      </span>
    ) : (
      <code>{name}</code>
    );
  };
  const joinRefs = names =>
    names.map((n, i) => (
      <span key={n + i}>
        {i > 0 && ", "}
        <FieldRef name={n} />
      </span>
    ));

  const mandatory = (
    <>
      {m.required ? "Yes" : "No"}
      {m.conditional && (
        <>
          {" "}— only when <FieldRef name={m.conditional.field} /> is <code>{json(m.conditional.values)}</code>
        </>
      )}
      {m.backendValidated && " · validated by the backend"}
      {m.databaseNotNull && " · database column NOT NULL"}
    </>
  );

  const defaultValue =
    r.defaultValue === null || r.defaultValue === undefined ? NF : r.defaultValue === "" ? (
      <>
        empty text <span className="muted">(defined as "" in the form)</span>
      </>
    ) : (
      <code>{json(r.defaultValue)}</code>
    );

  const maxLen = r.maxLength || {};
  const maxLength = [maxLen.form != null && `${maxLen.form} on the screen`, maxLen.database != null && `${maxLen.database} in the database`].filter(Boolean).join(" · ");
  const sv = db.storedVia;
  const lk = r.lookup;
  const askText = `Tell me everything about the ${r.label} (${r.fieldName}) field in ${screenName}`;

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="drawer fx-drawer" role="dialog" aria-modal="true" aria-labelledby="fx-d-title">
        <div className="drawer-head">
          <div className="fx-dhead-main">
            <h2 id="fx-d-title">{r.label}</h2>
            <div className="fx-dmeta">
              <code>{r.fieldName}</code>
              <span className="muted">
                {" · "}
                {[screenName, r.page, r.section].filter(Boolean).join(" › ")}
              </span>
              <div className="fx-tags">
                <RuleTags r={r} />
              </div>
            </div>
          </div>
          <button ref={closeRef} className="btn small ghost" type="button" onClick={onClose} aria-label="Close field details">
            <X size={16} />
          </button>
        </div>

        <div className="drawer-body fx-dbody" ref={bodyRef}>
          {(r.duplicateDefinition || r.labelNote) && <div className="note fx-gap">{[r.duplicateDefinition, r.labelNote].filter(Boolean).join(". ")}.</div>}

          <Block n="1" title="Identity">
            <KV
              rows={[
                ["Field name", r.label],
                ["Label key", r.labelKey ? (<><code>{r.labelKey}</code> <span className="muted">({r.labelSource})</span></>) : r.labelSource ? <span className="muted">{r.labelSource}</span> : ""],
                ["Technical name", <Code>{r.fieldName}</Code>],
                ["Screen / tab / section", [screenName, r.page, r.section].filter(Boolean).join(" › ")],
                ["Visible on screen", r.visibleOnScreen ? "Yes" : "No (hidden or system field)"],
              ]}
            />
          </Block>

          <Block
            n="2"
            title="Meaning"
            extra={
              <>
                <Chip tone="warn">AI-derived</Chip>
                {e && e.confidence && <Chip>{e.confidence} confidence</Chip>}
              </>
            }
          >
            {e ? (
              <div className="fx-ai">
                <KV
                  rows={[
                    ["Purpose", e.purpose],
                    ["What it represents", e.businessMeaning],
                    ["Business usage", e.businessUsage],
                    ["Impact on transactions", e.transactionImpact],
                    ["Example", e.example],
                    ["Business rules", e.businessRules],
                    ["Mentions not found in the sources", Array.isArray(e.unverifiedMentions) && e.unverifiedMentions.length ? e.unverifiedMentions.join(", ") : ""],
                  ]}
                />
                {e.reviewNote && (
                  <div className="note fx-gap-top">
                    <b>Review:</b> {e.reviewNote}.
                  </div>
                )}
              </div>
            ) : (
              NF
            )}
          </Block>

          <Block n="3" title="Type and values">
            <KV
              rows={[
                ["Field type (screen)", r.type.ui],
                ["Form type", r.type.form],
                ["Backend type", r.type.backend ? <Code>{r.type.backend}</Code> : NF],
                ["Database type", r.type.database ? <Code>{r.type.database}</Code> : NF],
                ["Maximum length", maxLength],
                [
                  "Allowed values",
                  r.allowedValues.length ? (
                    <div className="fx-tags">
                      {r.allowedValues.map((v, i) => (
                        <Chip key={i}>
                          {String(v.value)} = {v.label}
                        </Chip>
                      ))}
                    </div>
                  ) : (
                    NF
                  ),
                ],
                ["Default value", defaultValue],
                ["Placeholder", fe.placeholder],
              ]}
            />
          </Block>

          <Block n="4" title="Mandatory, validations and messages">
            <KV
              rows={[
                ["Mandatory", mandatory],
                [
                  "Validation rules",
                  r.validations.length ? (
                    <List items={r.validations} render={v => (<><Chip>{v.layer}</Chip> {v.rule}</>)} />
                  ) : (
                    NF
                  ),
                ],
                [
                  "Error / validation messages",
                  r.messages.length ? (
                    <List items={r.messages} render={x => (<>“{x.message}” <span className="muted">({x.layer})</span></>)} />
                  ) : (
                    NF
                  ),
                ],
              ]}
            />
          </Block>

          <Block n="5" title="Dependencies and calculation">
            <KV
              rows={[
                [
                  "Calculation / formula",
                  r.calculation ? (
                    <>
                      <code>{r.calculation.formula}</code> {r.calculation.readOnly && <Chip>read-only</Chip>}
                    </>
                  ) : (
                    NF
                  ),
                ],
                [
                  "Dependencies",
                  r.dependencies.length ? (
                    <List
                      items={r.dependencies}
                      render={x => (
                        <>
                          <b>{x.kind}</b> {joinRefs(x.fields)} <span className="muted">{(x.detail || "").slice(0, 160)}</span>
                        </>
                      )}
                    />
                  ) : (
                    NF
                  ),
                ],
                ["Configuration dependency", r.configuration.length ? <List items={r.configuration} render={c => c} /> : NF],
                ["Related fields", r.relatedFields.length ? <span>{joinRefs(r.relatedFields)}</span> : NF],
              ]}
            />
            {r.handlerLogic.map((h, i) => (
              <details className="fx-code" key={i}>
                <summary>
                  Screen logic: {h.event} → {h.handler}() {h.line ? `(line ${h.line})` : ""}
                  {h.updates.length ? ` updates ${h.updates.join(", ")}` : ""}
                </summary>
                <pre>{h.code}</pre>
              </details>
            ))}
          </Block>

          <Block n="6" title="Data source and database">
            <KV
              rows={[
                ["Storage", db.storage],
                [
                  "Database table.column",
                  db.column ? (
                    <>
                      <code>
                        {db.table}.{db.column}
                      </code>{" "}
                      {db.primaryKey && <Chip>primary key</Chip>}
                    </>
                  ) : (
                    NF
                  ),
                ],
                ["Nullable", db.nullable === null || db.nullable === undefined ? "" : db.nullable ? "Yes" : "No"],
                ["Foreign key", db.foreignKey ? <Code>{`${db.foreignKey.table}.${db.foreignKey.column}`}</Code> : ""],
                ["Shown from", db.lookupDisplay ? (<><Code>{`${db.lookupDisplay.table}.${db.lookupDisplay.column}`}</Code> (related master)</>) : ""],
                [
                  "Value kept in",
                  sv && sv.field ? (
                    <>
                      <FieldRef name={sv.field} />
                      {sv.table && sv.column ? (<> → <Code>{`${sv.table}.${sv.column}`}</Code></>) : ""}
                      {sv.basis ? <span className="muted"> ({sv.basis})</span> : ""}
                    </>
                  ) : (
                    ""
                  ),
                ],
                ["Grid rows saved to", db.childTable ? (<><Code>{db.childTable.table}</Code>{db.childTable.query ? ` via ${db.childTable.query}` : ""}</>) : ""],
                [
                  "Lookup / data source",
                  lk && lk.picklist ? (
                    <>
                      picklist <code>{lk.picklist}</code> → <code>{lk.source || "?"}</code>
                      {lk.displayColumns ? ` showing ${lk.displayColumns}` : ""}
                      {lk.criteria ? ` · criteria ${lk.criteria}` : ""}
                      {lk.allowAddNew ? " · add new allowed" : ""}
                      {lk.multiple ? " · several values allowed" : ""}
                    </>
                  ) : (
                    ""
                  ),
                ],
                [
                  "Grid columns",
                  fe.grid.length ? (
                    <div className="fx-tags">
                      {fe.grid.map((g, i) => (
                        <Chip key={i}>{g.label || g.column}</Chip>
                      ))}
                    </div>
                  ) : (
                    ""
                  ),
                ],
              ]}
            />
          </Block>

          <Block n="7" title="Backend and frontend mapping">
            <KV
              rows={[
                [
                  "Backend DTO",
                  be.dto ? (
                    <>
                      <code>{be.dto}</code>{" "}
                      <span className="muted">
                        {be.dtoFile}
                        {be.dtoLine ? ":" + be.dtoLine : ""}
                      </span>
                    </>
                  ) : (
                    NF
                  ),
                ],
                ["Queries", be.queries.length ? <div className="fx-codes">{be.queries.map((q, i) => <code key={i}>{q}</code>)}</div> : NF],
                [
                  "API endpoints",
                  be.endpoints.length ? (
                    <div className="fx-codes">
                      {be.endpoints.map((x, i) => (
                        <code key={i} title={x.file || ""}>
                          {x.method} {x.route}
                        </code>
                      ))}
                    </div>
                  ) : (
                    NF
                  ),
                ],
                ["Business logic", be.bll ? <Code>{be.bll}</Code> : ""],
                [
                  "Frontend control",
                  fe.control ? (
                    <>
                      <code>&lt;{fe.control}&gt;</code> in <code>{fe.component}</code>
                      {fe.htmlLine ? ` (template line ${fe.htmlLine})` : ""}
                    </>
                  ) : (
                    NF
                  ),
                ],
                [
                  "Screen events",
                  fe.events.length ? (
                    <div className="fx-codes">
                      {fe.events.map((x, i) => (
                        <code key={i}>
                          ({x.event}) → {x.handler}()
                        </code>
                      ))}
                    </div>
                  ) : (
                    ""
                  ),
                ],
                ["Form definition", fe.formDefinition ? <Code>{fe.formDefinition}</Code> : ""],
                ["Meta form", fe.metaForm ? <Code>{fe.metaForm}</Code> : ""],
                ["Posted on save", fe.postData === undefined || fe.postData === null ? "" : fe.postData ? "Yes" : "No"],
              ]}
            />
          </Block>

          <Block n="8" title="Usage across the ERP">
            <KV
              rows={[
                ["Related masters", r.relatedMasters.length ? <div className="fx-tags">{r.relatedMasters.map((x, i) => <Chip key={i}>{x}</Chip>)}</div> : NF],
                [
                  "Modules using it",
                  Object.keys(u.modules).length ? (
                    <div className="fx-tags">
                      {Object.entries(u.modules).map(([k, v]) => (
                        <Chip key={k} title={`${v} references in ${k}`}>
                          {k} · {v}
                        </Chip>
                      ))}
                    </div>
                  ) : (
                    NF
                  ),
                ],
                ["Reports / documents", u.reports.length ? <MoreList items={u.reports} render={x => <code>{x}</code>} /> : NF],
                [
                  "Other screens",
                  u.screens.length ? (
                    <div className="fx-tags">
                      {u.screens.slice(0, 15).map((x, i) => (
                        <Chip key={i}>{x}</Chip>
                      ))}
                      {u.screens.length > 15 && <span className="muted">and {u.screens.length - 15} more</span>}
                    </div>
                  ) : (
                    NF
                  ),
                ],
                [
                  "Sample backend references",
                  u.backendReferences.length ? (
                    <MoreList items={u.backendReferences} render={x => (<><Src source={x.source} line={x.line} /> <span className="muted">{x.fact}</span></>)} />
                  ) : (
                    ""
                  ),
                ],
                [
                  "Sample frontend references",
                  u.frontendReferences.length ? (
                    <MoreList items={u.frontendReferences} max={5} render={x => (<><Src source={x.source} line={x.line} /> <span className="muted">{x.fact}</span></>)} />
                  ) : (
                    ""
                  ),
                ],
              ]}
            />
          </Block>

          <Block n="9" title="Source evidence">
            {r.evidence.length ? (
              <List items={r.evidence} render={x => (<><Src source={x.source} line={x.line} /> <span className="muted">{x.fact}</span></>)} />
            ) : (
              NF
            )}
          </Block>
        </div>

        <div className="fx-dfoot">
          <Link className="btn primary" to={`/chat?q=${encodeURIComponent(askText)}`}>
            <MessageSquare size={15} /> Ask PIE about this field
          </Link>
          <button className="btn" type="button" onClick={onCopySummary}>
            <Copy size={15} /> Copy summary
          </button>
          <button className="btn" type="button" onClick={onCopyLink}>
            <Link2 size={15} /> Copy link
          </button>
        </div>
      </aside>
    </>
  );
}
