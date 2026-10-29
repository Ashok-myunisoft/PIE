// PIE frontend configuration, read when the page loads (not built in, so it can be changed
// after deployment without rebuilding).
// apiBase = where the PIE backend API is. "/usage/" means "this same web server", which is
// right for `npm run dev` (Vite forwards it to the backend) and for nginx in Docker.
// For a backend on another machine use e.g. "http://192.168.0.90:8000/usage/" and add this
// site's address to PIE_CORS_ORIGINS on the backend. Keep the trailing /usage/.
window.PIE_CONFIG = window.PIE_CONFIG || {
  apiBase: "/usage/"
};
