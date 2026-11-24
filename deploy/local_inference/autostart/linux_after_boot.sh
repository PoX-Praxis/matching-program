#!/usr/bin/env bash
# PoX の llama-server を、Linux の起動時に確かめ、PoX に知らせる（指示書81 §4）。
# 入れ物は docker の --restart unless-stopped で立ち上がる。トンネルは cloudflared のサービス（README の 8）。
set -u
cd "$(dirname "$0")/.."
for i in $(seq 1 60); do docker info >/dev/null 2>&1 && break; sleep 10; done
python3 start_server.py --if-missing
python3 check_server.py --health
python3 nudge_pox.py
