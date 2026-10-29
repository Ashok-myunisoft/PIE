// The frame around every page: top bar with navigation and the light/dark switch.
import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, BookOpen, Building2, Home, Menu, MessageSquare, Moon, Network, Sun, Table2, X } from "lucide-react";
import { store } from "../lib/hooks.js";

export const NAV = [
  { to: "/", label: "Home", icon: Home, end: true },
  { to: "/chat", label: "Ask PIE", icon: MessageSquare },
  { to: "/catalog", label: "Feature Catalog", icon: BookOpen },
  { to: "/fields", label: "Field Explorer", icon: Table2 },
  { to: "/relations", label: "Relationships", icon: Network },
  { to: "/logs", label: "Activity Log", icon: Activity },
  { to: "/intelligence", label: "Department Knowledge", icon: Building2 },
];

const themeStore = store("pie");

function useTheme() {
  const system = () => (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  const [theme, setTheme] = useState(() => themeStore.get("theme", "") || system());
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
  }, [theme]);
  const toggle = () =>
    setTheme(t => {
      const next = t === "dark" ? "light" : "dark";
      themeStore.set("theme", next);
      return next;
    });
  return [theme, toggle];
}

export default function Layout() {
  const [theme, toggleTheme] = useTheme();
  const [menu, setMenu] = useState(false);
  const { pathname } = useLocation();
  useEffect(() => setMenu(false), [pathname]);

  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand" aria-label="PIE home">
          <span className="logo">PIE</span>
          <span className="brand-text">
            <b>Product Intelligence</b>
            <small>GoodBooks ERP</small>
          </span>
        </NavLink>
        <nav className={`nav${menu ? " open" : ""}`} aria-label="PIE pages">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? "active" : "")}>
              <Icon size={16} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="topbar-right">
          <button className="icon-btn" type="button" onClick={toggleTheme} aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`} title="Light / dark theme">
            {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
          </button>
          <button className="icon-btn menu-btn" type="button" onClick={() => setMenu(m => !m)} aria-label="Menu" aria-expanded={menu}>
            {menu ? <X size={18} /> : <Menu size={18} />}
          </button>
        </div>
      </header>
      <div className="content">
        <Outlet />
      </div>
    </div>
  );
}
