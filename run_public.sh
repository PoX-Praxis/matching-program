#!/usr/bin/env bash
# PoX を公開モードで起動（debugger オフ）。起動後、別ターミナルでトンネルを張る。
#   cloudflared tunnel --url http://localhost:5000
#   または ngrok http 5000
set -e
cd "$(dirname "$0")"
export POX_DEBUG=0
export POX_PORT="${POX_PORT:-5000}"
echo "PoX をポート ${POX_PORT} で起動します（debug オフ）。"
echo "別ターミナルで: cloudflared tunnel --url http://localhost:${POX_PORT}"
python app.py
