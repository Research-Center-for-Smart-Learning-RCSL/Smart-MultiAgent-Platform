# Bundled local reranker (bge-reranker-v2-m3, CPU) — R10.08 / F-19.
# Internal service only; never exposed on the public network. The model weights
# are baked into the image at build so the runtime container needs no network
# (it runs on the internal data_net).
FROM python:3.12-slim

WORKDIR /app

# CPU-only torch keeps the image lean and avoids pulling CUDA wheels. Installed
# from the PyTorch CPU index before FlagEmbedding so its torch dependency is
# already satisfied by the CPU build.
RUN pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu torch==2.5.1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Warm the model into the image layer so first boot is instant and offline.
RUN python -c "from FlagEmbedding import FlagReranker; FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=False)"

COPY app.py .

EXPOSE 80
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "80"]
