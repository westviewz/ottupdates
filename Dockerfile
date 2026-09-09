# OTT Update Bot — Dockerfile
#
# Multi-stage build kept intentionally simple for a cron-job workload.
# The image is built by Railway from this file automatically.

FROM python:3.11-slim

# Prevent .pyc files and enable unbuffered stdout/stderr for Railway logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY app/ ./app/

# The cron job command — Railway overrides CMD via its cron configuration
CMD ["python", "-m", "app.main"]
