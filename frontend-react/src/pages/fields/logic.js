// Field Explorer: pure helpers (no React). Field records come from /usage/api/fields/{screen}.

const obj = v => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
const arr = v => (Array.isArray(v) ? v : []);

/** Fill in missing parts so the rest of the page can read every record safely. */
export function normalizeField(r) {
  const usage = obj(r.usage);
  const backend = obj(r.backend);
  const frontend = obj(r.frontend);
  return {
    ...r,
    label: r.label || r.fieldName || r.fieldKey || "(no label)",
    fieldName: r.fieldName || r.fieldKey || "",
    type: obj(r.type),
    mandatory: obj(r.mandatory),
    maxLength: obj(r.maxLength),
    database: obj(r.database),
    lookup: r.lookup && typeof r.lookup === "object" ? r.lookup : null,
    allowedValues: arr(r.allowedValues),
    validations: arr(r.validations),
    messages: arr(r.messages),
    dependencies: arr(r.dependencies).map(d => ({ ...d, fields: arr(d && d.fields) })),
    handlerLogic: arr(r.handlerLogic).map(h => ({ ...h, updates: arr(h && h.updates) })),
    configuration: arr(r.configuration),
    relatedFields: arr(r.relatedFields),
    relatedMasters: arr(r.relatedMasters),
    evidence: arr(r.evidence),
    backend: { ...backend, queries: arr(backend.queries), endpoints: arr(backend.endpoints) },
    frontend: { ...frontend, events: arr(frontend.events), grid: arr(frontend.grid) },
    usage: {
      ...usage,
      modules: obj(usage.modules),
      reports: arr(usage.reports),
      screens: arr(usage.screens),
      backendReferences: arr(usage.backendReferences),
      frontendReferences: arr(usage.frontendReferences),
    },
  };
}

/** Stable identifier of a field inside its screen (used in ?field=). */
export const fieldKey = r => r.fieldKey || r.fieldName;

/** Tab, else section, else main form / hidden (ESS screens use sections instead of tabs). */
export const where = r => r.page || r.section || (r.visibleOnScreen ? "Main form" : "Not shown on screen");

/** "Show only" filters: [key, label, test]. */
export const FLAGS = [
  ["mandatory", "Mandatory", r => !!r.mandatory.required],
  ["conditional", "Conditionally mandatory", r => !!r.mandatory.conditional],
  ["calculated", "Calculated", r => !!r.calculation],
  ["lookup", "Lookup (picklist)", r => !!(r.lookup && r.lookup.picklist)],
  ["options", "Fixed options", r => r.allowedValues.length > 0],
  ["deps", "Has dependencies", r => r.dependencies.length > 0],
  ["stored", "Stored in main table", r => r.database.storage === "column"],
  ["lookupdb", "Shown from related master", r => r.database.storage === "related master (display)"],
  ["notstored", "Not stored", r => /not stored/.test(r.database.storage || "")],
  ["used", "Used by other modules", r => Object.keys(r.usage.modules).length > 0],
  ["hidden", "Hidden / system", r => !r.visibleOnScreen || r.type.ui === "Hidden / system field"],
  ["review", "AI text needs review", r => !!(r.explanation && r.explanation.reviewNote)],
  ["conflict", "Source conflict / label note", r => !!(r.duplicateDefinition || r.labelNote)],
];

/** Lower-case text searched by the search box. */
export function haystack(r) {
  return [
    r.label, r.fieldName, r.labelKey, r.database.column, r.page, r.section, r.type.ui,
    (r.database.lookupDisplay || {}).table, (r.lookup || {}).source,
  ].filter(Boolean).join(" ").toLowerCase();
}

/** Filter fields by tab, flags and search words (every word must appear). */
export function filterFields(fields, { page, flags, q }) {
  const tests = FLAGS.filter(f => flags.includes(f[0])).map(f => f[2]);
  const words = (q || "").toLowerCase().split(/\s+/).filter(Boolean);
  return fields.filter(r => {
    if (page && where(r) !== page) return false;
    for (const t of tests) if (!t(r)) return false;
    if (words.length) {
      const hay = r._hay;
      for (const w of words) if (!hay.includes(w)) return false;
    }
    return true;
  });
}

/** Where the value is kept, as plain text (CSV, summary). */
export function storedInText(r) {
  const db = r.database;
  if (db.column) return `${db.table}.${db.column}`;
  if (db.lookupDisplay) return `${db.lookupDisplay.table}.${db.lookupDisplay.column}`;
  if (db.childTable) return `${db.childTable.table} (grid rows)`;
  return db.storage || "";
}

const json = v => {
  try {
    return JSON.stringify(v);
  } catch (e) {
    return String(v);
  }
};

/** Plain-text summary for "Copy summary". */
export function summaryText(r, screenName) {
  const m = r.mandatory, db = r.database, e = r.explanation || {};
  return [
    `${r.label} (${r.fieldName}) — ${screenName}${r.page ? ", " + r.page : ""}`,
    `Type: ${r.type.ui || r.type.form || "unknown"}${r.type.database ? " / " + r.type.database : ""}`,
    `Mandatory: ${m.required ? "Yes" : "No"}${m.conditional ? " (when " + m.conditional.field + " = " + json(m.conditional.values) + ")" : ""}`,
    r.allowedValues.length ? "Allowed values: " + r.allowedValues.map(v => v.value + "=" + v.label).join(", ") : "",
    r.validations.length ? "Validations: " + r.validations.map(v => v.rule).join("; ") : "",
    r.calculation ? "Formula: " + r.calculation.formula : "",
    db.column ? `Stored in ${db.table}.${db.column}` : db.lookupDisplay ? `Shown from ${db.lookupDisplay.table}.${db.lookupDisplay.column}` : "Storage: " + (db.storage || "unknown"),
    e.purpose ? "Purpose (AI-derived): " + e.purpose : "",
  ].filter(Boolean).join("\n");
}

/** Rows for "Download CSV (filtered)": header first. */
export function csvRows(list) {
  const rows = [["#", "Field", "Technical name", "Tab", "Section", "Type", "DB type", "Mandatory", "Allowed values", "Default", "Validations", "Formula", "Stored in", "Lookup source", "Used by modules", "Purpose (AI-derived)"]];
  list.forEach(r =>
    rows.push([
      r.order, r.label, r.fieldName, r.page, r.section, r.type.ui, r.type.database,
      r.mandatory.required ? "Yes" : "No",
      r.allowedValues.map(v => v.value + "=" + v.label).join("; "),
      r.defaultValue == null ? "" : typeof r.defaultValue === "object" ? json(r.defaultValue) : r.defaultValue,
      r.validations.map(v => v.rule).join("; "),
      r.calculation ? r.calculation.formula : "",
      storedInText(r),
      (r.lookup || {}).source || "",
      Object.keys(r.usage.modules).join(", "),
      (r.explanation || {}).purpose || "",
    ])
  );
  return rows;
}

/** Screens grouped by module (sorted), each group's screens sorted by name. */
export function groupScreens(screens) {
  const by = {};
  screens.forEach(s => (by[s.module || "other"] = by[s.module || "other"] || []).push(s));
  return Object.keys(by)
    .sort()
    .map(m => [m, by[m].slice().sort((a, b) => String(a.screenName || a.screen).localeCompare(String(b.screenName || b.screen)))]);
}

export { json };
