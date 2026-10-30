# PIE backend: the FastAPI server. Build from the project root:
#   docker build -f docker/backend.Dockerfile -t pie-backend .
# At run time mount the secrets file at /app/.env and the data folder at /app/outputs
# (see docker-compose.yml); neither is baked into the image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first, so code changes reuse this cached layer.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# The backend finds the project root two levels above pie_agent/, so /app is the root:
# /app/.env, /app/outputs, /app/data.
COPY backend/ backend/

RUN useradd --uid 1000 --create-home pie \
    && mkdir -p /app/outputs \
    && chown -R pie:pie /app
USER pie

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

# One worker: chats and the activity log are SQLite files under outputs/.
CMD ["uvicorn", "pie_agent.api:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8080", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]