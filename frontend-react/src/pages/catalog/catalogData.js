// Data helpers for the Feature Catalog page: cleaning up API responses, merging several
// module catalogs into one view, filtering, ranking, the copy blurb and the CSV sheet.
import { moduleLabel } from "../../lib/format.js";

export const READINESS = {
  READY: "Marketing-ready",
  PARTIAL: "Partially verified",
  NEEDS_EVIDENCE: "Needs evidence",
};
export const READINESS_HELP = {
  READY: "Confirmed screen with at least two verified highlights.",
  PARTIAL: "At least one verified highlight; more evidence would strengthen it.",
  NEEDS_EVIDENCE: "No verified highlight yet. Do not market without product input.",
};
export const READINESS_TONE = { READY: "ok", PARTIAL: "warn", NEEDS_EVIDENCE: "bad" };

const arr = v => (Array.isArray(v) ? v : []);
const obj = v => (v && typeof v === "object" && !Array.isArray(v) ? v : {});
const n = v => (typeof v === "number" && Number.isFinite(v) ? v : 0);

/** Fill in missing fields so the page never trips over a partial catalog. */
export function normalizeCatalog(raw, module) {
  const c = obj(raw);
  const moduleName = c.moduleName || moduleLabel(c.module || module);
  const features = arr(c.features).map(f => {
    const claims = arr(f.claims).map(x => ({ ...x, text: x.text || "", evidence: arr(x.evidence) }));
    const cap = obj(f.capability);
    const t = obj(f.technical);
    return {
      ...f,
      screenId: f.screenId || "",
      name: f.name || f.screenId || "Unnamed screen",
      category: f.category || "Other",
      readiness: READINESS[f.readiness] ? f.readiness : "NEEDS_EVIDENCE",
      capability: { ...cap, name: cap.name || "Uncategorised" },
      aliasScreenIds: arr(f.aliasScreenIds),
      claims,
      verifiedClaims: f.verifiedClaims ?? claims.filter(x => x.status === "VERIFIED").length,
      reviewClaims: f.reviewClaims ?? claims.filter(x => x.status !== "VERIFIED").length,
      migrationGaps: arr(f.migrationGaps),
      summary: f.summary && f.summary.text ? f.summary : null,
      technical: { ...t, apis: arr(t.apis), tables: arr(t.tables) },
      module: c.module || module,
      moduleName,
    };
  });
  return {
    ...c,
    module: c.module || module,
    moduleName,
    product: c.product || "",
    capabilities: arr(c.capabilities).map(cp => ({
      ...cp,
      name: cp.name || "Uncategorised",
      categories: obj(cp.categories),
      features: n(cp.features),
      ready: n(cp.ready),
      partial: n(cp.partial),
      needsEvidence: n(cp.needsEvidence),
      verifiedClaims: n(cp.verifiedClaims),
    })),
    features,
    statistics: obj(c.statistics),
  };
}

/** One catalog, or several merged into one view. Capability names shared by modules get the module name added. */
export function combine(list) {
  if (!list.length) return null;
  if (list.length === 1) return { ...list[0], multi: false };
  const count = {};
  list.forEach(c => c.capabilities.forEach(cp => (count[cp.name] = (count[cp.name] || 0) + 1)));
  const capName = (c, name) => (count[name] > 1 ? `${name} (${c.moduleName})` : name);
  const sum = k => list.reduce((t, c) => t + n(c.statistics[k]), 0);
  return {
    product: list[0].product,
    module: list.map(c => c.module).join(","),
    moduleName: list.map(c => c.moduleName).join(" + "),
    generatedAt: list.map(c => c.generatedAt || "").sort().pop(),
    multi: true,
    capabilities: list.flatMap(c => c.capabilities.map(cp => ({ ...cp, name: capName(c, cp.name), module: c.module }))),
    features: list.flatMap(c => c.features.map(f => ({ ...f, capability: { ...f.capability, name: capName(c, f.capability.name) } }))),
    statistics: {
      features: sum("features"),
      capabilities: sum("capabilities"),
      ready: sum("ready"),
      verifiedClaims: sum("verifiedClaims"),
      reviewClaims: sum("reviewClaims"),
      aiDraftsToReview: sum("aiDraftsToReview"),
    },
  };
}

/** Does a feature pass the current filters? */
export function matches(f, { capability, category, readiness, readyOnly, q }) {
  if (capability && f.capability.name !== capability) return false;
  if (category && f.category !== category) return false;
  if (readiness && f.readiness !== readiness) return false;
  if (readyOnly && f.readiness !== "READY") return false;
  if (q) {
    const hay = [f.name, f.capability.name, f.category, f.group, f.screenId, f.summary && f.summary.text, ...f.claims.map(c => c.text)]
      .join(" ")
      .toLowerCase();
    if (!q.toLowerCase().split(/\s+/).filter(Boolean).every(w => hay.includes(w))) return false;
  }
  return true;
}

/** Search ranking: exact name, then name contains the query, then all words in the name, then the rest. */
export function rankByQuery(list, q) {
  if (!q) return list;
  const query = q.toLowerCase();
  const words = query.split(/\s+/).filter(Boolean);
  const rank = f => {
    const name = f.name.toLowerCase();
    return name === query ? 0 : name.includes(query) ? 1 : words.every(w => name.includes(w)) ? 2 : 3;
  };
  return [...list].sort((a, b) => rank(a) - rank(b));
}

/** Text for "Copy verified blurb": name, optional AI description, then verified highlights. */
export function blurb(f, catalog, includeAi) {
  const lines = [`${f.name} (${[catalog.product, f.moduleName || catalog.moduleName].filter(Boolean).join(" ")} · ${f.capability.name})`];
  if (includeAi && f.summary) lines.push("", `${f.summary.text}  [AI draft: review before publishing]`);
  const v = f.claims.filter(x => x.status === "VERIFIED");
  if (v.length) {
    lines.push("");
    v.forEach(x => lines.push(`• ${x.text}`));
  }
  return lines.join("\n");
}

/** Rows for the feature-sheet CSV (header first). */
export function csvRows(features, multi) {
  const head = ["Capability", "Feature", "Screen type", "Readiness", "Verified highlights", "Needs review", "Screen ID"];
  if (multi) head.unshift("Module");
  const rows = features.map(f => {
    const r = [
      f.capability.name,
      f.name,
      f.category,
      READINESS[f.readiness],
      f.claims.filter(x => x.status === "VERIFIED").map(x => x.text).join(" | "),
      f.claims.filter(x => x.status !== "VERIFIED").map(x => x.text).join(" | "),
      f.screenId,
    ];
    if (multi) r.unshift(f.moduleName);
    return r;
  });
  return [head, ...rows];
}
