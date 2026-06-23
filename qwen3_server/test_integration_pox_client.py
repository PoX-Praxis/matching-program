"""
統合テスト: PoX クライアント（embedding_service）⇄ Qwen3 サーバの結線確認

実モデル不要。Flask test_client をサーバ実体にし、PoX 側 _qwen3_encode が出す
HTTP 呼び出し（urllib）を test_client にルートして往復させる。これで
「POX_EMBED_BACKEND=qwen3 に切り替えれば本当に動く」ことをモデル無しで担保する。
"""
import sys, os, io, json, urllib.request

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from server import create_app
from test_server import FakeEmbedder, DIM

import embedding_service
from embedding_config import FULL_DIM, SHORT_DIM


# ── Flask test_client を urllib.urlopen に見せかけるシム ──────────────────────
_client = create_app(FakeEmbedder()).test_client()


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _fake_urlopen(req, timeout=None):
    # req は urllib.request.Request。/embed に POST する。
    resp = _client.post("/embed", data=req.data, content_type="application/json")
    return _Resp(resp.data)


def _with_qwen3_backend(fn):
    """embedding_service を qwen3 バックエンドに切替え、urlopen を差し替えて実行。"""
    orig_backend = embedding_service.BACKEND
    orig_endpoint = embedding_service.QWEN3_ENDPOINT
    orig_urlopen = urllib.request.urlopen
    embedding_service.BACKEND = "qwen3"
    embedding_service.QWEN3_ENDPOINT = "http://test.local/embed"
    urllib.request.urlopen = _fake_urlopen
    try:
        return fn()
    finally:
        embedding_service.BACKEND = orig_backend
        embedding_service.QWEN3_ENDPOINT = orig_endpoint
        urllib.request.urlopen = orig_urlopen


def test_qwen3_encode_returns_full_dim():
    vec = _with_qwen3_backend(lambda: embedding_service._qwen3_encode("テキスト"))
    assert len(vec) == FULL_DIM == DIM


def test_embed_via_qwen3_backend_full_and_short():
    """embed() が qwen3 経由で full(1024)+short(256) を返す（MRL 切出しはクライアント側）。"""
    full, short = _with_qwen3_backend(lambda: embedding_service.embed("意志", "query"))
    assert len(full) == FULL_DIM
    assert len(short) == SHORT_DIM


def test_embed_qwen3_is_l2_normalized():
    import math
    full, short = _with_qwen3_backend(lambda: embedding_service.embed("x", "passage"))
    assert abs(math.sqrt(sum(v * v for v in full)) - 1.0) < 1e-6
    assert abs(math.sqrt(sum(v * v for v in short)) - 1.0) < 1e-6


def test_prefix_applied_client_side_only():
    """
    role による prefix 差はクライアント側で付く（サーバには加工しない）。
    query には instruction が付くので passage と異なる text がサーバに届く
    → 返るベクトルも異なる（FakeEmbedder は text 依存の決定論的出力）。
    """
    q = _with_qwen3_backend(lambda: embedding_service.embed("同一", "query")[0])
    p = _with_qwen3_backend(lambda: embedding_service.embed("同一", "passage")[0])
    assert q != p, "query/passage で別 text が届き別ベクトルになる（H-1 単一ソース）"


def test_build_vectors_via_qwen3():
    """build_vectors が qwen3 経由でも 8 ベクトルを正しい次元で返す。"""
    profile = {"will_text": "つなぎたい", "state_have": "現場知識"}
    v = _with_qwen3_backend(
        lambda: embedding_service.build_vectors(profile, "必要像テキスト"))
    for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query"):
        assert len(v[k]) == FULL_DIM
    for k in ("will_sym_256", "will_pas_256", "state_pas_256", "necessity_q_256"):
        assert len(v[k]) == SHORT_DIM


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  PASS: {t.__name__}")
    print(f"\nPoX⇄Qwen3 統合テスト: {len(tests)} 件 全 PASS")
