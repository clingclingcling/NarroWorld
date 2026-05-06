FROM node:20-bookworm-slim AS frontend-build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./frontend/
COPY locales ./locales

WORKDIR /app/frontend
RUN npm ci

COPY frontend ./
RUN npm run build

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FLASK_DEBUG=false \
    UPLOAD_FOLDER=/app/backend/uploads \
    OASIS_SIMULATION_DATA_DIR=/app/backend/uploads/simulations \
    FRONTEND_DIST_DIR=/app/frontend/dist

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.9.26 /uv /uvx /bin/

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend ./
COPY --from=frontend-build /app/frontend/dist /app/frontend/dist
RUN uv sync --frozen --no-dev

RUN mkdir -p /app/backend/uploads /app/backend/logs

EXPOSE 5002

CMD ["/bin/sh", "-c", "uv run gunicorn --bind 0.0.0.0:${PORT:-5002} --workers ${WEB_CONCURRENCY:-1} --threads ${GUNICORN_THREADS:-8} --timeout ${GUNICORN_TIMEOUT:-300} 'app:create_app()'"]
