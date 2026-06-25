"""
EmbeddingGemma-300M 常駐推論サーバ（PoX embedding 実験）

PoX 本体との HTTP 契約（qwen3_server と同一インターフェース）:
  POST /embed  body {"text": str, "model_tag"?: str}
               -> 200 {"embedding": [float×768], "dim": 768, "model_tag": str}
  POST /embed  body {"texts": [str,...], "model_tag"?: str}
               -> 200 {"embeddings": [[...], ...], "dim": 768, ...}
  GET  /health -> 200 {"status":"ok","model":...,"dim":...,"loaded":bool}

PoX 側の設定:
  POX_EMBED_BACKEND=embgemma
  POX_EMBGEMMA_ENDPOINT=http://localhost:8001/embed
  POX_EMBED_MODEL_TAG=embgemma-300m
  POX_EMBED_FULL_DIM=768
"""
import os
from flask import Flask, request, jsonify
from model import EmbGemmaEmbedder, MODEL_TAG as SERVER_MODEL_TAG

STRICT_TAG = os.environ.get("EMBGEMMA_STRICT_MODEL_TAG", "0") == "1"


def create_app(embedder, *, server_model_tag=SERVER_MODEL_TAG, strict_tag=STRICT_TAG):
    app = Flask(__name__)

    def _check_tag(req_tag):
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
    embedder = EmbGemmaEmbedder()
    if os.environ.get("EMBGEMMA_WARMUP", "1") == "1":
        embedder.warmup()
    return create_app(embedder)


if __name__ == "__main__":
    host = os.environ.get("EMBGEMMA_HOST", "0.0.0.0")
    port = int(os.environ.get("EMBGEMMA_PORT", "8001"))
    build_default_app().run(host=host, port=port)
