# 手順書② — Nomic Embed v2 常駐サービスを Render に新規作成（束2a / A-2）

`nomic_server/` を Render の独立 Web Service として立て、`pox-web` から HTTP で叩ける
埋め込み推論エンドポイントにする。**この段階では `pox-web` の `POX_EMBED_BACKEND` は
`stub` のまま**（実接続への切替は束2d）。ここではサービスを用意し `/health` で疎通と
実次元を確定するところまで。

- HTTP 契約: `POST /embed {"text": str}` → `{"embedding": [float×768], "dim": 768, "model_tag": str}`、
  `GET /health` → `{"status":"ok","dim":...,"loaded":bool}`（認証不要）。
- モデル: `nomic-ai/nomic-embed-text-v2-moe`（Apache-2.0、`trust_remote_code=True` 必須）。
  総パラメータ約475M（MoE、活性約305M）。実次元は768（`/health` で最終確認する）。

---

## A. Nomic モデルの取得方式（A-2 検討・報告）

`model.py` は `SentenceTransformer("nomic-ai/nomic-embed-text-v2-moe", trust_remote_code=True)`
でロードする。初回 `warmup()` 時に HuggingFace Hub からモデル（約1GB級）をダウンロードして
HF キャッシュ（既定 `~/.cache/huggingface`）に置く。Render は**再デプロイ/再起動でファイル
システムが揮発**するため、取得方式を選ぶ必要がある。3案と評価:

| 方式 | 仕組み | 起動 | 永続 | コスト | 判定 |
|------|--------|------|------|--------|------|
| **(1) 実行時DL** | 初回 warmup で Hub から取得。キャッシュは揮発 | 毎コールドスタートで再DL（数分） | ✗ | ディスク不要 | 検証用。本番の遅さは許容外 |
| **(2) 永続ディスク** | Render Disk を HF_HOME にマウント、初回のみDL | 2回目以降は速い | ✓ | 有料ディスク | ○ 運用が単純。推奨A |
| **(3) ビルド時ベイク** | build 時にリポジトリ配下へDLしスラグに焼く | 常に速い（DL無し） | ✓（イメージ内） | ディスク不要 | ○ 不変・高速。推奨B |

**推奨**: まず **(2) 永続ディスク**（設定が最少で確実）。イミュータブルな配布を重視するなら
**(3) ビルド時ベイク**（`HF_HOME` をリポジトリ配下に向け build コマンドで事前DL）。
どちらも `NOMIC_WARMUP=1`（既定）で起動時にモデルを常駐させ、初回リクエストの遅延を消す。

> 注: `trust_remote_code=True` は Nomic v2 のカスタムモデリングコード実行に必須。取得元は
> 公式 `nomic-ai/...` に固定する（`NOMIC_MODEL` を上書きしない限り変わらない）。

---

## B. サービス作成手順

### B-1. New → Web Service

- Render Dashboard → **New +** → **Web Service** → 同リポジトリ（`pox-praxis/matching-program`）を選択。
- **Root Directory**: `nomic_server`
- **Runtime**: Python 3
- **Instance Type**: **2GB RAM 級以上**（MoE のロードとバッチ推論に必要。1GB では OOM 懸念）。
- **Build Command**:
  ```bash
  pip install -r requirements.txt
  ```
  （方式(3)を採る場合はここに事前DLを追加。下の「方式(3)」参照）
- **Start Command**（`wsgi.py` の `app` を gunicorn で起動。`$PORT` は Render が注入）:
  ```bash
  gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app
  ```
  > workers=1 推奨: モデルをワーカーごとに二重ロードするとメモリを食う。並列は threads で稼ぐ。

### B-2. 環境変数

| 変数 | 値 | 備考 |
|------|-----|------|
| `NOMIC_MODEL_TAG` | `nomic-emb-v2` | `pox-web` の `POX_EMBED_MODEL_TAG` と一致必須（跨プール禁止・I章） |
| `NOMIC_SERVER_API_KEY` | （強いランダム文字列） | 設定すると `/embed` に API キー認証がかかる。**本番は設定推奨** |
| `NOMIC_WARMUP` | `1` | 起動時にモデルを常駐（初回リクエスト遅延を消す） |
| `NOMIC_DIM` | 空 or `768` | 空=初回 encode で自動検出。`/health` 確認後に `768` を明示すると安全 |
| `HF_HOME` | 方式(2): `/data/hf` / 方式(3): `/opt/render/project/src/.hf` | モデルキャッシュ先 |

> `PORT` は設定不要（Render が注入し、`server.py`/gunicorn が拾う）。`NOMIC_STRICT_MODEL_TAG`
> は既定0（警告のみ）。厳格化したい場合のみ `1`。

### 方式(2) 永続ディスク

- Service → **Disks** → **Add Disk**: Mount Path `/data`、サイズ 5GB 程度。
- 環境変数 `HF_HOME=/data/hf` を設定。初回起動でDLされ、以後の再起動はキャッシュ再利用。

### 方式(3) ビルド時ベイク（ディスク不要）

- 環境変数 `HF_HOME=/opt/render/project/src/.hf`（リポジトリ配下＝スラグに焼かれる）。
- Build Command を次のように拡張（build 中にDLしてキャッシュへ）:
  ```bash
  pip install -r requirements.txt && \
  python -c "import os; os.environ.setdefault('HF_HOME','/opt/render/project/src/.hf'); \
  from sentence_transformers import SentenceTransformer; \
  SentenceTransformer('nomic-ai/nomic-embed-text-v2-moe', trust_remote_code=True)"
  ```

---

## C. 疎通と実次元の確定

デプロイ後、サービス URL（例 `https://pox-nomic.onrender.com`）に対して:

```bash
# 認証不要。dim と loaded を確認する
curl -s https://pox-nomic.onrender.com/health
# → {"status":"ok","model":"nomic-ai/nomic-embed-text-v2-moe","model_tag":"nomic-emb-v2","dim":768,"loaded":true}
```

- `dim` の値が **実次元**。これを手順書① の `POX_EMBED_FULL_DIM` に設定する（想定768）。
- `loaded: true`（`NOMIC_WARMUP=1` なら起動直後に true）。

`/embed` の認証確認（`NOMIC_SERVER_API_KEY` を設定した場合）:

```bash
# キー無し → 401
curl -s -X POST https://pox-nomic.onrender.com/embed \
  -H 'content-type: application/json' -d '{"text":"疎通テスト"}'
# → {"error":"unauthorized（X-API-Key 不一致）"}

# キー有り → 200・768次元
curl -s -X POST https://pox-nomic.onrender.com/embed \
  -H 'content-type: application/json' -H "X-API-Key: <NOMIC_SERVER_API_KEY>" \
  -d '{"text":"疎通テスト"}' | python -c "import sys,json; d=json.load(sys.stdin); print('dim=',d['dim'])"
```

---

## D. pox-web 側の接続情報（束2d で有効化）

Nomic サービスが疎通したら、`pox-web` に接続情報を入れておく（**バックエンド切替は束2d**）:

| 変数 | 値 |
|------|-----|
| `POX_NOMIC_ENDPOINT` | `https://pox-nomic.onrender.com/embed` |
| `POX_NOMIC_API_KEY` | `NOMIC_SERVER_API_KEY` と同じ値（`_nomic_encode` が `X-API-Key` で送る） |
| `POX_EMBED_BACKEND` | `stub`（据え置き。**束2d で `nomic` に切替**） |

束2d で `POX_EMBED_BACKEND=nomic` にした時点で、`pox-web` の登録/照合が実 Nomic ベクトルを
使い始める。それまでは stub で768次元の配管を検証できる。
