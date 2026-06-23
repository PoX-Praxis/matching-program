"""
Qwen3-Embedding 常駐推論サーバ（PoX v4 / C章）

PoX 本体の embedding_service._qwen3_encode が叩く HTTP 契約:
  POST {endpoint}          body {"text": str, "model_tag"?: str}
                           -> 200 {"embedding": [float×1024], "dim": 1024, "model_tag": str}
  POST {endpoint}（バッチ） body {"texts": [str, ...], "model_tag"?: str}
                           -> 200 {"embeddings": [[...], ...], "dim": 1024, ...}
  GET  /health             -> 200 {"status":"ok","model":...,"dim":...,"loaded":bool}

PoX 側の設定（この URL を指す）:
  POX_EMBED_BACKEND=qwen3
  POX_QWEN3_ENDPOINT=http://<host>:8000/embed
  POX_EMBED_MODEL_TAG=<このサーバの QWEN3_MODEL_TAG と必ず一致させる>

model_tag 整合（I章 跨プール禁止の境界防御）:
  リクエストの model_tag がサーバ設定と違う場合、QWEN3_STRICT_MODEL_TAG=1 なら 409。
  既定は警告のみ（混在は PoX 側 WHERE model_tag=... でも防がれる）。
"""
import os
from flask import Flask, request, jsonify

from model import Qwen3Embedder, MODEL_TAG as SERVER_MODEL_TAG

STRICT_TAG = os.environ.get("QWEN3_STRICT_MODEL_TAG", "0") == "1"


def create_app(embedder, *, server_model_tag=SERVER_MODEL_TAG, strict_tag=STRICT_TAG):
    """
    Flask アプリを生成。embedder を注入できる（テストはモック注入で torch 不要）。
    """
    app = Flask(__name__)

    def _check_tag(req_tag):
        """model_tag 不一致を検査。strict なら拒否理由文字列、許容なら None。"""
        if req_tag and req_tag != server_model_tag:
            msg = (f"model_tag 不一致: request={req_tag!r} server={server_model_tag!r}。"
                   f"同一プールには同一 model_tag のベクトルのみ（I章）。")
            if strict_tag:
                return msg
            app.logger.warning(msg)
        return None

    @app.get("/health")
    def health():
        return jsonify({
            "status": "ok",
            "model": embedder.model_name,
            "model_tag": server_model_tag,
            "dim": embedder.expected_dim,
            "loaded": embedder.loaded,
        })

    @app.post("/embed")
    def embed():
        body = request.get_json(force=True, silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "JSON body が必要です"}), 400

        reject = _check_tag(body.get("model_tag"))
        if reject:
            return jsonify({"error": reject}), 409

        # バッチ（texts）優先、無ければ単一（text）
        if "texts" in body:
            texts = body["texts"]
            if not isinstance(texts, list) or not all(isinstance(t, str) for t in texts):
                return jsonify({"error": "texts は文字列の配列である必要があります"}), 400
            try:
                vecs = embedder.encode_batch(texts)
            except Exception as e:
                return jsonify({"error": f"埋め込み失敗: {e}"}), 500
            return jsonify({"embeddings": vecs, "dim": embedder.expected_dim,
                            "model_tag": server_model_tag})

        text = body.get("text")
        if not isinstance(text, str):
            return jsonify({"error": "text（文字列）または texts（配列）が必要です"}), 400
        try:
            vec = embedder.encode(text)
        except Exception as e:
            return jsonify({"error": f"埋め込み失敗: {e}"}), 500
        return jsonify({"embedding": vec, "dim": embedder.expected_dim,
                        "model_tag": server_model_tag})

    return app


def build_default_app():
    """本番用: env からモデルを構成し、起動時に warmup（コールドスタート前倒し）。"""
    embedder = Qwen3Embedder()
    if os.environ.get("QWEN3_WARMUP", "1") == "1":
        embedder.warmup()
    return create_app(embedder)


# gunicorn 'server:app' / 'wsgi:app' から参照される遅延構築用
def _lazy_app():
    embedder = Qwen3Embedder()        # warmup しない（/health 即応、初 /embed でロード）
    return create_app(embedder)


if __name__ == "__main__":
    host = os.environ.get("QWEN3_HOST", "0.0.0.0")
    port = int(os.environ.get("QWEN3_PORT", "8000"))
    build_default_app().run(host=host, port=port)
