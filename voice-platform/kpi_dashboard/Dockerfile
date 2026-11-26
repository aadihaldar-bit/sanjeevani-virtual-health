# Dockerfile for STL Digital Enterprise Voice Bot KPI Dashboard
# Optimized for Google Cloud Run & Containerized Environments

FROM python:3.11-slim

# Avoid buffering output
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV HOST=0.0.0.0
ENV GCS_LOG_BUCKET=stld-bkt-data-prod-voicebot-log-uscentral01-001
ENV GOOGLE_CLOUD_PROJECT=voicebot-support

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . /app/kpi_dashboard

# Ensure cache directory exists and is writable
RUN mkdir -p /app/kpi_dashboard/cache && chmod -R 777 /app/kpi_dashboard/cache

EXPOSE 8080

# Run uvicorn on $PORT for Cloud Run
CMD ["sh", "-c", "python -m uvicorn kpi_dashboard.main:app --host 0.0.0.0 --port ${PORT}"]
