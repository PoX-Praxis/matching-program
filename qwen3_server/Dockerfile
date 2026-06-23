# Qwen3-Embedding-0.6B 常駐推論サーバ
# build: docker build -t pox-qwen3 .
# run  : docker run -p 8000:8000 pox-qwen3
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    HF_HOME=/app/.hf \
    QWEN3_WARMUP=1

# CPU 専用イメージにする場合は下の torch を CPU wheel に差し替えてサイズを削減:
#   RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 任意: ビルド時にモデルを焼き込むとコールドスタートの初回ダウンロードを回避できる
# （イメージは ~2.5GB 大きくなる）。必要ならコメント解除:
# ARG QWEN3_MODEL=Qwen/Qwen3-Embedding-0.6B
# RUN python -c "from sentence_transformers import SentenceTransformer as S; S('${QWEN3_MODEL}')"

EXPOSE 8000
# -w 1: 0.6B モデルを worker ごとにロードするためメモリに注意（1 worker 推奨）
# -t 300: 初回ロード/長文のタイムアウト余裕
CMD ["gunicorn", "-b", "0.0.0.0:8000", "-w", "1", "-t", "300", "wsgi:app"]
