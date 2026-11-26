# ============================================================
#  STL Digital Voice Agent — Hardened Backend Dockerfile
#  Security: Non-root execution, minimal attack surface, healthcheck
# ============================================================

FROM python:3.11-slim

# Prevent Python from writing .pyc files & buffer stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8055 \
    HOST=0.0.0.0

WORKDIR /app

# Install system dependencies & audio processing utilities
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create dedicated non-root user and group
RUN groupadd --system --gid 1001 appuser && \
    useradd --system --uid 1001 --gid 1001 --create-home --shell /bin/bash appuser

# Install python dependencies as root into system environment
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code and adjust file ownership
COPY . .
RUN chown -R appuser:appuser /app

# Expose the FastAPI backend port
EXPOSE 8055

# Container Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8055/health || exit 1

# Enforce non-root execution
USER appuser

CMD ["python", "run.py"]
