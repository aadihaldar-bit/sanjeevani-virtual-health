FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMP_NUM_THREADS=2 \
    KYC_MODEL_PACK=buffalo_l

# build-essential only for insightface's small Cython extension; libgl/glib for OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir "numpy==1.26.4" "cython<3.1" \
 && pip install --no-cache-dir -r requirements.txt

# bake the face models into the image (downloaded from the insightface GitHub release)
RUN python -c "from insightface.app import FaceAnalysis; a=FaceAnalysis(name='buffalo_l', allowed_modules=['detection','recognition','landmark_3d_68'], providers=['CPUExecutionProvider']); a.prepare(ctx_id=-1, det_size=(640,640)); print('models baked:', list(a.models))"

COPY certs ./certs
COPY *.py ./
COPY tests ./tests

RUN mkdir -p /data
EXPOSE 9000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fs http://127.0.0.1:9000/healthz || exit 1

# single worker: ONNX models are loaded once and CPU is the bottleneck
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9000", "--workers", "1"]
