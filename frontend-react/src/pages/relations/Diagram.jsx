// The chain diagram: screen -> APIs -> backend endpoints -> database tables, with curved
// wires between the boxes. Hover, focus or click a box to follow its links up and down the chain.
import { useLayoutEffect, useMemo, useRef, useState } from "react";

/** Every node connected to `id`, following links downwards and upwards. */
function connected(id, edges) {
  const down = new Set([id]);
  const up = new Set([id]);
  let grow = true;
  while (grow) {
    grow = false;
    for (const e of edges) {
      if (down.has(e.from) && !down.has(e.to)) {
        down.add(e.to);
        grow = true;
      }
      if (up.has(e.to) && !up.has(e.from)) {
        up.add(e.from);
        grow = true;
      }
    }
  }
  return new Set([...down, ...up]);
}

/** The diagram's edges, without duplicates. Candidate links (inferred, not observed) are dashed. */
export function buildEdges(data, tables) {
  const byName = new Map(tables.map(t => [t.name, t]));
  const edges = new Map();
  const add = (from, to, cand) => edges.set(`${from}>${to}`, { from, to, cand });
  for (const a of data.apis || []) {
    add("S", a.id, a.status !== "OBSERVED");
    for (const ep of a.endpoints || []) {
      add(a.id, ep.id, ep.status !== "OBSERVED");
      for (const tn of ep.tables || []) {
        const t = byName.get(tn);
        if (t) add(ep.id, t.id, !(t.statuses || []).includes("OBSERVED"));
      }
    }
  }
  return [...edges.values()];
}

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

export default function Diagram({ data, tables, onTable }) {
  const s = data.screen || {};
  const cols = useRef(null);
  const [paths, setPaths] = useState([]);
  const [hover, setHover] = useState(null);
  const [pinned, setPinned] = useState(null);

  const endpoints = useMemo(() => {
    const seen = new Map();
    for (const a of data.apis || []) for (const ep of a.endpoints || []) if (!seen.has(ep.id)) seen.set(ep.id, ep);
    return [...seen.values()];
  }, [data]);
  const edges = useMemo(() => buildEdges(data, tables), [data, tables]);

  // Measure the boxes and draw the wires; again whenever the layout changes size.
  useLayoutEffect(() => {
    const host = cols.current;
    if (!host) return undefined;
    const draw = () => {
      const base = host.getBoundingClientRect();
      const at = id => host.querySelector(`[data-node="${CSS.escape(id)}"]`);
      setPaths(
        edges
          .map(e => {
            const a = at(e.from);
            const b = at(e.to);
            if (!a || !b) return null;
            const ra = a.getBoundingClientRect();
            const rb = b.getBoundingClientRect();
            const x1 = ra.right - base.left;
            const y1 = ra.top + ra.height / 2 - base.top;
            const x2 = rb.left - base.left;
            const y2 = rb.top + rb.height / 2 - base.top;
            const dx = Math.max(24, (x2 - x1) / 2);
            return { ...e, d: `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}` };
          })
          .filter(Boolean)
      );
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(host);
    return () => ro.disconnect();
  }, [edges]);

  // Reset the highlight when another screen or table filter is shown.
  useLayoutEffect(() => {
    setPinned(null);
    setHover(null);
  }, [data, tables]);

  const active = pinned || hover;
  const on = useMemo(() => (active ? connected(active, edges) : null), [active, edges]);
  const cls = (base, id) => `rel-node ${base}${on && on.has(id) ? " on" : ""}${pinned === id ? " pinned" : ""}`;

  const nodeProps = (id, extra) => ({
    type: "button",
    "data-node": id,
    "aria-pressed": pinned === id,
    onMouseEnter: () => setHover(id),
    onMouseLeave: () => setHover(h => (h === id ? null : h)),
    onFocus: () => setHover(id),
    onBlur: () => setHover(h => (h === id ? null : h)),
    onClick: () => {
      setPinned(p => (p === id ? null : id));
      if (extra) extra();
    },
  });

  return (
    <div className={`rel-diagram${on ? " focus" : ""}`}>
      <div className="rel-cols" ref={cols}>
        <svg className="rel-wires" aria-hidden="true" width="100%" height="100%">
          {paths.map(p => (
            <path
              key={`${p.from}>${p.to}`}
              d={p.d}
              className={`${p.cand ? "cand" : ""}${on && on.has(p.from) && on.has(p.to) ? " on" : ""}`}
            />
          ))}
        </svg>
        <div className="rel-col">
          <h4>Screen</h4>
          <button {...nodeProps("S")} className={cls("screen", "S")}>
            <b>{s.label || s.screenId}</b>
            <small>
              {s.module}
              {data.fieldDictionary ? ` · form ${data.fieldDictionary.form} · ${data.fieldDictionary.fields} fields` : ""}
            </small>
          </button>
        </div>
        <div className="rel-col">
          <h4>APIs called ({(data.apis || []).length})</h4>
          {(data.apis || []).map(a => (
            <button key={a.id} {...nodeProps(a.id)} className={cls("api", a.id)}>
              <b>
                {a.method} {a.route}
              </b>
              <small>
                {(a.status || "").toLowerCase()} · {Math.round((a.confidence || 0) * 100)}% sure
              </small>
            </button>
          ))}
          {!(data.apis || []).length && <p className="muted small">No API found.</p>}
        </div>
        <div className="rel-col">
          <h4>Backend ({endpoints.length})</h4>
          {endpoints.map(ep => (
            <button key={ep.id} {...nodeProps(ep.id)} className={cls("backend", ep.id)}>
              <b>{ep.name}</b>
              <small>{(ep.services || []).map(x => x.name).join(", ") || "no service"}</small>
            </button>
          ))}
          {!endpoints.length && <p className="muted small">No backend endpoint found.</p>}
        </div>
        <div className="rel-col">
          <h4>Database ({tables.length})</h4>
          {tables.map(t => (
            <button key={t.id} {...nodeProps(t.id, () => onTable(t.name))} className={cls("table", t.id)}>
              <span className="rel-ops" title={(t.operations || []).join(" and ")}>
                {(t.operations || []).map(o => o[0]).join("")}
              </span>
              <b>{t.name}</b>
              <small>
                {(t.fields || []).length ? `${plural(t.fields.length, "field")} of this screen` : t.objectType || ""}
                {t.alsoUsedByCount ? ` · ${plural(t.alsoUsedByCount, "other screen")}` : ""}
              </small>
            </button>
          ))}
          {!tables.length && <p className="muted small">No database object found.</p>}
        </div>
      </div>
    </div>
  );
}
