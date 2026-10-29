import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Activity, ArrowRight, BookOpen, Building2, MessageSquare, Network, Search, Sparkles, Table2 } from "lucide-react";
import { useApi } from "../lib/hooks.js";
import { num } from "../lib/format.js";
import { StatusDot } from "../components/ui.jsx";
import "./Home.css";

const QUICK = [
  "What are the key features available in the Inventory module?",
  "What features should I highlight for the pump industry?",
  "How do I create a Bill of Materials?",
];

const sum = (rows, key) => (rows || []).reduce((a, r) => a + ((r.statistics || {})[key] || 0), 0);

export default function Home() {
  const status = useApi("api/chat/status");
  const modules = useApi("api/modules");
  const fields = useApi("api/fields");
  const [q, setQ] = useState("");
  const navigate = useNavigate();

  const s = status.data;
  const mods = modules.data || [];
  const flds = fields.data || [];

  const ask = text => {
    const t = text.trim();
    if (t) navigate(`/chat?q=${encodeURIComponent(t)}`);
  };

  const cards = [
    {
      to: "/chat", icon: MessageSquare, kicker: "Ask", title: "Ask PIE",
      text: "Ask in plain English about features, industries, demo flows, differentiators, gaps, tickets or individual fields. Answers cite their sources.",
      facts: s ? [[num(s.points), "knowledge items"], [s.llm, "AI"]] : null, go: "Open the chat",
    },
    {
      to: "/catalog", icon: BookOpen, kicker: "Browse", title: "Feature Catalog",
      text: "Marketing-ready feature lists by capability, with verified highlights, readiness and a CSV feature sheet.",
      facts: mods.length ? [[num(sum(mods, "features")), "features"], [num(sum(mods, "ready")), "marketing-ready"], [mods.length, "modules"]] : null,
      go: "Open the catalog",
    },
    {
      to: "/fields", icon: Table2, kicker: "Explore", title: "Field Explorer",
      text: "Every field of a screen: type, allowed values, validations and messages, dependencies, formulas, database and code mapping, usage and evidence.",
      facts: flds.length ? [[num(sum(flds, "fields")), "fields"], [num(flds.length), flds.length === 1 ? "screen" : "screens"]] : null,
      go: "Open the explorer",
    },
    {
      to: "/relations", icon: Network, kicker: "Trace", title: "Relationships",
      text: "How each screen connects to APIs, backend services and database tables, with the evidence behind every link.",
      go: "Open relationships",
    },
    {
      to: "/intelligence", icon: Building2, kicker: "Prepare", title: "Department Knowledge",
      text: "Working briefs, product evidence and open questions for each department, straight from the knowledge base.",
      go: "Open department knowledge",
    },
    {
      to: "/logs", icon: Activity, kicker: "Operate", title: "Activity Log",
      text: "Every discovery, mapping, Qdrant ingestion and question: status, counts, errors, and how each answer was reached.",
      go: "Open the log",
    },
  ];

  return (
    <main className="page home">
      <section className="hero">
        <span className="hero-kicker">
          <Sparkles size={14} /> Product Intelligence for GoodBooks ERP
        </span>
        <h1>Evidence-backed answers about GoodBooks ERP</h1>
        <p className="muted">
          PIE reads the ERP's frontend, backend and database and turns them into knowledge you can ask about, browse and share. Every
          statement links back to where it came from.
        </p>
        <form
          className="hero-ask"
          onSubmit={e => {
            e.preventDefault();
            ask(q);
          }}
        >
          <Search size={18} aria-hidden="true" />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="Ask PIE anything about the ERP…" aria-label="Ask PIE a question" />
          <button className="btn primary" type="submit" disabled={!q.trim()}>
            Ask <ArrowRight size={15} />
          </button>
        </form>
        <div className="hero-quick">
          {QUICK.map(t => (
            <button key={t} type="button" onClick={() => ask(t)}>
              {t}
            </button>
          ))}
        </div>
      </section>

      <div className="statusline home-status" aria-live="polite">
        {s ? (
          <>
            <span>
              <StatusDot tone="ok" />
              Knowledge base <b>{s.collection}</b>: <b>{num(s.points)}</b> items
            </span>
            <span>
              Verified modules: <b>{(s.verifiedModules || []).length}</b>
            </span>
            <span>
              Field knowledge: <b>{num((s.byType || {}).FieldKnowledge || 0)}</b>
            </span>
            <span>
              Tickets: <b>{String(s.tickets)}</b>
            </span>
            <span>
              AI: <b>{s.llm}</b>
            </span>
          </>
        ) : status.error ? (
          <span>
            <StatusDot tone="off" />
            {status.error.message} Retrying automatically…
          </span>
        ) : (
          <span>
            <StatusDot />
            Checking the knowledge base…
          </span>
        )}
      </div>

      <div className="home-cards">
        {cards.map(({ to, icon: Icon, kicker, title, text, facts, go }) => (
          <Link key={to} to={to} className="home-card">
            <div className="home-card-top">
              <span className="home-icon">
                <Icon size={20} />
              </span>
              <span className="kicker">{kicker}</span>
            </div>
            <h2>{title}</h2>
            <p className="muted">{text}</p>
            {facts && (
              <div className="facts">
                {facts.map(([v, l]) => (
                  <span key={l}>
                    <b>{v}</b>
                    {l}
                  </span>
                ))}
              </div>
            )}
            <span className="go">
              {go} <ArrowRight size={14} />
            </span>
          </Link>
        ))}
      </div>

      <section className="how card">
        <h3>How PIE builds its knowledge</h3>
        <div className="how-steps">
          {[
            ["Discover", "Screens, forms, APIs, services and database objects from the ERP source."],
            ["Map", "Screen → API → business logic → SQL → tables, with file and line evidence."],
            ["Explain", "AI writes explanations from the extracted facts only, labelled as AI-derived."],
            ["Store", "Everything is kept in the Qdrant knowledge base that the chat searches."],
          ].map(([t, d], i) => (
            <div key={t}>
              <span className="how-n">{i + 1}</span>
              <b>{t}</b>
              <span className="muted">{d}</span>
            </div>
          ))}
        </div>
        <p className="muted policy">
          Verified facts come straight from the sources. AI-derived text and anything marked "needs review" should be checked with product
          before external use.
        </p>
      </section>
    </main>
  );
}
