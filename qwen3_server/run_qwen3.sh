#!/usr/bin/env bash
# Qwen3-Embedding 推論サーバをローカル起動。起動後、別ターミナルでトンネルを張る:
#   cloudflared tunnel --url http://localhost:8000
#   または ngrok http 8000
# 初回だけ依存インストール:  ./run_qwen3.sh --install
set -e
cd "$(dirname "$0")"

export QWEN3_PORT="${QWEN3_PORT:-8000}"
export QWEN3_WARMUP="${QWEN3_WARMUP:-1}"   # 起動時にモデルをロード（初回リクエストを速く）

if [ "$1" = "--install" ]; then
  echo "依存をインストールします（数分・torch を含むため数百MB）..."
  echo "  CPU 専用で軽くしたい場合は先に:"
  echo "  pip install torch --index-url https://download.pytorch.org/whl/cpu"
  pip install -r requirements.txt
fi

echo "Qwen3 サーバをポート ${QWEN3_PORT} で起動します。"
echo "  初回はモデル（~1.2GB）を Hugging Face から自動DLします（少し待ちます）。"
echo "  別ターミナルで: cloudflared tunnel --url http://localhost:${QWEN3_PORT}"
python server.py
