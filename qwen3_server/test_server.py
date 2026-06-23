"""
Qwen3 サーバ契約テスト（torch / 実モデル不要・モック注入）

PoX 本体 embedding_service._qwen3_encode が期待する HTTP 契約を固定する:
  POST /embed {"text"} -> {"embedding":[1024], "dim":1024}
  バッチ {"texts"} -> {"embeddings":[...]}
  GET /health -> loaded フラグ等
  model_tag 不一致は strict で 409 / 非 strict で 200
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from server import create_app

DIM = 1024


class FakeEmbedder:
    """sentence-transformers を使わない決定論的モック（契約検証用）。"""
    model_name = "fake/qwen3"
    expected_dim = DIM

    def __init__(self):
        self.loaded = False
        self.calls = []

    def warmup(self):
        self.loaded = True

    def encode(self, text):
        self.warmup()
        self.calls.append(text)
        return [float((hash(text) >> i) & 1) for i in range(DIM)]

    def encode_batch(self, texts):
        return [self.encode(t) for t in texts]


def _client(strict=False, tag="qwen3-embedding-0.6b-d1024"):
    app = create_app(FakeEmbedder(), server_model_tag=tag, strict_tag=strict)
    return app.test_client()


def test_embed_single_contract():
    """単一 text -> embedding（長さ 1024）+ dim。"""
    r = _client().post("/embed", json={"text": "意志のテキスト"})
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["embedding"]) == DIM
    assert data["dim"] == DIM
    assert "model_tag" in data


def test_embed_deterministic_same_text():
    c = _client()
    a = c.post("/embed", json={"text": "同じ"}).get_json()["embedding"]
    b = c.post("/embed", json={"text": "同じ"}).get_json()["embedding"]
    assert a == b


def test_embed_batch_contract():
    r = _client().post("/embed", json={"texts": ["a", "b", "c"]})
    assert r.status_code == 200
    data = r.get_json()
    assert len(data["embeddings"]) == 3
    assert all(len(v) == DIM for v in data["embeddings"])


def test_embed_missing_text_400():
    assert _client().post("/embed", json={"foo": "bar"}).status_code == 400


def test_embed_bad_json_400():
    r = _client().post("/embed", data="not json", content_type="application/json")
    assert r.status_code == 400


def test_embed_batch_bad_type_400():
    assert _client().post("/embed", json={"texts": [1, 2]}).status_code == 400


def test_health_reports_dim_and_loaded():
    r = _client().get("/health")
    assert r.status_code == 200
    d = r.get_json()
    assert d["status"] == "ok"
    assert d["dim"] == DIM
    assert d["loaded"] is False, "health は warmup を強制しない（即応）"


def test_model_tag_mismatch_strict_409():
    r = _client(strict=True).post("/embed",
                                  json={"text": "x", "model_tag": "別モデル"})
    assert r.status_code == 409


def test_model_tag_mismatch_lenient_200():
    r = _client(strict=False).post("/embed",
                                   json={"text": "x", "model_tag": "別モデル"})
    assert r.status_code == 200


def test_model_tag_match_ok():
    r = _client(strict=True, tag="my-tag").post("/embed",
                                                json={"text": "x", "model_tag": "my-tag"})
    assert r.status_code == 200


def test_health_then_embed_marks_loaded():
    app = create_app(FakeEmbedder())
    c = app.test_client()
    assert c.get("/health").get_json()["loaded"] is False
    c.post("/embed", json={"text": "x"})
    assert c.get("/health").get_json()["loaded"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nQwen3 サーバ契約テスト: {len(tests)} 件 全 PASS")
