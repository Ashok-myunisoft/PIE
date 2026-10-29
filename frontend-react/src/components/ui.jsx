// Small building blocks shared by the pages. Styles are in styles/global.css.
import { createContext, useCallback, useContext, useRef, useState } from "react";
import { AlertTriangle, Inbox, RefreshCw } from "lucide-react";

/* ---------- toast: short confirmation messages ("Link copied") ---------- */
const ToastContext = createContext(() => {});

export function ToastProvider({ children }) {
  const [msg, setMsg] = useState("");
  const timer = useRef();
  const show = useCallback(text => {
    setMsg(text);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setMsg(""), 2200);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className={`toast${msg ? " show" : ""}`} role="status" aria-live="polite">
        {msg}
      </div>
    </ToastContext.Provider>
  );
}

/** const toast = useToast(); toast("Saved"); */
export const useToast = () => useContext(ToastContext);

/* ---------- loading, empty and error states ---------- */
export function Dots() {
  return (
    <span className="dots" aria-hidden="true">
      <i />
      <i />
      <i />
    </span>
  );
}

export function Loading({ children = "Loading…" }) {
  return (
    <div className="state">
      <Dots />
      <span>{children}</span>
    </div>
  );
}

export function EmptyState({ icon: Icon = Inbox, title, children, action }) {
  return (
    <div className="state empty-state">
      <Icon size={28} aria-hidden="true" />
      {title && <b>{title}</b>}
      {children && <div className="muted">{children}</div>}
      {action}
    </div>
  );
}

/** An error with a Try again button. Offline errors say the page keeps retrying. */
export function ErrorState({ error, onRetry, title = "Something went wrong" }) {
  if (!error) return null;
  return (
    <div className="state error-state" role="alert">
      <AlertTriangle size={24} aria-hidden="true" />
      <b>{error.offline ? "Cannot reach the PIE server" : title}</b>
      <div className="muted">
        {error.message}
        {error.offline && " This page will keep trying and fill in as soon as the backend answers."}
      </div>
      {onRetry && (
        <button className="btn" type="button" onClick={onRetry}>
          <RefreshCw size={14} /> Try again
        </button>
      )}
    </div>
  );
}

/* ---------- labels ---------- */
export function Chip({ children, tone, title, className = "" }) {
  return (
    <span className={`chip${tone ? " " + tone : ""} ${className}`} title={title}>
      {children}
    </span>
  );
}

/** Coloured dot for a status line: ok | off | warn | (grey). */
export function StatusDot({ tone }) {
  return <span className={`dot${tone ? " " + tone : ""}`} aria-hidden="true" />;
}

/** Big number with a caption, for summary rows. */
export function Stat({ value, label, tone, title }) {
  return (
    <div className={`stat${tone ? " " + tone : ""}`} title={title}>
      <b>{value}</b>
      <span>{label}</span>
    </div>
  );
}

/** Page heading with optional description and actions on the right. */
export function PageHeader({ title, description, children }) {
  return (
    <div className="page-head">
      <div>
        <h1>{title}</h1>
        {description && <p className="muted">{description}</p>}
      </div>
      {children && <div className="page-actions">{children}</div>}
    </div>
  );
}
