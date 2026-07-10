# Render フル構築 手順書（pox-db 有料化 + Nomic サービス新設）

**対象**: PoX の本番（Render）を、DB の失効回避と実埋め込み（Nomic）稼働まで完成させる。
**あなたが操作するもの**: Render ダッシュボード（ブラウザ）だけ。コードは変更済み（commit `e7fcddd`）。

> この手順は **Blueprint（render.yaml）を使いません**。既存の `pox-web` / `pox-db` に一切触れず、
> `pox-nomic` を手作業で新規追加します（最も安全な方法として選択済み）。`render.yaml` は
> 現状のまま残します。

## 事前に1つだけ決めておくもの

- **`NOMIC_SERVER_API_KEY`**: あなたが決める**長いランダム文字列**（推測されにくいパスワードのようなもの）。
  例: 英数字を32文字以上。手順B と 手順C の両方で**同じ値**を使うので、メモしておく。
  （生成例: 1Password 等のパスワード生成、または適当な長い英数字列。記号は避けると安全）

## 費用の目安（※金額は必ず申込画面の表示額で最終確認）

| 項目 | プラン | 目安月額 |
|---|---|---|
| pox-db 有料化（手順A） | Basic（最小構成） | **$6/月〜** |
| pox-nomic 本体（手順B） | Standard（2GB RAM） | **$25/月** |
| pox-nomic モデル用ディスク（手順B） | 10GB | **約 $2.5/月**（$0.25/GB） |
| 合計（追加分） | | **約 $33〜34/月** |

> ここの数値は目安です。Render の申込画面に出る金額が正です。差があれば画面を優先してください。

---

# 手順A: pox-db の有料化（7/20 削除回避・最優先）

無料 Postgres は失効予定です。**先にこれを済ませてください。**

1. Render ダッシュボード（https://dashboard.render.com）にログイン。
2. 左の一覧、または上部の検索から **`pox-db`** を開く（種類が **PostgreSQL** のもの）。
3. `pox-db` のページ左メニューで **「Settings」** を開く。
4. プラン欄（「Instance Type」や「Plan」と表示）にある **「Upgrade」/「Change Plan」/「Upgrade instance」** ボタンを押す。

   > 🔴 **超重要（前回の混同ポイント）**: ここで押すのは **`pox-db` サービス単体の「Upgrade instance」** です。
   > 画面右上のアカウント名から入る **「Workspace の Update Plan」/「Pro・Scale へのアップグレード」ではありません。**
   > Workspace 全体のプラン（Team/Pro など）を上げても、この DB の失効は止まりません。
   > **必ず `pox-db` を開いた状態の Settings 内**のアップグレードを操作すること。

5. 表示されたプラン一覧から、**一番安い有料プラン（Basic 系。目安 $6/月）** を選ぶ。
   容量は最小で十分（現状のデータ量は小さい）。上位（Pro など）は不要。
6. 金額を確認して **確定（Confirm / Upgrade）**。ここで月額課金が始まります。

### 成功の確認
- `pox-db` のページ上部にあった **「This database will be deleted on … (7/20 等)」の赤い警告が消える**。
- Settings のプランが Basic（選んだ有料プラン）になっている。
- ❗警告が消えない場合: 無料プランのまま確定できていない可能性。手順4〜6をやり直す。

---

# 手順B: Nomic サービス（pox-nomic）を新規作成

`nomic_server/` をコードから読み取って埋め込み推論サービスを立てます。

1. ダッシュボード右上の **「New +」** → **「Web Service」** を選ぶ。
2. リポジトリ選択で **`pox-praxis/matching-program`** を選び **「Connect」**。
3. 設定画面で以下を**この通り**入力（値は全部確定済み）:

| 項目 | 入力する値 |
|---|---|
| **Name** | `pox-nomic` |
| **Region** | **Oregon (US West)** ← `pox-web`/`pox-db` と**必ず同じ**。違うと通信に失敗する |
| **Branch** | `main` |
| **Root Directory** | `nomic_server` |
| **Runtime / Language** | `Python 3` |
| **Build Command** | `pip install -r requirements.txt` |
| **Start Command** | `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app` |
| **Instance Type** | **Standard（2 GB RAM / $25/月）** ← 一覧から Standard を選ぶ |

4. **Health Check Path**（「Advanced」を開くと出る）に **`/health`** を入力。

5. **環境変数（Environment Variables）** を「Add Environment Variable」で以下すべて追加:

| Key | Value |
|---|---|
| `PYTHON_VERSION` | `3.11.9` |
| `NOMIC_MODEL_TAG` | `nomic-emb-v2` |
| `NOMIC_WARMUP` | `1` |
| `NOMIC_DIM` | `768` |
| `HF_HOME` | `/var/model-cache/huggingface` |
| `NOMIC_SERVER_API_KEY` | **（冒頭で決めた長い文字列）** |

6. **永続ディスク（Disk）** を追加（「Advanced」→「Add Disk」）:

| 項目 | 値 |
|---|---|
| **Name** | `nomic-model-cache` |
| **Mount Path** | `/var/model-cache` |
| **Size (GB)** | `10` |

   > モデル（約1〜2GB）をここに保存し、再起動のたびに再ダウンロードしないためのものです。

7. 画面下の **「Create Web Service」** を押す。

   > 💳 **この時点で `pox-nomic` の月額課金（Standard $25 + ディスク約$2.5）が始まります。**
   > 「Create」を押す前に、画面に出る月額の見積りを確認してください。

### 初回デプロイについて
- 初回は **HuggingFace からモデルをダウンロード**するため **5〜15分程度**かかります（`NOMIC_WARMUP=1` で起動時にロード）。
- Logs（ページ左「Logs」）に `[NomicEmbedder] 実次元検出: 768 次元` のような行が出れば成功に近い。
- **完了の見分け方**: 次の手順Dで `/health` が応答すればデプロイ成功。

### 失敗の見分け方（手順B）
- Logs に **`Out of memory` / `Worker … was killed`** → 2GB では足りない可能性（下の「メモリ不足」参照）。
- Logs に `No space left on device` → ディスクが小さい。Settings→Disks でサイズを増やす。

---

# 手順C: pox-web を Nomic に繋ぐ + DB を 768 で作り直す

**二段構え**で進めます（安全のため）。
- **段階1**: バックエンドは `stub` のまま、DB を 768 次元へ作り直して確認。
- **段階2**: `pox-nomic` の疎通が取れてから、実埋め込み（`nomic`）へ切替。

> ⚠️ 段階1〜2 が終わるまで、**本番で新規プロフィール登録（/v4/seekers）をしないでください**。
> 段階1 の stub で作ったベクトルは意味を持たないため、実運用データが混ざると照合が濁ります。

## 段階1: DB を 768 次元へ作り直す（バックエンドは stub のまま）

### C-1. 事前チェック（DB が空か確認）
`pox-db` のページ → **「Connect」** → **「PSQL Command」**（または任意の psql クライアント）で:

```sql
SELECT count(*) FROM profile_vectors;
```
- **0 が返る** → そのまま進めてよい。
- **1 以上** → 作り直せない（データ保全のため自動で止まる）。次を実行して空にしてから進む
  （`profile_vectors` はベクトルの派生表で、登録し直せば再生成される）:
  ```sql
  TRUNCATE profile_vectors;
  ```

### C-2. pox-web に環境変数を追加
`pox-web` を開く → 左メニュー **「Environment」** → 「Add Environment Variable」で以下を追加:

| Key | Value | 意味 |
|---|---|---|
| `POX_EMBED_MODEL_TAG` | `nomic-emb-v2` | プールの model_tag。これで次元が 768 に決まる |
| `POX_EMBED_FULL_DIM` | `768` | 念のため次元を明示（768 で固定） |
| `POX_ALLOW_VECTOR_TABLE_REBUILD` | `1` | **DB 再作成の許可フラグ（今回だけ）** |

`POX_EMBED_BACKEND` は**まだ追加しない**（＝既定の `stub` のまま）。

**「Save Changes」** を押すと自動で再デプロイされます。

### C-3. ログで再作成を確認
`pox-web` → **「Logs」** を開き、以下のいずれかが出ることを確認:

- `[schema_v4] 再作成: profile_vectors を drop→再作成します。理由: 次元変更 …→768 …`
- `[schema_v4] 再作成: derived_necessity を drop→再作成します。理由: キー構造変更 …`（旧構造だった場合）
- 最後に必ず: `[schema_v4] init_v4 完了。` と `次元: FULL=768（256系は廃止…）`

> もし `[schema_v4] 停止: … N 件のデータがあり再作成できません` が出たら、C-1 に戻って
> `profile_vectors` を空にしてから再デプロイ（Manual Deploy → Deploy latest commit）。

### C-4. フラグを外す（❗外し忘れ厳禁）
再作成が確認できたら、`pox-web` → Environment で
**`POX_ALLOW_VECTOR_TABLE_REBUILD` を削除**（ゴミ箱アイコン）→ Save。

> 🔴 このフラグを残すと、将来スキーマ変更時に**本番デプロイで意図せず DB が作り直される**危険があります。
> **必ず削除**してください。削除後にもう一度再デプロイされ、ログに `init_v4 完了。` が出れば OK。

### C-5. スキーマ確認（任意・psql）
```sql
-- _256 列が消えている（0 行が正しい）
SELECT column_name FROM information_schema.columns
 WHERE table_name='profile_vectors' AND column_name LIKE '%\_256' ESCAPE '\';
-- 非同期生成用の列がある（2 行）
SELECT column_name FROM information_schema.columns
 WHERE table_name='profiles_v4' AND column_name IN ('generation_status','generation_error');
-- 必要像の履歴索引がある（1 行）
SELECT indexname FROM pg_indexes WHERE tablename='derived_necessity' AND indexname='uq_dn_active';
```

## 段階2: 実埋め込み（nomic）へ切替

**手順D で `pox-nomic` の `/health` が OK になってから**行ってください。

### C-6. pox-web に接続情報を追加
`pox-web` → Environment に追加:

| Key | Value |
|---|---|
| `POX_EMBED_BACKEND` | `nomic` |
| `POX_NOMIC_ENDPOINT` | `https://pox-nomic.onrender.com/embed` ← `pox-nomic` ページ**最上部に表示される URL** の末尾に `/embed` を付ける |
| `POX_NOMIC_API_KEY` | **手順B で決めた `NOMIC_SERVER_API_KEY` と同じ値** |

> `POX_NOMIC_ENDPOINT` の URL は、`pox-nomic` サービスのページ上部（サービス名の下）に出る
> `https://pox-nomic-xxxx.onrender.com` 形式のアドレスをコピーし、末尾に `/embed` を足したものです。

**「Save Changes」** → 再デプロイ。これで登録・照合が**実際の Nomic ベクトル**を使い始めます。

### C-7. ANTHROPIC_API_KEY の有無を確認（必要像フォールバック用）
`pox-web` → Environment に **`ANTHROPIC_API_KEY`** があるか確認:
- **ある** → 必要像フィールドを同梱しない登録でも、サーバー側で必要像を自動生成できる。
- **ない** → 必要像フィールドを**同梱した登録（各自AI生成ルート）**のみ動作。同梱なしの登録は
  ステータスが `error`（キー未設定の案内）になる。フォールバックも使うなら、ここでキーを追加。

---

# 手順D: 疎通確認

## D-1. pox-nomic 単体の確認
ブラウザで `pox-nomic` の URL + `/health` を開く（例: `https://pox-nomic-xxxx.onrender.com/health`）。

- **成功**: `{"status":"ok","model":"nomic-ai/nomic-embed-text-v2-moe","model_tag":"nomic-emb-v2","dim":768,"loaded":true}` のような JSON。
  - `dim` が **768**、`loaded` が **true** であること。
- **失敗（応答なし/エラー画面）**: まだデプロイ中か、起動失敗。Logs を確認（下の対処表）。

（任意・できる人向け）認証テスト:
```bash
# キー無し → 401 が正しい
curl -s -X POST https://pox-nomic-xxxx.onrender.com/embed \
  -H 'content-type: application/json' -d '{"text":"テスト"}'
# キー有り → dim:768 が返る
curl -s -X POST https://pox-nomic-xxxx.onrender.com/embed \
  -H 'content-type: application/json' -H "X-API-Key: <手順Bで決めた値>" \
  -d '{"text":"テスト"}'
```

## D-2. pox-web ↔ Nomic の確認（段階2 完了後）
最小確認として、v4 登録を1件投げてステータスが `ready` になるか見ます（できる人向け・curl）:

```bash
# 1) 必要像を同梱した最小登録（各自AI生成ルート）
curl -s -X POST https://pox-web.onrender.com/v4/seekers \
  -H 'content-type: application/json' \
  -d '{"user_id":"smoke_test","will_text":"疎通テスト","state_have":"確認",
       "necessity_text":"確認相手","gate_s":0.3,"gate_u":0.3,"p_sharpness":0.0,
       "alpha":1.0,"beta":1.0,"evidence_span":"","generator":"manual"}'
# → 202 と {"id":"smoke_test","generation_status":"preparing", ...}

# 2) 少し待ってからステータス確認
curl -s https://pox-web.onrender.com/v4/seekers/smoke_test/status
# → {"id":"smoke_test","generation_status":"ready", ...} なら pox-web→Nomic 疎通 OK
```
- `ready` になれば、pox-web が Nomic を呼んでベクトル生成できています。
- `error` の場合は `generation_error` の文言を確認（多くは下表のいずれか）。

curl を使わない場合は、`pox-web` の Logs に埋め込み呼び出しのエラーが出ていないかで代替確認します。

## D-3. 失敗時の一次対処表

| 症状 | 原因 | 対処 |
|---|---|---|
| `/health` が応答しない・起動失敗 | 初回モデルDL中 | 5〜15分待って再確認。Logs に `実次元検出: 768` が出るまで待つ |
| Logs に `Out of memory` / `Worker … killed` | 2GB では足りない | `pox-nomic` → Settings → Instance Type を **Pro（4GB, 目安 $85/月）** に上げる |
| pox-web の status が `error`、`unauthorized`/`401` | API キー不一致 | `NOMIC_SERVER_API_KEY`（pox-nomic）と `POX_NOMIC_API_KEY`（pox-web）が**同じ値**か確認 |
| status が `error`、`POX_NOMIC_ENDPOINT 未設定` 等 | エンドポイント未設定/誤り | C-6 の `POX_NOMIC_ENDPOINT` を再確認（末尾 `/embed`、URL は pox-nomic の実アドレス） |
| 照合や登録が異常に遅い／繋がらない | **region 不一致** | `pox-nomic` が **Oregon** か確認。違えば作り直し（region は後から変更不可） |
| Logs に `次元 … が FULL_DIM=768 と不一致` | 次元設定ズレ | pox-web の `POX_EMBED_FULL_DIM=768`、pox-nomic の `NOMIC_DIM=768` を確認 |
| status が `error`、必要像キー未設定案内 | フォールバックに `ANTHROPIC_API_KEY` 無し | C-7 でキーを追加するか、必要像を同梱して登録する |
| Logs に `v4 は Postgres … が必要` (503) | DB 接続が Postgres でない | `pox-web` に `DATABASE_URL`（pox-db 接続）がある通常構成なら発生しない。Environment を確認 |

---

## 参考: pox-nomic の設定値まとめ（この1枚で再現可能）

| 区分 | 項目 | 値 |
|---|---|---|
| 基本 | Region | Oregon（pox-web/pox-db と同一） |
| 基本 | Root Directory | `nomic_server` |
| 基本 | Instance Type | Standard（2GB, $25/月） |
| 起動 | Build | `pip install -r requirements.txt` |
| 起動 | Start | `gunicorn --workers 1 --threads 4 --timeout 120 --bind 0.0.0.0:$PORT wsgi:app` |
| 起動 | Health Check | `/health` |
| ディスク | Mount / Size | `/var/model-cache` / 10GB |
| 環境変数 | `PYTHON_VERSION` | `3.11.9` |
| 環境変数 | `NOMIC_MODEL_TAG` | `nomic-emb-v2` |
| 環境変数 | `NOMIC_WARMUP` | `1` |
| 環境変数 | `NOMIC_DIM` | `768` |
| 環境変数 | `HF_HOME` | `/var/model-cache/huggingface` |
| 環境変数 | `NOMIC_SERVER_API_KEY` | （ユーザーが決める長い文字列） |

関連手順書: `docs/deploy_2a_db768_rebuild.md`（DB 再作成の詳細）、`docs/deploy_2a_nomic_service.md`（Nomic サービスとモデル取得方式の技術背景）。
