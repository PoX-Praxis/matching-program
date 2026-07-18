# Nomic 推論サーバー構築手順（自宅Windows PC + Cloudflare Tunnel）

**目的**: 採用モデル `nomic-emb-v2` の推論サーバーを常時稼働の自宅 Windows PC 上に構築し、
Cloudflare Tunnel 経由で pox-web(Render) から安全に呼ぶ。

**構成**:
```
[利用者] → pox-web (Render, 無料) ──HTTPS(APIキー)──▶ Cloudflare Tunnel ──▶ 自宅PC
                                                                        localhost:8002
                                                                        nomic_server (waitress)
        pox-db (Render, Basic) にデータ保管
```

## 安全性の基本方針（重要）
- **家庭のルーターのポート開放は一切しません**。Cloudflare Tunnel は PC から**外向きに**接続を張る
  方式なので、家のネットワークに**外部からの入口を作らずに**公開できます。ポート開放は禁止。
- Nomic は **`127.0.0.1`（localhost）だけ**にバインド。同じLANの他PCからも外部からも直接は見えない。
  到達は **Cloudflare Tunnel 経由のみ**。
- 認証は二重: ①Tunnel で経路暗号化・自宅IP秘匿 ②既存 `NOMIC_SERVER_API_KEY`（`X-API-Key` 検証）。
- 扱うデータは現状 **redacted（伏字済み）**。将来 raw を扱うなら、分離強化やデータセンター移行を
  別途検討（末尾メモ）。

## あなたが用意する秘密の値（1つ）
- `NOMIC_SERVER_API_KEY` = 長いランダム文字列（32文字以上推奨）。**自宅PC側**と **pox-web(Render)側**で
  同じ値を使う。メモしておく。

> ⚠️ **Windows では gunicorn は動きません**（Unix専用）。本手順は Windows対応の **waitress** で起動します。
> メモリに余裕があるので `NOMIC_TORCH_DTYPE=float32`（fp32）で読み込みます。

以下、コマンドは **PowerShell**（スタート → "PowerShell" → 管理者は必要な箇所で明記）で実行します。
パスは例として `C:\pox\...` を使います（好きな場所でOK。以降読み替え）。

---

# Part 1: Nomic サーバーをローカルで動かす

## 1-1. 前提ソフトの導入
1. **Python 3.11（64-bit）**: https://www.python.org/downloads/ から 3.11.x を入れる。
   インストーラ最初の画面で **「Add python.exe to PATH」にチェック**。
2. **Git**: https://git-scm.com/download/win を既定設定で導入。
3. 確認（新しい PowerShell を開いて）：
   ```powershell
   python --version
   git --version
   ```
   - 成功の見分け方: `Python 3.11.x` と `git version ...` が表示。

## 1-2. コード取得（git clone）
リポジトリは**非公開**なので GitHub の **Personal Access Token（PAT・読み取り専用）** が必要。
1. GitHub → Settings → Developer settings → **Fine-grained tokens** → Generate。
   Repository access = `pox-praxis/matching-program` のみ、Permissions: Contents = Read-only。
   生成された `github_pat_xxx` をコピー（一度しか表示されない）。
2. クローン（`<PAT>` を置換）：
   ```powershell
   mkdir C:\pox
   cd C:\pox
   git clone https://<PAT>@github.com/pox-praxis/matching-program.git
   ```
   - 成功の見分け方: `C:\pox\matching-program\nomic_server` に `model.py server.py wsgi.py requirements.txt` がある。
   - ⚠️ PAT はこの1回だけ使用。保存しない。

## 1-3. Python 環境と依存インストール
```powershell
cd C:\pox\matching-program\nomic_server
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```
- 何をするか: 隔離した Python 環境を作り有効化。プロンプト先頭に `(.venv)` が付く。
- もし `Activate.ps1 ... 実行できない` と出たら、一度だけ実行ポリシーを緩める：
  ```powershell
  Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
  ```
  その後もう一度 `.\.venv\Scripts\Activate.ps1`。

依存の導入（Windows の torch は既定で CPU 版なので特別な指定は不要）：
```powershell
pip install -r requirements.txt
pip install waitress
```
- 何をするか: flask / sentence-transformers / torch(CPU) / einops / **waitress**（Windows用の常駐サーバー）を導入。
- 時間がかかる（数分〜十数分。torch が大きい）。
- 失敗時: ディスク空き（`Get-PSDrive C`）を確認。ネットワーク不調なら再実行（途中まではキャッシュ済み）。

## 1-4. モデルのキャッシュ場所
```powershell
mkdir C:\pox\hf-cache
```
- 何をするか: HuggingFace から落とすモデルの保存先。一度落とせば残り、再DLしない。

## 1-5. 環境変数を設定（このPowerShellセッション用）
`<KEY>` を自分の長い文字列に置換：
```powershell
$env:NOMIC_SERVER_API_KEY="<KEY>"
$env:NOMIC_MODEL_TAG="nomic-emb-v2"
$env:NOMIC_DIM="768"
$env:NOMIC_TORCH_DTYPE="float32"
$env:NOMIC_WARMUP="1"
$env:HF_HOME="C:\pox\hf-cache"
```
- 何をするか: サーバーの動作設定。`float32`＝メモリに余裕がある前提の高精度読み込み。
- ※これは**この画面だけ**の一時設定。常駐化（Part 2）では NSSM 側に設定するので別途入れ直す。

## 1-6. 手動起動で動作確認（初回はモデルDLで数分）
```powershell
.\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8002 wsgi:app
```
- 何をするか: Nomic を localhost:8002 で起動。`NOMIC_WARMUP=1` で起動時にモデル読み込み。
- 初回は HuggingFace から**モデルDL（1〜2GB・数分）**。
  `[NomicEmbedder] 実次元検出: 768 次元` が出れば読み込み成功。
- Windows Defender が「python がネットワークに接続」を聞いてきたら → **「キャンセル」/許可しないでOK**
  （外部からの着信は使わない。外向き接続は許可不要で通る）。

**別の PowerShell ウィンドウ**を開いて疎通確認：
```powershell
curl.exe -s http://localhost:8002/health
```
- 成功の見分け方: `{"status":"ok","model":"nomic-ai/nomic-embed-text-v2-moe","model_tag":"nomic-emb-v2","dim":768,"loaded":true}`。
- 確認できたら、起動中のウィンドウで **Ctrl+C** で停止（次は常駐化する）。

---

# Part 2: 常時稼働化（Windowsサービス化・自動再起動）

**NSSM**（Non-Sucking Service Manager）で waitress をサービス化する。落ちても自動再起動、PC起動時に自動開始。

## 2-1. NSSM 入手
1. https://nssm.cc/download → 最新（`nssm 2.24`）の zip をダウンロード。
2. 展開し、`win64\nssm.exe` を `C:\pox\nssm.exe` にコピー。
3. 確認：
   ```powershell
   C:\pox\nssm.exe version
   ```

## 2-2. サービス登録（管理者 PowerShell）
スタート → PowerShell を右クリック → **管理者として実行**。`<KEY>` を置換：
```powershell
C:\pox\nssm.exe install PoXNomic "C:\pox\matching-program\nomic_server\.venv\Scripts\waitress-serve.exe" "--host=127.0.0.1 --port=8002 wsgi:app"
C:\pox\nssm.exe set PoXNomic AppDirectory "C:\pox\matching-program\nomic_server"
C:\pox\nssm.exe set PoXNomic AppEnvironmentExtra NOMIC_SERVER_API_KEY=<KEY> NOMIC_MODEL_TAG=nomic-emb-v2 NOMIC_DIM=768 NOMIC_TORCH_DTYPE=float32 NOMIC_WARMUP=1 HF_HOME=C:\pox\hf-cache
C:\pox\nssm.exe set PoXNomic Start SERVICE_AUTO_START
C:\pox\nssm.exe set PoXNomic AppStdout C:\pox\nomic-log.txt
C:\pox\nssm.exe set PoXNomic AppStderr C:\pox\nomic-log.txt
```
- 何をするか: waitress をサービス `PoXNomic` として登録。環境変数・作業フォルダ・自動起動・ログ出力先を設定。
- NSSM は既定で**プロセスが落ちたら自動再起動**する。

開始：
```powershell
C:\pox\nssm.exe start PoXNomic
```
- 確認：
  ```powershell
  Get-Service PoXNomic
  curl.exe -s http://localhost:8002/health
  ```
  - 成功の見分け方: `Status: Running`、`/health` が `dim:768,loaded:true`。
  - モデルロード中は数分 `loaded:false` のことがある。`C:\pox\nomic-log.txt` に `実次元検出: 768` が出れば完了。

> 代替（NSSMを使わない場合）: **タスクスケジューラ**で「PC起動時」に
> `C:\pox\matching-program\nomic_server\.venv\Scripts\waitress-serve.exe --host=127.0.0.1 --port=8002 wsgi:app` を
> 実行するタスクを作る（環境変数は `setx` で永続設定）。ただし自動再起動やログ管理は NSSM の方が確実。

## 2-3. スリープ/休止を無効化（稼働継続の必須設定）
管理者 PowerShell：
```powershell
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /hibernate off
```
- 何をするか: 電源接続時に**スリープ・休止しない**設定（画面だけ消えるのはOK）。スリープすると Nomic も止まる。
- 加えて Windows の 設定 → システム → 電源 で「スリープ = なし（電源接続時）」を確認。

## 2-4. Windows Update の再起動対策
- 設定 → Windows Update → **アクティブ時間の変更** で、使う時間帯を登録（その間は自動再起動しない）。
- 再起動が起きても、`PoXNomic` は**自動起動**に設定済みなので、起動後に自動で復帰する（Part 2-2）。

## 2-5. 再起動テスト
```powershell
Restart-Computer
```
- PC が再起動したら（数分後）、再度 PowerShell で：
  ```powershell
  Get-Service PoXNomic
  curl.exe -s http://localhost:8002/health
  ```
- 成功の見分け方: サービスが `Running`、`/health` が応答。**手動操作なしで復帰**していればOK。

---

# Part 3: Cloudflare Tunnel の設定（Windows）

Nomic の 8002 は localhost のみ。Cloudflare Tunnel が **localhost:8002 → 公開HTTPS URL** を橋渡しする。
**推奨は「A. ダッシュボード管理トンネル（安定URL）」**。ドメインが無ければ「B. クイックトンネル」。

## 3-A. ダッシュボード管理トンネル（安定・推奨／独自ドメインが必要）
> Cloudflare の無料プランに**独自ドメイン**（安価な .xyz 等でも可）を追加してあること。
> URL 固定（例 `https://nomic.example.com`）＝本番向き。GUI 中心で Windows でも楽。

1. Cloudflare にログイン → 対象ドメインを **Websites** に追加済みにする（ネームサーバー切替が必要）。
2. **Zero Trust** ダッシュボード → **Networks → Tunnels → Create a tunnel** → **Cloudflared** を選択。
3. トンネル名 `pox-nomic` → Save。
4. **Windows 用のインストールコマンド**（`cloudflared.exe service install <長いトークン>`）が表示される。
   これを**自宅PCの管理者 PowerShell** で実行。
   - 先に cloudflared を入れる: https://github.com/cloudflare/cloudflared/releases/latest から
     `cloudflared-windows-amd64.exe` を DL → `C:\pox\cloudflared.exe` にリネーム配置。
   - 実行例（トークンは画面の値）:
     ```powershell
     C:\pox\cloudflared.exe service install <画面に出たトークン>
     ```
   - これで cloudflared が **Windowsサービスとして常駐**（PC起動時開始・落ちても復帰）。
5. ダッシュボードに戻り、トンネルの **Public Hostname** を追加:
   - Subdomain: `nomic` / Domain: `example.com`（自分のドメイン）
   - Service: **Type = HTTP**, **URL = `localhost:8002`**
   - Save。
6. **あなたの公開URL** = `https://nomic.example.com`（pox-web では末尾に `/embed`）。

## 3-B. クイックトンネル（ドメイン不要・お試し／URLは毎回変わる）
> ドメインが無い場合の暫定。`https://<ランダム>.trycloudflare.com` が得られるが、
> 実行を止める/PC再起動で **URLが変わる**（pox-web も都度更新が必要）。まず動作確認用。

cloudflared 配置後（3-A の DL と同じ）、通常 PowerShell で：
```powershell
C:\pox\cloudflared.exe tunnel --url http://localhost:8002
```
- ログに `https://<ランダム>.trycloudflare.com` が出る。この窓を閉じるとURLは消える。
- 安定運用に入るなら 3-A（ドメイン＋サービス化）へ移行。

## 3-C. 外部からの疎通確認
スマホの回線など**自宅LAN外**から（または PC で）、`<URL>` に対して：
```powershell
curl.exe -s <URL>/health
```
- 成功の見分け方: Part 1-6 と同じ `{"...","dim":768,"loaded":true}` が返る。
- これで**自宅の生IP・ポートを晒さず**、Cloudflare 経由でのみ到達する構成が完成。

---

# Part 4: 家庭LANとの分離（安全性）

- **外部からの着信は一切不要**（Tunnel は PC からの外向き接続のため）。したがって:
  - **ルーターのポート開放をしない**（この構成では不要かつ危険）。
  - Windows Defender ファイアウォールは **オンのまま**。8002 への**インバウンド許可ルールを作らない**。
    Part 1-6 で「python のネットワーク許可」を聞かれても**許可しない**でよい（外向きは許可不要で動く）。
- localhost バインド（`--host=127.0.0.1`）により、**同じ家のLAN内の他機器からも直接は見えない**。
- 可能なら、この Nomic 用PCは**専用機**として、家族の共有ファイル・他用途と混ぜない。
  （共有フォルダ公開やゲスト機能はこのPCでは有効化しない。）
- **データ範囲の注記（将来の判断ポイント）**: 現状このPCが受け取るのは **redacted（伏字済み）データ**。
  もし将来 raw（伏字前）を扱う方針になるなら、家庭環境ではなく**分離強化 or データセンター/クラウド移行**を
  検討すること（家庭PCで生の個人データを常時保持するのはリスクが高い）。

---

# Part 5: pox-web(Render) との接続

**Part 3-C で自宅LAN外から /health が返ることを確認してから**行う（先に切り替えると登録が失敗する）。

Render → **pox-web** → **Environment** に以下を設定：

| Key | Value | 備考 |
|---|---|---|
| `POX_NOMIC_ENDPOINT` | `<URL>/embed` | 例 `https://nomic.example.com/embed`。**末尾 `/embed` 必須** |
| `POX_NOMIC_API_KEY` | （自宅PCの `NOMIC_SERVER_API_KEY` と**同じ値**） | ⚠️ pox-web 側の変数名は **`POX_NOMIC_API_KEY`**。コードはこれを `X-API-Key` で送る |
| `POX_EMBED_BACKEND` | `nomic` | stub から実Nomicへ切替 |
| `POX_EMBED_MODEL_TAG` | `nomic-emb-v2` | 自宅PCの `NOMIC_MODEL_TAG` と一致必須 |
| `POX_EMBED_FULL_DIM` | `768` | 次元を明示 |
| `POX_ALLOW_VECTOR_TABLE_REBUILD` | `1` | profile_vectors を768で作り直す許可（**一度きり**） |

> ⚠️ **名前の落とし穴**: 自宅PC側は `NOMIC_SERVER_API_KEY`、pox-web側は `POX_NOMIC_API_KEY`。
> **名前は違うが値は同じ**。不一致だと 401 になる。

## 5-1. DB 768 再作成の事前確認（psql）
Render → pox-db → Connect → PSQL：
```sql
SELECT count(*) FROM profile_vectors;
```
- **0** なら次へ。**1以上**なら `TRUNCATE profile_vectors;`（ベクトルは登録で再生成される派生データ）。

## 5-2. 保存 → 再デプロイ → ログ確認
Environment を **Save Changes** → 自動再デプロイ。pox-web → **Logs**：
- `[schema_v4] 再作成: profile_vectors ... 768 ...`（旧次元だった場合）
- 最後に `[schema_v4] init_v4 完了。` と `次元: FULL=768`

## 5-3. フラグ削除（❗外し忘れ厳禁）
再作成を確認したら、pox-web → Environment で **`POX_ALLOW_VECTOR_TABLE_REBUILD` を削除** → Save。
- 残すと将来のデプロイで意図せず DB を作り直す危険。**必ず削除**。

---

# Part 6: 疎通・受け入れ確認

## 6-1. pox-web → Nomic の最小確認（登録1件）
```powershell
curl.exe -s -X POST https://<pox-web-url>/v4/seekers -H "content-type: application/json" -d "{\"user_id\":\"smoke_test\",\"will_text\":\"疎通テスト\",\"state_have\":\"確認\",\"necessity_text\":\"確認相手\",\"gate_s\":0.3,\"gate_u\":0.3,\"p_sharpness\":0.0,\"alpha\":1.0,\"beta\":1.0,\"evidence_span\":\"\",\"generator\":\"manual\"}"
# → 202 と generation_status:"preparing"

# 5秒ほど待ってから
curl.exe -s https://<pox-web-url>/v4/seekers/smoke_test/status
# → generation_status:"ready" なら pox-web→Cloudflare→自宅PC→Nomic が全部つながっている
```

## 6-2. DB 確認（件数のみ・個人データは出さない）
Render pox-db の psql：
```sql
SELECT count(*) FROM profile_vectors WHERE model_tag='nomic-emb-v2';
-- 1以上なら実Nomicのベクトルが保存されている
DELETE FROM profiles_v4 WHERE id='smoke_test';   -- 確認用データの後始末（関連行はカスケード）
```

## 6-3. 耐久確認（放置・再起動しても生きているか）
- **数時間放置**後に `curl.exe -s <URL>/health` が返るか（スリープ無効化が効いているか）。
- **PC再起動**後に、手動操作なしで `Get-Service PoXNomic` が `Running`・`<URL>/health` が応答するか。
- どちらも OK なら「常時稼働」の要件を満たす。

## 6-4. 失敗時の一次切り分け

| 症状 | 原因 | 対処 |
|---|---|---|
| status が `error`、Tunnel応答なし | トンネル/サービス停止・URL誤り | `Get-Service cloudflared` / `curl.exe -s <URL>/health`。URL末尾 `/embed` か |
| status が `error`、`401`/`unauthorized` | APIキー不一致 | 自宅PC `NOMIC_SERVER_API_KEY` と pox-web `POX_NOMIC_API_KEY` が**同一値**か（名前は違う） |
| `/health` が `loaded:false` のまま | 初回モデルDL中/失敗 | `C:\pox\nomic-log.txt` を確認。数分待つ。ディスク/ネット確認 |
| status が `error`、`次元 … 不一致` | 次元設定ズレ | 自宅PC `NOMIC_DIM=768`、pox-web `POX_EMBED_FULL_DIM=768` を確認 |
| 一定時間後に落ちる | スリープ/休止 | Part 2-3 の電源設定を再確認（standby/hibernate=0） |
| PC再起動後に上がらない | 自動起動未設定 | `nssm set PoXNomic Start SERVICE_AUTO_START` を再実行 |

---

## 運用メモ
- **コード更新**: `cd C:\pox\matching-program` → `git pull` → `C:\pox\nssm.exe restart PoXNomic`。
- **APIキー変更**: `nssm set PoXNomic AppEnvironmentExtra ...`（新キー含め全部）→ `nssm restart PoXNomic` → pox-web の `POX_NOMIC_API_KEY` も更新。
- **モデルは永続**: 一度DLすれば `C:\pox\hf-cache` に残り、再起動で再DLしない。
- **コスト**: 自宅PC の電気代のみ（Cloudflare Tunnel 無料）。Render は pox-web 無料 + pox-db Basic のみ。
- **注意**: 自宅PCが停止/ネット切断中は Nomic が使えず、pox-web の登録・照合が `error` になる。安定稼働なら
  常時起動・有線LAN推奨。24/365 の可用性が要るなら将来 VPS(Hetzner 等)へ移行。
