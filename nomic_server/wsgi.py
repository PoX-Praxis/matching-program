"""
gunicorn 入口（Render 本番）。nomic_server/ 内で:
    gunicorn -w 1 -t 120 -b 0.0.0.0:$PORT wsgi:app
モデルを1回ロードして常駐（-w 1 推奨: 埋め込みモデルはメモリを食うため単一ワーカー）。
/health は認証不要、/embed は NOMIC_SERVER_API_KEY 設定時に X-API-Key 検証。
"""
from server import build_default_app

app = build_default_app()
