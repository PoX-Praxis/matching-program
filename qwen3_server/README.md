# PoX Qwen3-Embedding 推論サーバ

PoX v4 の embedding バックエンドを `stub`（決定論ハッシュ）から実 Qwen3-Embedding-0.6B
に差し替えるための常駐推論サーバ。PoX 本体のコードは一切変更せず、環境変数だけで切替える。

```
PoX 本体 (embedding_service._qwen3_encode)
   │  POST {"text", "model_tag"}        prefix は PoX 側で付与済み
   ▼
このサーバ /embed  ──►  Qwen3-Embedding-0.6B  ──►  {"embedding":[1024], "dim":1024}
```

## 契約（PoX 本体と固定）

| Method | Path | Body | Response |
|--------|------|------|----------|
| `GET`  | `/health` | — | `{"status":"ok","model":...,"model_tag":...,"dim":1024,"loaded":bool}` |
| `POST` | `/embed`  | `{"text": str, "model_tag"?: str}` | `{"embedding":[float×1024],"dim":1024,"model_tag":str}` |
| `POST` | `/embed`  | `{"texts":[str,...], "model_tag"?: str}` | `{"embeddings":[[...],...],"dim":1024,...}` |

**重要**: query 用の instruction prefix は PoX 側 `embedding_config.PREFIX` で付与済みの
テキストが届く。このサーバは prefix を**足さない**（非対称ロジックの単一ソースを H-1 に保つ）。

## ローカル実行

```bash
cd qwen3_server
pip install -r requirements.txt          # CPU だけなら下記 CPU wheel を推奨
python server.py                         # 0.0.0.0:8000、起動時にモデルをロード(warmup)
```

CPU 専用（イメージ/メモリ削減）:
```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install flask gunicorn sentence-transformers numpy
```

別ターミナルで疎通確認:
```bash
python smoke_test.py http://localhost:8000
```

## Docker

```bash
docker build -t pox-qwen3 .
docker run -p 8000:8000 pox-qwen3
```

## PoX 本体を実 Qwen3 に向ける

PoX（Render の pox-web 等）に環境変数を設定:

```
POX_EMBED_BACKEND=qwen3
POX_QWEN3_ENDPOINT=http://<このサーバのホスト>:8000/embed
POX_EMBED_MODEL_TAG=qwen3-embedding-0.6b-d1024   # サーバ QWEN3_MODEL_TAG と一致必須
```

設定後、新規登録/更新分から実ベクトルになる。**既存の stub ベクトルとは混ぜない**こと
（model_tag が同じだと別意味のベクトルが同居してしまう）。バックエンド切替時は
`POX_EMBED_MODEL_TAG` を新しい値（例 `...-d1024-v1`）に変え、新プールとして作り直すのが安全
（I章 跨プール禁止）。サーバ側 `QWEN3_STRICT_MODEL_TAG=1` で境界でも不一致を 409 拒否できる。

## 設定（環境変数）

| 変数 | 既定 | 説明 |
|------|------|------|
| `QWEN3_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | ロードするモデル |
| `QWEN3_MODEL_TAG` | `qwen3-embedding-0.6b-d1024` | 供給ベクトルの model_tag |
| `QWEN3_DIM` | `1024` | 期待出力次元（不一致は 500） |
| `QWEN3_DEVICE` | 自動 | `cpu` / `cuda` / `cuda:0` |
| `QWEN3_WARMUP` | `1` | 起動時にモデルをロード（0 で初回 /embed 時） |
| `QWEN3_STRICT_MODEL_TAG` | `0` | 1 で model_tag 不一致を 409 拒否 |
| `QWEN3_HOST` / `QWEN3_PORT` | `0.0.0.0` / `8000` | バインド先 |

## ホスティング要件

Qwen3-Embedding-0.6B は約 0.6B パラメータ（fp16 で ~1.2GB、fp32 で ~2.4GB）。

- **CPU**: 最低 ~2–3GB RAM。1 件あたり数百 ms 程度（コア数次第）。少人数のデモなら十分。
- **GPU**: 任意。大量バッチ/低レイテンシが要るときのみ。
- **Render 無料枠（512MB）では動かない**。実ホストは下記いずれかを使う:
  - 手元PC + トンネル（Cloudflare 等）でデモ公開（常時起動ではない）
  - RAM 2GB+ の有料 VPS / Render 有料 / Fly.io / Railway 等
  - Hugging Face Inference Endpoints / Modal / RunPod 等のサーバレス GPU

## テスト

```bash
python test_server.py                  # 契約テスト（モック・torch 不要）
python test_integration_pox_client.py  # PoX クライアントとの往復（モデル不要）
python smoke_test.py <URL>             # 実起動サーバへの疎通
```
