@echo off
REM ==============================================================================
REM Deploy STL Digital Enterprise Voice Bot KPI Dashboard to Google Cloud Run
REM ==============================================================================

set SERVICE_NAME=stl-voicebot-kpi-dashboard
set REGION=us-central1
set BUCKET_NAME=stld-bkt-data-prod-voicebot-log-uscentral01-001
set PROJECT_ID=voicebot-support
set IMAGE_TAG=gcr.io/%PROJECT_ID%/%SERVICE_NAME%:latest

echo ======================================================================
echo   Deploying STL Voice Bot KPI Dashboard to Google Cloud Run
echo   Service Name : %SERVICE_NAME%
echo   Region       : %REGION%
echo   GCS Bucket   : %BUCKET_NAME%
echo   Image Tag    : %IMAGE_TAG%
echo ======================================================================

echo [1/3] Building container image with Google Cloud Build...
gcloud builds submit --tag %IMAGE_TAG% .

echo [2/3] Deploying to Google Cloud Run...
gcloud run deploy %SERVICE_NAME% ^
    --image %IMAGE_TAG% ^
    --platform managed ^
    --region %REGION% ^
    --allow-unauthenticated ^
    --port 8080 ^
    --memory 1Gi ^
    --cpu 1 ^
    --set-env-vars GCS_LOG_BUCKET=%BUCKET_NAME%,GOOGLE_CLOUD_PROJECT=%PROJECT_ID%

echo ======================================================================
echo   Deployment finished!
echo ======================================================================
pause
