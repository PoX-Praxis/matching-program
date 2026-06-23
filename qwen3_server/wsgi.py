"""
gunicorn エントリポイント: `gunicorn -b 0.0.0.0:8000 wsgi:app`

QWEN3_WARMUP=1（既定）なら worker 起動時にモデルをロードする。
コールドスタートを避けたい本番ではこのまま。/health を即応させたい場合は
QWEN3_WARMUP=0 にして初回 /embed 時にロードさせる。
"""
from server import build_default_app

app = build_default_app()
