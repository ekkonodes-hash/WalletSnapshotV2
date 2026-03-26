# Use the official Playwright + Python image — Chromium already installed
FROM mcr.microsoft.com/playwright/python:v1.58.0-jammy

WORKDIR /app

# Install system fonts so the audit-bar timestamp renders correctly on Linux
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-dejavu-core \
    fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app source
COPY . .

# /data is the persistent volume mount point on Railway.
# Locally this directory is unused (DATA_DIR defaults to the repo root).
RUN mkdir -p /data /data/outputs /data/browser_profile outputs browser_profile

# Railway injects $PORT at runtime (usually 8080). We must bind to it.
# Locally $PORT is unset so we fall back to 5000 — no behaviour change.
EXPOSE 8080

# Use gunicorn for production.  --workers 1 keeps a single process so the
# in-memory scheduler and status dict are shared across all requests.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 4 --timeout 300 app:app"]
