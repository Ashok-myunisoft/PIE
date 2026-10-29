// Display helpers shared by the pages.

/** "ess" -> "ESS", "inventory" -> "Inventory" (short codes are acronyms). */
export const moduleLabel = m => (!m ? "" : m.length <= 4 ? m.toUpperCase() : m.charAt(0).toUpperCase() + m.slice(1));

/** Module options for <ModulePicker>, verified ones first and marked. */
export function moduleOptions(modules, verified = [], names = {}) {
  const ordered = [...verified, ...modules.filter(m => !verified.includes(m))];
  return [...new Set(ordered)].map(m => ({
    value: m,
    label: names[m] || moduleLabel(m),
    note: verified.includes(m) ? "verified" : "",
  }));
}

export const num = v => (v == null || v === "" ? "—" : Number(v).toLocaleString());

/** Time today ("14:05"), else a short date ("3 Oct"). */
export function when(t) {
  const d = new Date(t);
  if (Number.isNaN(d.getTime())) return "";
  const same = d.toDateString() === new Date().toDateString();
  return same
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : d.toLocaleDateString([], { day: "numeric", month: "short" });
}

export function dateTime(t) {
  const d = new Date(t);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

/** "Today", "Yesterday", "Previous 7 days", ... for grouping lists by date. */
export function bucket(t) {
  const d = new Date(t);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.floor((today - new Date(d.getFullYear(), d.getMonth(), d.getDate())) / 864e5);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : days < 7 ? "Previous 7 days" : days < 30 ? "Previous 30 days" : "Older";
}

/** "2 minutes ago" style. */
export function ago(t) {
  const s = Math.round((Date.now() - new Date(t).getTime()) / 1000);
  if (Number.isNaN(s)) return "";
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
}

/** CSV text from a header row and data rows. */
export function toCsv(rows) {
  const cell = v => {
    const s = v == null ? "" : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  return "﻿" + rows.map(r => r.map(cell).join(",")).join("\r\n");
}
