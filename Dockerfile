# syntax=docker/dockerfile:1.7

ARG PYTHON_VERSION=3.14

# ---------------------------------------------------------------------------
# Stage 1: build an isolated virtualenv with the runtime dependencies only.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /build

# Copy the manifest on its own so this layer is reused until requirements change.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --upgrade pip \
 && pip install -r requirements.txt

# ---------------------------------------------------------------------------
# Stage 2: slim runtime image, no build tooling, non-root user.
# ---------------------------------------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}" \
    RAIL_DATA_PATH=/app/data/processed/station_usage.csv

# Fixed UID/GID so the host data volume can be chowned to match (see deploy.yml).
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home \
        --home-dir /nonexistent --shell /usr/sbin/nologin app

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

# Application code stays root-owned and read-only to the runtime user.
COPY api/ ./api/
COPY pipeline/ ./pipeline/

# Only the data directory is writable (the pipeline writes here; the API reads).
RUN mkdir -p data/raw data/processed \
 && chown -R app:app data

USER app

EXPOSE 8000

# /health returns 503 when the station table is not loaded, which marks the
# container unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"]

CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
