// One knowledge record: title, status and category, owner, the content, and its evidence.
import { Chip } from "../../components/ui.jsx";

const TONE = { APPROVED: "ok", VERIFIED: "ok", PUBLISHED: "ok", DRAFT: "warn", REVIEW_REQUIRED: "warn", MISSING: "bad", SOURCE_EVIDENCE: "accent" };
const words = s => String(s || "").replace(/_/g, " ").toLowerCase();

export default function RecordCard({ record: r }) {
  const evidence = {
    source_evidence: r.source_evidence,
    source_review: r.source_review,
    provenance: r.provenance,
    related_record_ids: r.related_record_ids,
    reviewed_by: r.reviewed_by,
    reviewed_on: r.reviewed_on,
  };
  return (
    <article className="card intel-record" data-record-id={r.id}>
      <h2>{r.title}</h2>
      <div className="intel-tags">
        {r.status && <Chip tone={TONE[r.status]}>{words(r.status)}</Chip>}
        {r.category && <Chip>{words(r.category)}</Chip>}
        {r.kind && <Chip>{words(r.kind)}</Chip>}
      </div>
      <p className="intel-meta">
        <code>{r.id}</code> · Owner: {r.owner || "not set"} · ERP version: {r.erp_version || "not confirmed"}
      </p>
      <div className="intel-content">{r.content}</div>
      <details className="intel-evidence">
        <summary>Evidence and review</summary>
        <pre>{JSON.stringify(evidence, null, 2)}</pre>
      </details>
    </article>
  );
}
