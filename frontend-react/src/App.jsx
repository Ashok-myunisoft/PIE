// Pages and their addresses. Hash routing (/#/chat) works on any static web server
// without extra server settings.
import { HashRouter, Link, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout.jsx";
import { EmptyState, ToastProvider } from "./components/ui.jsx";
import Home from "./pages/Home.jsx";
import Chat from "./pages/Chat.jsx";
import Catalog from "./pages/Catalog.jsx";
import Fields from "./pages/Fields.jsx";
import Relations from "./pages/Relations.jsx";
import Logs from "./pages/Logs.jsx";
import Intelligence from "./pages/Intelligence.jsx";

function NotFound() {
  return (
    <main className="page">
      <EmptyState title="Page not found" action={<Link className="btn primary" to="/">Go to Home</Link>}>
        This address does not match a PIE page.
      </EmptyState>
    </main>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <HashRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Home />} />
            <Route path="chat" element={<Chat />} />
            <Route path="catalog" element={<Catalog />} />
            <Route path="fields" element={<Fields />} />
            <Route path="relations" element={<Relations />} />
            <Route path="logs" element={<Logs />} />
            <Route path="intelligence" element={<Intelligence />} />
            <Route path="*" element={<NotFound />} />
          </Route>
        </Routes>
      </HashRouter>
    </ToastProvider>
  );
}
