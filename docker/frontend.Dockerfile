# PIE frontend: the static web pages served by nginx, which also forwards API calls to the
# backend container. Build from the project root:
#   docker build -f docker/frontend.Dockerfile -t pie-frontend .
FROM nginx:1.27-alpine

COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/ /usr/share/nginx/html/

# Pages and API share one address, so the pages call the API on a relative path.
# (frontend/config.js in the repo keeps http://localhost:8000/usage/ for local use.)
RUN printf '%s\n' \
      '// PIE frontend configuration (Docker image): the API is behind the same nginx.' \
      'window.PIE_CONFIG = window.PIE_CONFIG || {' \
      '  apiBase: "/usage/"' \
      '};' > /usr/share/nginx/html/config.js

EXPOSE 80
