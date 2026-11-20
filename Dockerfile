# =============================================================================
# Stage 1: "builder" - install the Python packages
# This stage is thrown away at the end. Only what we copy OUT of it survives.
# =============================================================================
FROM python:3.12-slim AS builder

# A virtual environment in a fixed place, so we can copy it as ONE folder
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# =============================================================================
# Stage 2: "runtime" - the image that actually ships
# Starts clean: no pip cache, no build leftovers - just the app + its packages
# =============================================================================
FROM python:3.12-slim

# The pipeline passes the Git commit SHA in here (--build-arg APP_VERSION=...)
ARG APP_VERSION=dev

ENV PATH="/opt/venv/bin:$PATH" \
    APP_VERSION=$APP_VERSION \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Create a normal (non-root) user and give it the /app folder,
# so the app can still write its SQLite file when no DATABASE_URL is set
RUN useradd --create-home --uid 10001 appuser \
    && chown appuser:appuser /app

# Take ONLY the finished packages from the builder stage
COPY --from=builder /opt/venv /opt/venv
COPY main.py .
COPY alembic.ini .
COPY migrations ./migrations

# Everything from here on runs as appuser (by its numeric ID - Kubernetes needs
# a number to prove the container is not root)
USER 10001

EXPOSE 8000

# Docker checks /health every 30s and marks the container healthy/unhealthy.
# (slim images have no curl, so we use Python itself to make the request)
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"]

CMD ["sh", "-c", "alembic upgrade head && exec uvicorn main:app --host 0.0.0.0 --port 8000"]
