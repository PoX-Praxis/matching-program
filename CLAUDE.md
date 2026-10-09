# PoX マッチングプラットフォーム — 開発引き継ぎ

## リポジトリ
- GitHub: `pox-praxis/matching-program` / ブランチ: `main`
- デプロイ先: Render（`pox-web.onrender.com`。render.yaml のサービス名 `pox-web` に対応）

## セキュリティ制約（絶対厳守）
- `ANTHROPIC_API_KEY` / `GITHUB_TOKEN` / `DATABASE_URL` はコードにもリポジトリにも書かない
- `.env`, `*.key`, `secrets.json` → コミット禁止
- `pox.db`, `*.db` → `.gitignore` 済み
- `seeker` カラムは viewer/public API レスポンスに絶対出さない
- `GET /profile/<id>` は必ず `get_profile_view()` のみ呼ぶ（`get_seeker()` は呼ばない）

## スキーマ: v4 二軸（意志/現状）
v3.1（意志/求めている/能力/フェーズ）から v4（意志 + 現状4スロット）へ移行済み。

### v4 profile_view フィールド
`schema_version`, `headline`, `pursuing`, `state_have`, `state_can_type`, `state_bound`, `state_unsorted`, `seeking`, `keys`, `trajectory`

### v3 後方互換フィールド
`schema_version`, `headline`, `pursuing`, `needs`, `offering`, `phase_badge`, `keys`, `trajectory`

## 完了済みタスク
- v4 スキーマ全面移行（`src/profile_view.py`, `src/db.py`, `app.py`, 全テンプレート）
- 構造化プロンプト v4「判断しない採集者」を `templates/register.html` に反映
- 構造化プロンプト v5（2026-10-06 の文面）に差し替え（v4 の JSON も受け付ける）
- 構造化プロンプト v5 改訂2（A 対話・B 自分で書く）に差し替え（指示書60）。本文は `docs/prompts/` が唯一の正で、画面はそこから読み込む
- 構造化プロンプト v5 改訂3 に差し替え・共鳴の門・根拠の表示・生成元の記録・正準化 p3/c4（指示書61）
- iOS スマートクォート（`“` `”`）によるJSON登録エラー修正
  - `src/profile_view.py`: `strip_code_fence()` でUnicodeエスケープ正規化
  - `templates/register.html`: 送信前にJS側でも正規化

## 継続中タスク
- [ ] Render デプロイ後の動作確認（登録 → マイページ v4 表示）
- [ ] Windows PC で Qwen3-Embedding ローカルサーバー起動
  - `py -m pip install -r requirements.txt`（`pip` でなく `py -m pip`）
  - `py qwen3_server\run_qwen3.bat`
- [ ] Cloudflare トンネル: `cloudflared tunnel --url http://localhost:8000`
- [ ] Render 環境変数を更新:
  - `POX_EMBED_BACKEND=qwen3`
  - `POX_QWEN3_ENDPOINT=https://<tunnel-url>/embed`
  - `POX_EMBED_MODEL_TAG=qwen3-embedding-0.6b-d1024`

## 主要ファイル構成
```
app.py                        # Flask ルート
src/
  db.py                       # DB層（profiles/seekers 二層保存）
  profile_view.py             # seeker → profile_view 変換（v3/v4 両対応）
  db_connect.py               # SQLite/Postgres 接続切替
templates/
  register.html               # 登録フォーム（プロンプトは docs/prompts/ から読み込む・v4 JSON も受付）
  mypage.html                 # マイページ（v4/v3 分岐表示）
  profile.html                # 公開プロフィール
  edit.html                   # 編集フォーム（意志 + 現状4スロット）
qwen3_server/                 # Qwen3-Embedding ローカル推論サーバー
docs/prompts/
  v5_A_dialogue.txt                   # ① 構造化プロンプト v5 改訂3 A（対話して作る・推奨）＝唯一の正
  v5_B_selfwrite.txt                  # ① 同 B（自分で書いて作る・簡易）
spec/
  structuring_prompt_v5.md            # ① v5 の旧文面（2026-10-06・一発変換。改訂2 で置き換え）
  structuring_prompt_v4_interview.md  # ① 構造化プロンプト v4 仕様書（旧）
```
