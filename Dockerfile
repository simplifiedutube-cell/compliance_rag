# Local embeddings run in-process, so this image needs the ONNX runtime and
# ~1 GB of RAM at query time. The index itself is tiny (~3 MB).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed

WORKDIR /app

# Dependencies first so edits to source do not re-download the wheels.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bake the embedding model into the image (~65 MB) rather than downloading it
# on first request. Without this the first query after every deploy is slow
# and silently depends on HuggingFace being reachable from the VPS.
RUN python -c "from fastembed import TextEmbedding; \
    import os; TextEmbedding(os.getenv('LOCAL_EMBED_MODEL','BAAI/bge-small-en-v1.5'))"

# Build the index at image build time so the container starts ready to serve.
# Needs network egress to legislation.gov.au during build only.
RUN python -m index.build || \
    echo "index build skipped -- mount a prebuilt data/ volume instead"

EXPOSE 8000

# Single worker: the embedding model is held in-process and each worker would
# hold its own copy. Scale with replicas, not workers, if you ever need to.
# $PORT is injected by hosts like Render; falls back to 8000 for
# docker-compose, which sets it explicitly.
CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
