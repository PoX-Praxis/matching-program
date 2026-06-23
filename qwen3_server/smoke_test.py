"""
実サーバ スモークテスト（実モデルを起動した後に外から叩く）

使い方:
  python smoke_test.py http://localhost:8000

確認内容:
  /health が ok / /embed が 1024 次元・~単位ベクトルを返す / 同一入力が決定論的。
"""
import sys, json, math, urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=120) as r:
        return json.loads(r.read())


def _post(path, body):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.loads(r.read())


def main():
    print(f"対象: {BASE}")
    h = _get("/health")
    print(f"  /health -> {h}")
    assert h["status"] == "ok"
    dim = h["dim"]

    a = _post("/embed", {"text": "農家と飲食店を直接つなぎたい"})["embedding"]
    assert len(a) == dim, f"次元 {len(a)} != {dim}"
    norm = math.sqrt(sum(x * x for x in a))
    print(f"  /embed dim={len(a)} L2={norm:.4f}")
    assert abs(norm - 1.0) < 1e-3, "L2 正規化されていること"

    b = _post("/embed", {"text": "農家と飲食店を直接つなぎたい"})["embedding"]
    assert a == b, "同一入力は決定論的であること"

    batch = _post("/embed", {"texts": ["x", "y"]})["embeddings"]
    assert len(batch) == 2 and all(len(v) == dim for v in batch)
    print(f"  /embed batch=2 OK")

    print("\nスモークテスト 全 PASS。PoX 側に次を設定してください:")
    print("  POX_EMBED_BACKEND=qwen3")
    print(f"  POX_QWEN3_ENDPOINT={BASE}/embed")
    print(f"  POX_EMBED_MODEL_TAG={h['model_tag']}  # サーバと一致必須")


if __name__ == "__main__":
    main()
