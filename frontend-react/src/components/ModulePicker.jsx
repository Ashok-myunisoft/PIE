// Choose one module, several, or all of them (an empty selection means all).
//   <ModulePicker options={[{value: "ess", label: "ESS", note: "verified"}]} selected={sel} onChange={setSel} />
import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, Link2, Search } from "lucide-react";
import { copyText } from "../lib/hooks.js";

function labelFor(selected, options, allLabel) {
  if (!selected.length) return allLabel;
  const name = v => (options.find(o => o.value === v) || { label: v }).label;
  return selected.length <= 2 ? selected.map(name).join(" + ") : `${name(selected[0])} + ${selected.length - 1} more`;
}

export default function ModulePicker({
  options = [],
  selected = [],
  onChange,
  allLabel = "All modules",
  title = "Modules",
  loading = false,
  shareable = true,
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [copied, setCopied] = useState("");
  const host = useRef(null);
  const search = useRef(null);

  // Close on outside click or Escape.
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = e => host.current && !host.current.contains(e.target) && setOpen(false);
    const onKey = e => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    setTimeout(() => search.current && search.current.focus(), 0);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const shown = useMemo(() => {
    const t = term.trim().toLowerCase();
    return options.filter(o => !t || o.label.toLowerCase().includes(t) || o.value.includes(t));
  }, [options, term]);

  function toggle(v) {
    const next = selected.includes(v) ? selected.filter(x => x !== v) : [...selected, v];
    // Keep the option-list order, so links and labels are stable.
    onChange(options.map(o => o.value).filter(x => next.includes(x)).concat(next.filter(x => !options.some(o => o.value === x))));
  }

  async function copyLink() {
    const ok = await copyText(window.location.href);
    setCopied(ok ? "Link copied" : "Copy failed");
    setTimeout(() => setCopied(""), 1500);
  }

  return (
    <div className="mp" ref={host}>
      <button
        type="button"
        className={`mp-btn${selected.length ? " active" : ""}`}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={`${title}: choose one, several or all`}
        onClick={() => setOpen(o => !o)}
      >
        <span className="mp-label">{labelFor(selected, options, allLabel)}</span>
        {selected.length > 1 && <span className="mp-n">{selected.length}</span>}
        <ChevronDown size={15} aria-hidden="true" />
      </button>
      {open && (
        <div className="mp-pop" role="dialog" aria-label={title}>
          <div className="mp-title">{title}</div>
          {options.length > 6 && (
            <label className="mp-search">
              <Search size={14} aria-hidden="true" />
              <input ref={search} type="search" placeholder="Find a module…" value={term} onChange={e => setTerm(e.target.value)} aria-label="Find a module" />
            </label>
          )}
          <div className="mp-actions">
            <button type="button" className={!selected.length ? "on" : ""} onClick={() => onChange([])}>
              {allLabel}
            </button>
            {selected.length > 0 && (
              <button type="button" onClick={() => onChange([])}>
                Clear selection
              </button>
            )}
          </div>
          <div className="mp-list" role="group" aria-label="Modules">
            {shown.map(o => {
              const on = selected.includes(o.value);
              return (
                <button type="button" key={o.value} className={`mp-item${on ? " on" : ""}`} aria-pressed={on} onClick={() => toggle(o.value)}>
                  <span className="mp-box">{on && <Check size={12} strokeWidth={3} />}</span>
                  <span className="mp-name">{o.label}</span>
                  {o.note && <small className={o.note === "verified" ? "ok" : ""}>{o.note === "verified" ? "✓ verified" : o.note}</small>}
                </button>
              );
            })}
            {!shown.length && (
              <div className="mp-empty">
                {loading ? "Loading modules from the PIE server…" : options.length ? "No module matches." : "No modules yet: waiting for the PIE server."}
              </div>
            )}
          </div>
          <div className="mp-foot">
            <span className="mp-sum">{selected.length ? `${selected.length} of ${options.length} selected` : `All ${options.length} modules`}</span>
            {shareable && (
              <button type="button" onClick={copyLink} title="Copy a link that opens this page with these modules">
                <Link2 size={13} /> {copied || "Copy link"}
              </button>
            )}
            <button type="button" className="primary" onClick={() => setOpen(false)}>
              Done
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
