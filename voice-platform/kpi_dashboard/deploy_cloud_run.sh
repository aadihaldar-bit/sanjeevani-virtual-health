#!/usr/bin/env bash
# ==============================================================================
# Deploy STL Digital Enterprise Voice Bot KPI Dashboard to Google Cloud Run
# ==============================================================================

set -e

SERVICE_NAME="stl-voicebot-kpi-dashboard"
REGION="us-central1"
BUCKET_NAME="stld-bkt-data-prod-voicebot-log-uscentral01-001"
PROJECT_ID=$(gcloud config get-value project 2>/dev/null || echo "voicebot-support")
IMAGE_TAG="gcr.io/${PROJECT_ID}/${SERVICE_NAME}:latest"

echo "======================================================================"
echo "  Deploying STL Voice Bot KPI Dashboard to Google Cloud Run"
echo "  Service Name : ${SERVICE_NAME}"
echo "  Region       : ${REGION}"
echo "  GCS Bucket   : ${BUCKET_NAME}"
echo "  Image Tag    : ${IMAGE_TAG}"
echo "======================================================================"

# 1. Build and push container with Cloud Build
echo "[1/3] Building container image with Google Cloud Build..."
gcloud builds submit --tag "${IMAGE_TAG}" .

# 2. Deploy service to Cloud Run
echo "[2/3] Deploying container image to Cloud Run..."
gcloud run deploy "${SERVICE_NAME}" \
    --image "${IMAGE_TAG}" \
    --platform managed \
    --region "${REGION}" \
    --allow-unauthenticated \
    --port 8080 \
    --memory 1Gi \
    --cpu 1 \
    --min-instances 0 \
    --max-instances 5 \
    --set-env-vars "GCS_LOG_BUCKET=${BUCKET_NAME},GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"

# 3. Retrieve service URL
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" --platform managed --region "${REGION}" --format="value(status.url)")

echo "======================================================================"
echo "  Deployment Complete!"
echo "  Dashboard URL: ${SERVICE_URL}"
echo "======================================================================"
