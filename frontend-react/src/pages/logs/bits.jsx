// Small pieces shared by the Activity Log tabs.
import { Chip, ErrorState, Loading } from "../../components/ui.jsx";
import { num } from "../../lib/format.js";
import { toneOf } from "./logsApi.js";

export function Badge({ status }) {
  if (!status) return null;
  return <Chip tone={toneOf(status)}>{String(status).replace(/_/g, " ")}</Chip>;
}

/** A table row that opens something; works with mouse, Enter and Space. */
export function ClickRow({ onOpen, label, className = "", children }) {
  return (
    <tr
      className={`clickable ${className}`}
      tabIndex={0}
      aria-label={label}
      onClick={onOpen}
      onKeyDown={e => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      {children}
    </tr>
  );
}

/** Wraps a table with header cells; heads starting with "#" are right-aligned numbers. */
export function LogTable({ head, empty = "Nothing recorded yet.", children, rows }) {
  return (
    <div className="table-wrap">
      <table className="table logs-table">
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={i} className={h.startsWith("#") ? "num" : ""}>
                {h.replace(/^#/, "")}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {children}
          {!rows && (
            <tr>
              <td colSpan={head.length} className="muted">
                {empty}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/** Loading / error placeholder for a tab whose data has not arrived. Returns null when data is there. */
export function TabState({ res, what }) {
  if (res.data) return null;
  if (res.error) return <ErrorState error={res.error} onRetry={res.reload} title={`Could not load ${what}`} />;
  return <Loading>Loading {what}…</Loading>;
}

/** Refresh errors while older data is still shown. */
export function StaleError({ res }) {
  if (!res.data || !res.error) return null;
  return (
    <p className="note logs-gap" role="alert">
      Could not refresh: {res.error.message}
    </p>
  );
}

/** "retrievedItems: 40 · citedItems: 16" from a counts object (first four non-zero values). */
export function Headline({ counts }) {
  const parts = Object.entries(counts || {}).filter(([, v]) => v).slice(0, 4);
  return parts.map(([k, v], i) => (
    <span key={k}>
      {i > 0 && " · "}
      {k}: <b>{num(v)}</b>
    </span>
  ));
}
