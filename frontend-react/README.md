# PIE frontend (React)

The PIE web app built with React: Home, Ask PIE, Feature Catalog, Field Explorer,
Relationships, Activity Log and Department Knowledge. It talks to the same backend as the
plain-HTML pages in `../frontend/`, which are unchanged.

## Run it on your PC

You need Node.js 18 or newer. Start the backend first (`.\start_backend.ps1` in the project
root), then:

```powershell
cd frontend-react
npm install        # first time only
npm run dev        # http://localhost:5173
```

The dev server forwards `/usage/...`, `/logs/api/...` and `/health` to the backend at
`http://localhost:8000`, so there are no cross-site (CORS) problems. To use another backend:
`$env:PIE_BACKEND = "http://192.168.0.90:8000"; npm run dev`.

## Build for a web server

```powershell
npm run build      # writes the finished site to dist/
npm run preview    # optional: serve dist/ at http://localhost:4173 to check it
```

`dist/` is plain static files: any web server can serve it (nginx, IIS). The web server must
also forward `/usage/` and `/logs/api/` to the backend, as `docker/nginx.conf` does.

`dist/config.js` holds the backend address (`apiBase`, default `"/usage/"` = the same server).
It is read when the page opens, so you can change it after building without rebuilding.

## Pages and addresses

The app uses hash addresses, so it needs no special web-server rules:

| Page | Address |
|---|---|
| Home | `/#/` |
| Ask PIE | `/#/chat` (`?q=question` asks straight away, `?modules=ess,inventory`, `?audience=developer`) |
| Feature Catalog | `/#/catalog` (`?modules=`, `?tab=features`, `?capability=`, `?q=`, `?feature=`) |
| Field Explorer | `/#/fields` (`?screen=`, `?field=`, `?modules=`, `?q=`) |
| Relationships | `/#/relations` (`?screen=`, `?modules=`) |
| Activity Log | `/#/logs` (`?run=<id>`) |
| Department Knowledge | `/#/intelligence` (`?module=inventory`) |

## Code layout

```text
src/
  main.jsx, App.jsx        start-up and the list of pages
  components/Layout.jsx    top bar, navigation, light/dark theme
  components/ModulePicker.jsx
  components/ui.jsx        loading / empty / error states, chips, toasts, page header
  lib/api.js               calls to the backend (address from public/config.js)
  lib/hooks.js             useApi (auto-retry while the backend is down), URL and storage helpers
  lib/format.js, lib/markdown.js
  pages/                   one file per page (+ a folder for the bigger ones)
  styles/global.css        colours (light and dark), buttons, tables, cards
```
