// Ask PIE: the chat. Conversations are saved on the PIE server as chat sessions; each keeps its
// own history, modules and audience. The browser only remembers which chat was open last.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Archive, ArchiveRestore, Check, MessageSquarePlus, PanelLeft, Pencil, Search, Send, Trash2, X } from "lucide-react";
import { api, qs } from "../lib/api.js";
import { copyText, store, useApi, useDebounced, useModuleSelection, usePatchParams } from "../lib/hooks.js";
import { bucket, dateTime, moduleLabel, moduleOptions, num, when } from "../lib/format.js";
import ModulePicker from "../components/ModulePicker.jsx";
import { Chip, Dots, StatusDot, useToast } from "../components/ui.jsx";
import AnswerCard from "./chat/AnswerCard.jsx";
import "./Chat.css";

const SUGGESTIONS = [
  ["Feature discovery", "Give me the feature list of the modules."],
  ["Industry targeting", "What features should I highlight for the pump industry?"],
  ["Demo preparation", "Prepare a demo flow for a pump industry's IT Manager."],
  ["Differentiation", "What are our key product differentiators?"],
  ["Market gaps", "What product features are missing or in demand?"],
  ["Ticket analysis", "What are the top issues reported in our software?"],
  ["Feature discovery", "What are the key features available in the Inventory module?"],
  ["Industry targeting", "What business problems can our ERP solve for the pump industry?"],
  ["Content creation", "Write a brochure page for the Inventory module."],
  ["How-to", "How do I create a Bill of Materials?"],
  ["Business process", "Explain the end-to-end process from gate entry to stock."],
  ["Developer", "Where is the BOM validated before saving, and which line throws the error?"],
];

// Who the answer is written for: changes style and level of detail, never the facts.
const AUDIENCES = [
  ["marketing", "Marketing Team"],
  ["client_nontechnical", "Client (non-technical)"],
  ["client_technical", "Client (technical)"],
  ["developer", "Developer"],
  ["stakeholder", "Stakeholder"],
];
const AUDIENCE_LABELS = Object.fromEntries(AUDIENCES);
const MAX_CHARS = 2000;

const chatStore = store("pie-chat");
const scopeText = s =>
  (s.modules && s.modules.length ? s.modules.map(moduleLabel).join(" + ") : "All modules") + " · " + (AUDIENCE_LABELS[s.audience] || "Marketing Team");

function newUserId() {
  let id = chatStore.get("userId", "");
  if (!id) {
    id = "u-" + (crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2));
    chatStore.set("userId", id);
  }
  return id;
}

/* ---------- left sidebar: saved chats ---------- */
function Sidebar({ open, sessions, current, search, setSearch, showArchived, setShowArchived, onNew, onOpen, onRename, onArchive, onDelete, status }) {
  const [editing, setEditing] = useState(null);
  const [title, setTitle] = useState("");

  const groups = [];
  for (const s of sessions) {
    const b = bucket(s.updatedAt);
    if (!groups.length || groups[groups.length - 1].label !== b) groups.push({ label: b, items: [] });
    groups[groups.length - 1].items.push(s);
  }
  const startRename = s => {
    setEditing(s.sessionId);
    setTitle(s.title);
  };
  const finishRename = async s => {
    const t = title.trim();
    setEditing(null);
    if (t && t !== s.title) await onRename(s.sessionId, t);
  };

  return (
    <aside className={`side${open ? " open" : ""}`} aria-label="Chats">
      <button className="btn primary newchat" type="button" onClick={onNew}>
        <MessageSquarePlus size={16} /> New chat
      </button>
      <label className="search side-search">
        <Search size={14} />
        <input className="input" type="search" placeholder="Search chats…" value={search} onChange={e => setSearch(e.target.value)} aria-label="Search chats" />
      </label>
      <div className="side-list">
        {!sessions.length && <div className="side-empty">{search.trim() ? "No chat matches." : "No saved chats yet. Ask a question to start one."}</div>}
        {groups.map(g => (
          <div key={g.label}>
            <div className="side-group">{g.label}</div>
            {g.items.map(s => {
              const active = current && current.sessionId === s.sessionId;
              if (editing === s.sessionId) {
                return (
                  <form
                    key={s.sessionId}
                    className="sitem editing"
                    onSubmit={e => {
                      e.preventDefault();
                      finishRename(s);
                    }}
                  >
                    <input
                      className="input"
                      autoFocus
                      value={title}
                      maxLength={120}
                      onChange={e => setTitle(e.target.value)}
                      onKeyDown={e => e.key === "Escape" && setEditing(null)}
                      aria-label="Chat title"
                    />
                    <button className="icon-btn sm" type="submit" aria-label="Save title">
                      <Check size={14} />
                    </button>
                    <button className="icon-btn sm" type="button" onClick={() => setEditing(null)} aria-label="Cancel">
                      <X size={14} />
                    </button>
                  </form>
                );
              }
              return (
                <div
                  key={s.sessionId}
                  className={`sitem${active ? " active" : ""}`}
                  role="button"
                  tabIndex={0}
                  aria-current={active ? "true" : undefined}
                  title={`${s.title}\n${scopeText(s)}\nCreated ${dateTime(s.createdAt)} · updated ${dateTime(s.updatedAt)}`}
                  onClick={() => !active && onOpen(s.sessionId)}
                  onKeyDown={e => e.key === "Enter" && onOpen(s.sessionId)}
                >
                  <span className="t">{s.title}</span>
                  <small>
                    {scopeText(s)} · {when(s.updatedAt)}
                  </small>
                  <span className="acts" onClick={e => e.stopPropagation()}>
                    <button type="button" title="Rename" aria-label="Rename" onClick={() => startRename(s)}>
                      <Pencil size={13} />
                    </button>
                    <button type="button" title={s.archived ? "Restore" : "Archive"} aria-label={s.archived ? "Restore" : "Archive"} onClick={() => onArchive(s)}>
                      {s.archived ? <ArchiveRestore size={13} /> : <Archive size={13} />}
                    </button>
                    <button type="button" title="Delete" aria-label="Delete" className="del" onClick={() => onDelete(s)}>
                      <Trash2 size={13} />
                    </button>
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <label className="check side-foot">
        <input type="checkbox" checked={showArchived} onChange={e => setShowArchived(e.target.checked)} /> Show archived chats
      </label>
      <div className="side-status">{status}</div>
    </aside>
  );
}

export default function Chat() {
  const toast = useToast();
  const [params] = useSearchParams();
  const patchParams = usePatchParams();
  const [userId] = useState(newUserId);

  // ---- knowledge base status and module list (retries until the backend answers)
  const status = useApi("api/chat/status");
  const s = status.data;
  const options = useMemo(() => (s ? moduleOptions(s.modules || [], s.verifiedModules || []) : []), [s]);
  const [modules, setModules] = useModuleSelection("chat");
  useEffect(() => {
    // Drop choices that no longer exist once the real module list is known.
    if (!options.length) return;
    const known = new Set(options.map(o => o.value));
    if (modules.some(m => !known.has(m))) setModules(modules.filter(m => known.has(m)));
  }, [options]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- audience (?audience=developer) and "include unconfirmed"
  const [audience, setAudienceState] = useState(() => {
    const a = params.get("audience") || chatStore.get("audience", "marketing");
    return AUDIENCE_LABELS[a] ? a : "marketing";
  });
  const setAudience = useCallback(
    a => {
      const key = AUDIENCE_LABELS[a] ? a : "marketing";
      setAudienceState(key);
      chatStore.set("audience", key);
      patchParams(p => p.set("audience", key));
    },
    [patchParams]
  );
  const [includeReview, setIncludeReview] = useState(() => chatStore.get("includeReview", false));
  useEffect(() => chatStore.set("includeReview", includeReview), [includeReview]);

  // ---- chats
  const [current, setCurrent] = useState(null); // open session; null = new chat (created on the first question)
  const [messages, setMessages] = useState([]); // {role, content?, response?, error?, question?}
  const [busy, setBusy] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [sessions, setSessions] = useState([]);
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [sideOpen, setSideOpen] = useState(false);
  const debouncedSearch = useDebounced(search, 250);

  const loadSessions = useCallback(async () => {
    try {
      const q = debouncedSearch.trim();
      const r = await api("api/sessions?" + qs({ userId, q, archived: showArchived }));
      setSessions(r.sessions || []);
    } catch (e) {
      setSessions([]);
    }
  }, [userId, debouncedSearch, showArchived]);
  useEffect(() => {
    loadSessions();
  }, [loadSessions, s]); // reload once the backend becomes reachable

  useEffect(() => chatStore.set("session", current ? current.sessionId : null), [current]);

  const newChat = useCallback(() => {
    setCurrent(null);
    setMessages([]);
    setSideOpen(false);
    setTimeout(() => inputRef.current && inputRef.current.focus(), 0);
  }, []);

  const openSession = useCallback(
    async id => {
      try {
        const sess = await api(`api/sessions/${encodeURIComponent(id)}?` + qs({ userId }));
        setCurrent(sess);
        setMessages(
          (sess.messages || []).map(m =>
            m.role === "user" ? { role: "user", content: m.content } : { role: "assistant", response: m.response || { answer: m.content } }
          )
        );
        // The chat continues with the modules and audience it was using.
        setModules(sess.modules || []);
        setAudience(sess.audience || "marketing");
        justAnswered.current = false;
        setSideOpen(false);
      } catch (e) {
        toast("Could not open that chat: " + e.message);
        newChat();
      }
    },
    [userId, setModules, setAudience, toast, newChat]
  );

  async function renameSession(id, title) {
    try {
      const u = await api(`api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: { userId, title } });
      setCurrent(c => (c && c.sessionId === id ? { ...c, ...u } : c));
      loadSessions();
    } catch (e) {
      toast("Rename failed: " + e.message);
    }
  }
  async function archiveSession(sess) {
    try {
      await api(`api/sessions/${encodeURIComponent(sess.sessionId)}`, { method: "PATCH", body: { userId, archived: !sess.archived } });
      if (current && current.sessionId === sess.sessionId && !sess.archived) newChat();
      toast(sess.archived ? "Chat restored" : "Chat archived");
      loadSessions();
    } catch (e) {
      toast("Archive failed: " + e.message);
    }
  }
  async function deleteSession(sess) {
    if (!window.confirm(`Delete "${sess.title}"? This cannot be undone.`)) return;
    try {
      await api(`api/sessions/${encodeURIComponent(sess.sessionId)}?` + qs({ userId }), { method: "DELETE" });
      if (current && current.sessionId === sess.sessionId) newChat();
      toast("Chat deleted");
      loadSessions();
    } catch (e) {
      toast("Delete failed: " + e.message);
    }
  }

  // ---- asking
  const ask = useCallback(
    async question => {
      question = (question || "").trim();
      if (!question || busy) return;
      // Earlier turns only give follow-ups context; send short excerpts.
      const history = messages
        .filter(m => !m.error)
        .map(m => ({ role: m.role, content: (m.role === "user" ? m.content : m.response ? m.response.answer || "" : "").slice(0, 2000) }))
        .slice(-6);
      setMessages(ms => [...ms, { role: "user", content: question }]);
      setBusy(true);
      try {
        const data = await api("api/chat", {
          method: "POST",
          body: { message: question, history, modules, audience, includeReview, userId, ...(current ? { sessionId: current.sessionId } : { newSession: true }) },
        });
        setMessages(ms => [...ms, { role: "assistant", response: data }]);
        if (data.session) {
          setCurrent(c => ({ ...(c || {}), ...data.session }));
          loadSessions();
        }
      } catch (e) {
        const reason = e.offline ? "the PIE server did not respond. Check that the backend is running, then try again." : e.message;
        setMessages(ms => [...ms, { role: "assistant", error: `PIE could not answer: ${reason}`, question }]);
      } finally {
        justAnswered.current = true;
        setBusy(false);
      }
    },
    [busy, messages, modules, audience, includeReview, userId, current, loadSessions]
  );

  function retry(idx) {
    const m = messages[idx];
    setMessages(ms => ms.slice(0, idx - 1).concat(ms.slice(idx + 1)));
    // Ask again after the failed pair is removed from the history.
    setTimeout(() => ask(m.question || ""), 0);
  }

  // Seconds counter while waiting for an answer.
  useEffect(() => {
    if (!busy) return undefined;
    const started = Date.now();
    setElapsed(0);
    const t = setInterval(() => setElapsed(Math.round((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(t);
  }, [busy]);

  // ---- first load: ?q= asks straight away, else reopen the chat that was open last
  const started = useRef(false);
  const pendingQ = useRef(null);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const q = params.get("q");
    if (q) {
      patchParams(p => p.delete("q"));
      pendingQ.current = q;
      return;
    }
    const last = chatStore.get("session", null);
    if (last) openSession(last);
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (pendingQ.current) {
      const q = pendingQ.current;
      pendingQ.current = null;
      ask(q);
    }
  }, [ask]);

  // ---- scrolling: follow the loader; when an answer arrives show its beginning
  const scroller = useRef(null);
  const thread = useRef(null);
  const justAnswered = useRef(false);
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const last = thread.current && thread.current.lastElementChild;
    if (justAnswered.current && last && !busy) {
      last.scrollIntoView({ block: "start" });
      justAnswered.current = false;
    } else el.scrollTop = el.scrollHeight;
  }, [messages, busy]);

  // ---- composer
  const [input, setInput] = useState("");
  const inputRef = useRef(null);
  useEffect(() => {
    const t = inputRef.current;
    if (!t) return;
    t.style.height = "auto";
    t.style.height = Math.min(t.scrollHeight, 200) + "px";
  }, [input]);
  const submit = e => {
    e && e.preventDefault();
    const q = input;
    if (!q.trim() || busy) return;
    setInput("");
    ask(q);
  };

  async function copyAnswer(m) {
    const ok = await copyText((m.response.answer || "").replace(/\s?\[K\d+\]/g, ""));
    toast(ok ? "Answer copied" : "Copy failed");
  }

  // ---- header text
  const scope = current || { title: "New chat", modules, audience };
  const questions = current ? (current.messageCount / 2) | 0 : 0;
  const statusLine = s ? (
    <>
      <span>
        <StatusDot tone="ok" />
        <b>{num(s.points)}</b> knowledge items
      </span>
      <span>
        AI: <b>{s.llm}</b>
      </span>
    </>
  ) : status.error ? (
    <span>
      <StatusDot tone="off" />
      Server not reachable. Retrying…
    </span>
  ) : (
    <span>
      <StatusDot /> Connecting…
    </span>
  );

  return (
    <div className="chat">
      {sideOpen && <div className="side-backdrop" onClick={() => setSideOpen(false)} />}
      <Sidebar
        open={sideOpen}
        sessions={sessions}
        current={current}
        search={search}
        setSearch={setSearch}
        showArchived={showArchived}
        setShowArchived={setShowArchived}
        onNew={newChat}
        onOpen={openSession}
        onRename={renameSession}
        onArchive={archiveSession}
        onDelete={deleteSession}
        status={statusLine}
      />
      <div className="chatcol">
        <div className="chatbar">
          <button className="icon-btn side-toggle" type="button" onClick={() => setSideOpen(o => !o)} aria-label="Show chats" aria-expanded={sideOpen}>
            <PanelLeft size={18} />
          </button>
          <div className="chatbar-title">
            <b title={scope.title}>{scope.title}</b>
            <span className="chatbar-ctx">
              {current ? (
                <>
                  {questions} question{questions === 1 ? "" : "s"} · {when(current.updatedAt)}
                </>
              ) : (
                "The title is created from your first question"
              )}
            </span>
          </div>
          <div className="chatbar-controls">
            <ModulePicker title="Answer from modules" options={options} selected={modules} onChange={setModules} loading={!s && !status.error} />
            <label className="aud" title="Who the answer is written for: it changes the style and level of detail, never the facts">
              <span>For</span>
              <select className="input" value={audience} onChange={e => {
                setAudience(e.target.value);
                if (messages.length) toast("Next answers are for: " + AUDIENCE_LABELS[e.target.value]);
              }} aria-label="Answer written for">
                {AUDIENCES.map(([k, l]) => (
                  <option key={k} value={k}>
                    {l}
                  </option>
                ))}
              </select>
            </label>
            <label className="check" title="Also use knowledge that still needs product review">
              <input type="checkbox" checked={includeReview} onChange={e => setIncludeReview(e.target.checked)} />
              <span>Include unconfirmed</span>
            </label>
          </div>
        </div>

        <div className="chat-scroll" ref={scroller}>
          <div className="thread" ref={thread} aria-live="polite">
            {!messages.length && !busy ? (
              <div className="welcome">
                <h1>What would you like to know?</h1>
                <p className="muted">
                  Ask about GoodBooks ERP features, industries, demos, differentiators, gaps or tickets. PIE answers only from its knowledge base and shows the sources it used.
                </p>
                <p className="ctxline">
                  Answers are written for <Chip tone="accent">{AUDIENCE_LABELS[audience]}</Chip> from <Chip>{modules.length ? modules.map(moduleLabel).join(" + ") : "all modules"}</Chip>
                </p>
                {status.error && (
                  <div className="note">The PIE server is not reachable yet. You can type your question; this page reconnects automatically.</div>
                )}
                <div className="prompts">
                  {SUGGESTIONS.slice(0, 6).map(([c, q]) => (
                    <button key={q} className="prompt" type="button" onClick={() => ask(q)}>
                      <small>{c}</small>
                      {q}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              messages.map((m, i) =>
                m.role === "user" ? (
                  <div key={i} className="msg user">
                    <div className="bubble">{m.content}</div>
                  </div>
                ) : (
                  <div key={i} className="msg">
                    <AnswerCard message={m} idx={i} isLast={i === messages.length - 1} onCopy={() => copyAnswer(m)} onRetry={() => retry(i)} />
                  </div>
                )
              )
            )}
            {busy && (
              <div className="msg">
                <div className="answer loading">
                  <Dots />
                  <span>Searching the PIE knowledge base and writing an answer… {elapsed}s</span>
                </div>
              </div>
            )}
          </div>
        </div>

        <footer className="composer">
          {messages.length > 0 && (
            <div className="suggest" aria-label="Suggested questions">
              {SUGGESTIONS.map(([c, q]) => (
                <button key={q} type="button" title={c} onClick={() => ask(q)} disabled={busy}>
                  {q}
                </button>
              ))}
            </div>
          )}
          <form className="inputrow" onSubmit={submit}>
            <label className="sr-only" htmlFor="chat-input">
              Your question
            </label>
            <textarea
              id="chat-input"
              ref={inputRef}
              rows={1}
              maxLength={MAX_CHARS}
              value={input}
              autoFocus
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) submit(e);
              }}
              placeholder="Ask about features, industries, demos, differentiators, gaps or tickets…"
            />
            <button className="btn primary send" type="submit" disabled={busy || !input.trim()} aria-label="Send">
              <Send size={16} />
            </button>
          </form>
          <div className="hint">
            <span>Enter to send · Shift+Enter for a new line. Answers use only PIE knowledge; check ⚠ items with product before external use.</span>
            <span>
              {input.length}/{MAX_CHARS}
            </span>
          </div>
        </footer>
      </div>
    </div>
  );
}
