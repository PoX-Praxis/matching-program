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
- 構造化プロンプト v5 rev4（```json のコードブロックで出す・根拠は「／」）に差し替え・登録の形式補正の告知（指示書62）
- 画面の整理（マイページの切り替えと説明の同意の欄を外す・同意をポリシーの版 2026-11 に一本化・つながる・登録者一覧）（指示書70 §4）
- 構造化プロンプト v5 rev5 A（本人が選ぶ: 必須・目的の種類・「縛られているもの」の説明への使用）に差し替え。B は rev4 のまま。正準形 c5（＋種類）・p4（＋説明への使用）。説明の生成は構造化の値を使う（指示書70 順2・§5）
- iOS スマートクォート（`“` `”`）によるJSON登録エラー修正
  - `src/profile_view.py`: `strip_code_fence()` でUnicodeエスケープ正規化
  - `templates/register.html`: 送信前にJS側でも正規化

## 継続中タスク
- [x] 指示書77a 追補2（照合の表 v0.3.1-dev を LLM なしで測る）。表 v0.3.1-dev は 2026-11-19 に凍結した（事前登録 §1）。凍結の後は、お手本と v2 を見て表を直さない（直すなら v0.3.2）
  - 照合は `src/match_table.py`（`match(..., version="v0.3.1-dev")`。既定は v0.2 のまま。v0.3 も残す）、表は `data/match_tables/<版>/`（文書の写し。Code は中身を変えない・表に無い分岐を足さない）
  - 計測は `scripts/t77a_measure.py --version v0.3.1-dev`（記録 `data/explanation_bench/runs/77a_match_tables_v0.3.1-dev.json`・gates_v1.2）。v0.2・v0.3 の表と記録は残す
  - fugashi＋unidic-lite は `requirements-bench.txt`（CI だけ。本番の requirements.txt には入れない）
- [x] v2 の正解は固定した（2026-11-19・`data/explanation_bench/v2/v2_labels.json`・sha256 2c47105e…・テスト 545）。固定の後は正解を変えない（直すなら新しい版として別のファイル）
  - 報告には v2 の文を書かない（id と数だけ）。v2 の中身を照合の表の作業に使わない。照合も LLM も、指示があるまで動かさない
- [ ] 指示書77b 順1（照合の表 v0.3.1-dev ＋ 機械の［A］で v2 を測る）は報告して止まった（`docs/reports/77b.md`）。**合否は否**（線 1 誤り 0・線 2 1/24・線 4 同じ）。順2（LLM の［A］を参考に測る）は KAORU の「進めて」まで動かさない
  - 機械の［A］は `scripts/machine_a.py`（規則 `data/machine_a/a_rules_v1.json` は指示書 §3 の写し。規則に無い変換を足さない・迷えば型の ①）
  - 計測は `scripts/t77b_measure.py`（記録 `data/explanation_bench/runs/77b_v2_machineA.json`。v2 の文は記録に書かない）。事前登録は `docs/prereg/v2_lines_2026-11-19.md`（封印・sha256 17d60c6c…）
  - v2 の ① の文は KAORU 用の `data/explanation_bench/v2/77b_sentences_for_KAORU.md` にだけある。落ちた後に v2 を開けば「探索」の扱い。直した表は新しい v3 で判定する
  - 追補（探索）：KAORU の指示で v2 を探索として開き、表が落とした 23 組を `data/explanation_bench/v2/77b_drops_explore.md` に書いた（報告 77b の「探索」の節）。表・機械の読み（M1〜M4）をどう直すかは決めていない。直した表は v3 で判定する
- [ ] 指示書78 順1（機械の読み M1〜M4・F1 を直して測る）は報告して止まった（`docs/reports/78.md`。合否は決めない）。表 v0.3.2-dev：60 組・gates は v0.3.1-dev と同じ、v2（探索）は説明 3/24・誤り 0
  - 表は `docs/match_tables_v0.3.2-dev.md`・`data/match_tables/v0.3.2-dev/`（読みは §0''' と `readings.json`。語は足していない）。照合は `match(..., version="v0.3.2-dev")`（読みは `_v032()` のときだけ）
  - 計測は `scripts/t78_measure.py`（記録 `data/explanation_bench/runs/78_match_tables_v0.3.2-dev.json`）。v0.3.1-dev は凍結のまま
  - 前からある読みの穴 N1〜N3（「〜たら」を過去と読む・「〜したいです」が希望の関門に当たらない・「〜んでいる」が継続にならない）は直していない
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
  v5_A_dialogue.txt                   # ① 構造化プロンプト v5 rev5 A（対話して作る・推奨）＝唯一の正
  v5_B_selfwrite.txt                  # ① 同 B（自分で書いて作る・簡易。rev4 のまま。指示書70 の確認 2）
spec/
  structuring_prompt_v5.md            # ① v5 の旧文面（2026-10-06・一発変換。改訂2 で置き換え）
  structuring_prompt_v4_interview.md  # ① 構造化プロンプト v4 仕様書（旧）
```
