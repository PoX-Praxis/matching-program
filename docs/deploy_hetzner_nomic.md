# Nomic 推論サーバー構築手順（Hetzner VPS + Cloudflare Tunnel）

**目的**: 採用モデル `nomic-emb-v2` の本番推論サーバーを Hetzner Cloud VPS に構築し、
Cloudflare Tunnel 経由で pox-web(Render) から安全に呼べる状態にする。

**構成**:
```
[利用者] → pox-web (Render, 無料) ──HTTPS(APIキー)──▶ Cloudflare Tunnel ──▶ Hetzner VPS
                                                                          localhost:8002
                                                                          nomic_server (gunicorn)
        pox-db (Render, Basic) にデータ保管
```
- Hetzner の Nomic ポートは**外部に一切開けない**（ufw で塞ぐ）。到達は **Cloudflare Tunnel 経由のみ**。
- 認証は二重: ①Cloudflare Tunnel で経路暗号化 ②既存 `NOMIC_SERVER_API_KEY`（`X-API-Key` 検証）。
- **コード変更なし**。既存 `nomic_server/`（commit e7fcddd 以降）をそのまま動かす。

**あなたが用意する秘密の値（1つ）**: `NOMIC_SERVER_API_KEY` = 長いランダム文字列（32文字以上推奨）。
Hetzner 側（サーバー）と pox-web 側（Render）で**同じ値**を使う。メモしておく。

> サーバー: **4 vCPU / 8GB RAM クラス**を使う。Hetzner の現行 SKU では **CX32（Intel）** または
> **CPX31（AMD）** が該当（指示書の "CX33" 相当）。8GB あるので **float32** でも余裕で載る。

---

# Part 1: Hetzner サーバーの準備（Hetzner Cloud コンソール）

## 1-1. SSH 鍵を用意（無ければ作る）
自分のPCのターミナル（Windows は PowerShell）で：

```bash
ssh-keygen -t ed25519 -C "pox-nomic"
```
- 何をするか: サーバーに安全にログインするための鍵ペアを作る。
- 保存先を聞かれたら Enter（既定 `~/.ssh/id_ed25519`）。パスフレーズは任意。
- **公開鍵**の中身を表示してコピー（この後 Hetzner に貼る）:
  ```bash
  cat ~/.ssh/id_ed25519.pub
  ```
- 成功の見分け方: `ssh-ed25519 AAAA... pox-nomic` の1行が表示される。

## 1-2. サーバー作成（Hetzner Cloud コンソール）
1. https://console.hetzner.cloud → プロジェクトを開く（無ければ New Project）。
2. **Add Server** をクリック。
3. **Location**: EU（例 Nuremberg / Falkenstein / Helsinki）を選ぶ。
4. **Image**: **Ubuntu 24.04**。
5. **Type**: タブ **Shared vCPU** →（コスト重視なら **CPX31**、Intel が良ければ **CX32**）。
   いずれも **4 vCPU / 8 GB RAM**。
6. **SSH keys**: **Add SSH key** → 1-1 でコピーした公開鍵を貼り付け → 名前を付けて保存。
7. 名前（Name）を `pox-nomic` に。
8. **Create & Buy now**。
   - 成功の見分け方: サーバー一覧に `pox-nomic` が現れ、**公開IP（Public IP）** が表示される。
9. **公開IPを控える**（例 `203.0.113.45`）。以降 `<IP>` と表記。

## 1-3. 初回 SSH 接続
自分のPCから：
```bash
ssh root@<IP>
```
- 何をするか: サーバーに管理者(root)でログイン。
- 初回は `Are you sure...` と聞かれたら `yes`。
- 成功の見分け方: プロンプトが `root@pox-nomic:~#` に変わる。

## 1-4. 基本設定（root で実行）
```bash
apt update && apt -y upgrade
```
- 何をするか: パッケージ一覧の更新とセキュリティ更新。数分かかる。

作業用ユーザー（root常用を避ける）を作成：
```bash
adduser pox
usermod -aG sudo pox
rsync --archive --chown=pox:pox ~/.ssh /home/pox
```
- 何をするか: `pox` ユーザー作成 → sudo 権限付与 → SSH鍵を pox でも使えるようコピー。
- `adduser pox` はパスワードを聞かれる（設定してメモ）。フルネーム等は Enter で飛ばす。

ファイアウォール（SSH 以外は全部塞ぐ）：
```bash
ufw allow OpenSSH
ufw --force enable
ufw status
```
- 何をするか: **SSH(22番)だけ許可**し、他は遮断。Nomic のポートは**外に開けない**（Tunnel 経由のみ）。
- 成功の見分け方: `Status: active` と `OpenSSH ALLOW` が表示される。**8002 は出てこない**のが正しい。

以降は作業用ユーザーで作業。一度ログアウトして入り直す：
```bash
exit
ssh pox@<IP>
```
- 成功の見分け方: プロンプトが `pox@pox-nomic:~$` になる。

---

# Part 2: Nomic サーバーの配置と起動

## 2-1. 必要ツールの導入（pox ユーザー・sudo）
```bash
sudo apt -y install python3-venv python3-pip git
```
- 何をするか: Python 仮想環境・pip・git を入れる。

## 2-2. コード取得（git clone）
リポジトリは**非公開**なので、GitHub の **Personal Access Token（PAT・読み取り専用）** が必要。
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate new token。
   - Repository access: `pox-praxis/matching-program` のみ。Permissions: Contents=Read-only。
   - 生成された `github_pat_xxx` をコピー（**一度しか表示されない**）。
2. クローン（`<PAT>` を実際の値に置換）：
```bash
cd ~
git clone https://<PAT>@github.com/pox-praxis/matching-program.git
```
- 何をするか: リポジトリを `~/matching-program` に取得（`nomic_server/` を含む）。
- 成功の見分け方: `cd ~/matching-program/nomic_server && ls` で
  `model.py server.py wsgi.py requirements.txt` が見える。
- ⚠️ PAT はこのコマンド以外に**貼らない・保存しない**。用が済んだら GitHub 側で失効させてよい。

## 2-3. Python 環境と依存インストール
```bash
cd ~/matching-program/nomic_server
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
```
- 何をするか: この場所に隔離した Python 環境を作り有効化。プロンプト先頭に `(.venv)` が付く。

**CPU 版の torch を先に入れる**（GPU用の巨大ライブラリ〈約2.5GB〉を避け、時間とディスクを節約）：
```bash
pip install torch --index-url https://download.pytorch.org/whl/cpu
```
- 何をするか: CPU専用の torch を導入。Hetzner は GPU 無しなのでこれで十分。
- 成功の見分け方: 最後に `Successfully installed torch-...+cpu` が出る（`+cpu` が付く）。

残りの依存：
```bash
pip install -r requirements.txt
```
- 何をするか: flask / sentence-transformers / einops / gunicorn 等を導入。torch は上で入済みなので再取得されない。
- 時間がかかる（数分）。失敗時の対処:
  - `No space left on device` → `df -h /` で空きを確認（8GBクラスでも十分なはずだが、他で埋まっていないか）。
  - メモリ不足でkill → `free -h` を確認（このステップは通常メモリを食わない。起動時が本番）。

## 2-4. モデルのキャッシュ場所を用意
```bash
mkdir -p ~/hf-cache
```
- 何をするか: HuggingFace からダウンロードするモデルの保存先。VPS の通常ディスクに残る（Render と違い永続）。

## 2-5. 手動起動で動作確認（初回はモデルDLで数分）
必要な環境変数を付けて gunicorn を起動（**`<KEY>` は自分で決めた長い文字列**）：
```bash
NOMIC_SERVER_API_KEY="<KEY>" \
NOMIC_MODEL_TAG="nomic-emb-v2" \
NOMIC_DIM="768" \
NOMIC_TORCH_DTYPE="float32" \
NOMIC_WARMUP="1" \
HF_HOME="/home/pox/hf-cache" \
.venv/bin/gunicorn --workers 1 --threads 4 --timeout 120 --bind 127.0.0.1:8002 wsgi:app
```
- 何をするか: Nomic サーバーを localhost:8002 で起動。`NOMIC_WARMUP=1` で起動時にモデルを読み込む。
- 初回は HuggingFace から**モデルDL（1〜2GB・数分）**。ログに
  `[NomicEmbedder] 実次元検出: 768 次元` が出れば読み込み成功。
- `127.0.0.1:8002` にバインド＝**外部からは到達不可**（Tunnel 経由のみにするため意図的）。
- `NOMIC_TORCH_DTYPE=float32`: 8GB あるので fp32 で読み込む（bf16 にしたいなら `bfloat16`）。

**別のターミナル**で同じサーバーに SSH し、疎通確認：
```bash
ssh pox@<IP>
curl -s http://localhost:8002/health
```
- 成功の見分け方: `{"status":"ok","model":"nomic-ai/nomic-embed-text-v2-moe","model_tag":"nomic-emb-v2","dim":768,"loaded":true}`。
- `dim":768` と `loaded":true` を確認できたら、最初のターミナルで **Ctrl+C** で一旦停止（次は systemd で常駐化する）。

---

# Part 3: systemd で常駐化（自動再起動）

## 3-1. 秘密の環境変数ファイル（600 権限）
```bash
sudo tee /etc/nomic.env >/dev/null <<'EOF'
NOMIC_SERVER_API_KEY=<KEY>
NOMIC_MODEL_TAG=nomic-emb-v2
NOMIC_DIM=768
NOMIC_TORCH_DTYPE=float32
NOMIC_WARMUP=1
HF_HOME=/home/pox/hf-cache
EOF
sudo sed -i 's/<KEY>/実際の長い文字列/' /etc/nomic.env
sudo chmod 600 /etc/nomic.env
```
- 何をするか: APIキー等をファイルに置き、**root のみ読める 600 権限**にする（unit ファイルに直書きしない）。
- 2行目の `実際の長い文字列` を自分のキーに置換（または `sudo nano /etc/nomic.env` で直接編集）。
- 成功の見分け方: `sudo cat /etc/nomic.env` で6行が正しく入っている。

## 3-2. サービス定義
```bash
sudo tee /etc/systemd/system/nomic.service >/dev/null <<'EOF'
[Unit]
Description=Nomic Embed v2 inference server (PoX)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pox
WorkingDirectory=/home/pox/matching-program/nomic_server
EnvironmentFile=/etc/nomic.env
ExecStart=/home/pox/matching-program/nomic_server/.venv/bin/gunicorn --workers 1 --threads 4 --timeout 120 --bind 127.0.0.1:8002 wsgi:app
Restart=always
RestartSec=5
TimeoutStartSec=600

[Install]
WantedBy=multi-user.target
EOF
```
- 何をするか: 落ちたら自動再起動（`Restart=always`）、サーバー再起動でも自動開始する常駐設定。
- `TimeoutStartSec=600`: 初回モデルロードの余裕。

## 3-3. 起動・自動起動登録
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nomic
sudo systemctl status nomic --no-pager
```
- 何をするか: サービスを起動し、OS起動時の自動立ち上げを登録。
- 成功の見分け方: `active (running)` と緑表示。
- ログを見る：
  ```bash
  journalctl -u nomic -f
  ```
  `実次元検出: 768 次元` が出れば読み込み完了。**Ctrl+C** で log 表示を抜ける（サービスは動き続ける）。
- 再起動テスト（任意）: `sudo reboot` → 数分後に再SSH → `curl -s http://localhost:8002/health` が返れば自動復帰OK。

---

# Part 4: Cloudflare Tunnel の設定

Hetzner の 8002 は外に開けない。Cloudflare Tunnel が **localhost:8002 → 公開HTTPS URL** を橋渡しする。
2通りある。**本番は「A. 名前付きトンネル（安定URL）」を推奨**。ドメインが無ければ「B. クイックトンネル」。

## 4-A. 名前付きトンネル（安定・推奨／独自ドメインが必要）
> 名前付きトンネルは **Cloudflare の無料プランに追加した独自ドメイン**が必要（安価な .xyz 等でも可）。
> URL が固定（例 `https://nomic.example.com`）で、再起動しても変わらない＝本番向き。

### 4-A-1. cloudflared インストール
```bash
curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o cloudflared.deb
sudo dpkg -i cloudflared.deb
cloudflared --version
```
- 成功の見分け方: `cloudflared version ...` が表示。

### 4-A-2. ログイン & トンネル作成
```bash
cloudflared tunnel login
```
- 何をするか: 表示されたURLをPCのブラウザで開き、**Cloudflare にログイン→対象ドメインを選択→Authorize**。
- 成功の見分け方: `You have successfully logged in` と証明書 `~/.cloudflared/cert.pem` が保存される。

```bash
cloudflared tunnel create pox-nomic
```
- 何をするか: トンネルを作成。**Tunnel ID** と認証情報 `~/.cloudflared/<ID>.json` が作られる。
- 表示された **Tunnel ID** を控える。

### 4-A-3. DNS ルートと設定ファイル
`nomic` サブドメインをトンネルに向ける（`example.com` は自分のドメインに置換）：
```bash
cloudflared tunnel route dns pox-nomic nomic.example.com
```
設定ファイルを作成（`<ID>` を実際の Tunnel ID に置換）：
```bash
mkdir -p ~/.cloudflared
tee ~/.cloudflared/config.yml >/dev/null <<'EOF'
tunnel: <ID>
credentials-file: /home/pox/.cloudflared/<ID>.json
ingress:
  - hostname: nomic.example.com
    service: http://localhost:8002
  - service: http_status:404
EOF
```
- 何をするか: `nomic.example.com` に来たリクエストを localhost:8002 に流す設定。
- `<ID>` と `nomic.example.com` を自分の値に置換（`nano ~/.cloudflared/config.yml`）。

### 4-A-4. 常駐化
```bash
sudo cloudflared --config /home/pox/.cloudflared/config.yml service install
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared --no-pager
```
- 何をするか: トンネルを systemd で常駐（落ちても自動復帰）。
- 成功の見分け方: `active (running)`。
- **あなたの公開URL** = `https://nomic.example.com`（末尾に `/embed` を付けたものを pox-web で使う）。

## 4-B. クイックトンネル（ドメイン不要・お試し／URLは毎回変わる）
> ドメインが無い場合の暫定策。`https://<ランダム>.trycloudflare.com` が得られるが、
> **再起動のたびにURLが変わり**、pox-web 側も都度更新が必要。安定運用には向かない。まず動作確認用。

インストール（4-A-1 と同じ）後：
```bash
cloudflared tunnel --url http://localhost:8002
```
- 何をするか: 一時トンネルを張る。ログに `https://<ランダム>.trycloudflare.com` が出る。
- このプロセスを閉じるとURLが消える。常駐したい場合は systemd 化するが、URLは再起動で変わる点に注意。
- **本番運用に入るなら 4-A（名前付き＋ドメイン）へ移行**すること。

## 4-C. 外部からの疎通確認
自分のPCから（`<URL>` は 4-A なら `https://nomic.example.com`、4-B ならランダムURL）：
```bash
curl -s <URL>/health
```
- 成功の見分け方: Part 2-5 と同じ `{"...","dim":768,"loaded":true}` が返る。
- これで **Hetzner の生IP・ポートを晒さず**、Cloudflare 経由でのみ到達する構成が完成。

---

# Part 5: pox-web(Render) との接続

**Part 4-C で外部から /health が返ることを確認してから**行う（先に切り替えると登録が失敗するため）。

Render → **pox-web** → **Environment** に以下を設定（値も明記）：

| Key | Value | 備考 |
|---|---|---|
| `POX_NOMIC_ENDPOINT` | `<URL>/embed` | 例 `https://nomic.example.com/embed`。**末尾 `/embed` 必須** |
| `POX_NOMIC_API_KEY` | （Hetzner の `NOMIC_SERVER_API_KEY` と**同じ値**） | ⚠️ pox-web 側の変数名は **`POX_NOMIC_API_KEY`**（`NOMIC_SERVER_API_KEY` ではない）。コードはこれを `X-API-Key` で送る |
| `POX_EMBED_BACKEND` | `nomic` | ここで初めて stub から実Nomicへ切替 |
| `POX_EMBED_MODEL_TAG` | `nomic-emb-v2` | プールの model_tag（Hetzner の `NOMIC_MODEL_TAG` と一致必須） |
| `POX_EMBED_FULL_DIM` | `768` | 次元を明示 |
| `POX_ALLOW_VECTOR_TABLE_REBUILD` | `1` | profile_vectors を768で作り直す許可（**一度きり**） |

> ⚠️ **変数名の注意**: サーバー(Hetzner)側は `NOMIC_SERVER_API_KEY`、pox-web(Render)側は
> `POX_NOMIC_API_KEY`。**名前は違うが値は同じ**にする。不一致だと 401 になる。

## 5-1. DB 768 再作成の事前確認（psql）
Render → pox-db → Connect → PSQL：
```sql
SELECT count(*) FROM profile_vectors;
```
- **0** なら次へ。**1以上**なら `TRUNCATE profile_vectors;`（ベクトルは登録で再生成される派生データ）。

## 5-2. 保存 → 再デプロイ → ログ確認
Environment を **Save Changes** → 自動再デプロイ。pox-web → **Logs** で：
- `[schema_v4] 再作成: profile_vectors ... 768 ...`（旧次元だった場合）
- 最後に `[schema_v4] init_v4 完了。` と `次元: FULL=768`

## 5-3. フラグ削除（❗外し忘れ厳禁）
再作成を確認したら、pox-web → Environment で **`POX_ALLOW_VECTOR_TABLE_REBUILD` を削除** → Save。
- 残すと将来のデプロイで意図せず DB を作り直す危険。**必ず削除**。

---

# Part 6: 疎通・受け入れ確認

## 6-1. pox-web → Nomic の最小確認（登録1件）
```bash
# 必要像を同梱した最小登録（各自AI生成ルート）
curl -s -X POST https://<pox-web-url>/v4/seekers \
  -H 'content-type: application/json' \
  -d '{"user_id":"smoke_test","will_text":"疎通テスト","state_have":"確認",
       "necessity_text":"確認相手","gate_s":0.3,"gate_u":0.3,"p_sharpness":0.0,
       "alpha":1.0,"beta":1.0,"evidence_span":"","generator":"manual"}'
# → 202 と generation_status:"preparing"

sleep 5
curl -s https://<pox-web-url>/v4/seekers/smoke_test/status
# → generation_status:"ready" なら pox-web→Cloudflare→Hetzner→Nomic が全部つながっている
```

## 6-2. DB 確認（個人データは出さない・件数のみ）
Render pox-db の psql：
```sql
-- 768次元のベクトルが1件入ったか（本文は取得しない）
SELECT count(*) FROM profile_vectors WHERE model_tag='nomic-emb-v2';
```
- 1以上になっていれば、実Nomicのベクトルが保存されている。
- 確認用の smoke_test は後で消してよい: `DELETE FROM profiles_v4 WHERE id='smoke_test';`（関連行はカスケード）。

## 6-3. 失敗時の一次切り分け

| 症状 | 原因 | 対処 |
|---|---|---|
| status が `error`、`POX_NOMIC_ENDPOINT` 系の文言 / Tunnel応答なし | トンネル未起動・URL誤り | Hetzner で `sudo systemctl status cloudflared`、`curl -s <URL>/health` を再確認。URL末尾 `/embed` か |
| status が `error`、`401`/`unauthorized` | APIキー不一致 | Hetzner `NOMIC_SERVER_API_KEY` と pox-web `POX_NOMIC_API_KEY` が**同一値**か（名前は違う） |
| Hetzner ログに `Out of memory` | メモリ不足 | `NOMIC_TORCH_DTYPE=bfloat16` に変更（`/etc/nomic.env` → `sudo systemctl restart nomic`）。8GBなら通常不要 |
| status が `error`、`次元 … と不一致` | 次元設定ズレ | Hetzner `NOMIC_DIM=768`、pox-web `POX_EMBED_FULL_DIM=768` を確認 |
| `/health` が `loaded:false` のまま | 初回モデルDL中 or 失敗 | `journalctl -u nomic -f` で進捗確認。数分待つ。ディスク/ネット確認 |
| pox-web が 503 `v4 は Postgres が必要` | DB未接続 | pox-web に `DATABASE_URL`（pox-db）がある通常構成なら発生しない |

---

## 運用メモ
- **サーバー更新**: コードを更新したら Hetzner で `cd ~/matching-program && git pull` → `sudo systemctl restart nomic`。
- **APIキー変更**: `/etc/nomic.env` を編集 → `sudo systemctl restart nomic` → pox-web 側 `POX_NOMIC_API_KEY` も同じ値に更新。
- **モデルは永続**: 一度DLすれば `~/hf-cache` に残り、再起動で再DLしない（Render のような別課金ディスクは不要）。
- **コスト**: Hetzner 4vCPU/8GB ≈ €7/月前後（表示額が最終）。Render は pox-web 無料 + pox-db Basic のみ。
