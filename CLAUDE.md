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

## 費用のかかる実行の決まり（絶対厳守・決定ログ 2026-11-24・指示書81 追補1）
- モデルを走らせるジョブ（HF Jobs など費用のかかるもの）は、KAORU が明示に許可したときだけ起動する。原因を確かめるための試しの実行もこれに含む。費用 0 の調べは自由。

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
- [ ] 指示書79（説明の生成の時間を、過去の記録から測る）は報告して止まった（`docs/reports/79.md`）。走らせ直していない（費用 0）。速くする手の順番は KAORU と Claude が決める
  - 1 組 約 9.7 分＝起動 43 %・ラベルの段 48 %・選ぶ・まとめるの段 7 %。時間はほぼ出力のトークン数で決まる（毎秒約 99）
  - ラベルの段の JSON は fails_if 46 %・key 31 %。見積もり：B・C を外すと JSON −32 %、二回に分けると −37 %、キーを短くすると −3 %（考える部分の減りは分からない）
  - 記録に足りないもの：vLLM のログ（起動の内訳・速さ）・リクエストごとの時刻と最初のトークンまでの時間・機械の段の時間。KAORU×PA の記録と数の表は `local/` だけ
- [ ] 指示書80（出力を変えずに速くする）は順2 まで終えて止まった（`docs/reports/80.md`）。**b（組の中で二つの向きを同時に）で出力が変わった（4 組すべて。先に送った N_by_O だけ）**。§4 のとおり、向きは順に・組は 1 つずつ（既定のまま）。入れたのは §1-2（1 回の起動で何組か）と §1-3（時間の記録）だけ
  - 回 a は 2 回のジョブで一字同じ。KAORU×PA の a は v06 と違う（入力のトークンは同じ。同時の数 1→8 などの違い。切り分けていない）。同時の数を 1 に戻すかは KAORU が決める
  - 時間（a）：起動 256 秒、1 組 67〜196 秒（KAORU×PA 196 秒）、4 組まとめて 1 組あたり 197 秒。費用 約 $2.9〜3.0（1 回目は前払いの残高切れで回 a の後に止まった）
  - 流れ：fields → atoms → prompts（`docs/prompts/explanation_label_v0.3.txt` 02ec1ca8…・`explanation_select_v0.1.txt` 19e818fb…。直さない）→ checks（C8）→ card。KAORU×PA の 5・6 回目の出力を流すと、プロンプト 4 つ・カード 2 枚が一字同じ
  - 推論先：`endpoint.py`（local_http）・`hf_job.py`（hf_jobs。非公開のジョブで vLLM を起こし 127.0.0.1 に送る。設定は 5・6 回目のまま、同時の数 `--max-num-seqs 8` だけ足す）。記録は数と時刻だけ、vLLM のログは数だけ取り出す
  - 架空の組は `synthetic.py`（synthetic_v1 から 3 組）。KAORU×PA の組の JSON と出力は `local/t80/` だけ
- [ ] 指示書81（説明の生成を本番につなぐ・PC で同じ状態を作れる準備）は順3 まで終えて止まった（`docs/reports/81.md`・PR #222・#223）。KAORU が画面を見る。順4（表示のフラグ）は KAORU の許可の後。表示のフラグは切ったまま
  - 順3：本番と同じ流れ・hf_jobs・H200 で KAORU×PA を 1 回（入力は手元の公開の欄）。起動 246 秒・1 組 164 秒・約 $0.57。PA の求めている相手の ① は、部品の番号のような数が入って安全の確かめで出さなかった
  - PC の一式は `deploy/local_inference/`（README の手順 1〜8・get_model・start_server・check_server・nudge_pox・autostart・.env.example）。12/6 以降に KAORU が使う
  - HF Jobs の公開した口は Authorization を通さない。鍵は X-Api-Key にも入れる（llama-server は両方を見る）
  - 設定は `config/explain_runtime.toml` だけ（HF Jobs と PC が読む）：モデル ggml-org/gpt-oss-120b-GGUF・f5999036…・MXFP4 sha256 582bd40f…／llama.cpp b1b770d（build 12359）・公式の入れ物 ghcr.io/ggml-org/llama.cpp@sha256:a8cf841e…／温度 0・seed 0・medium・同時 1・候補 A。機械ごとに変えてよいのは置き場所だけ（POX_EXPLAIN_N_GPU_LAYERS・POX_EXPLAIN_N_CPU_MOE）。版の鍵（p81:…）に置き場所と推論先は入れない
  - 本番：`src/explain_service.py`・予定 `src/explain_queue.py`（表 explanation_queue・explanation_batches）。POX_EXPLAIN_BACKEND=hf_jobs（1 日 1 回。ジョブの口を公開し、llama-server の鍵で守る）／local_http（PC。すぐ作る・止まっていれば予定に残す）。未設定なら作らない。許可リストの組だけ。いまの入力・いまの版の説明だけを出す
  - 流れのカード（`X.is_pipeline`）には形の決まり（句 40 字・R1/R2 の述語・閉じた語彙）を当てず、安全の確かめだけを当てる。fugashi・unidic-lite を本番の requirements.txt にも入れた（流れを Render で動かすため）
  - 運営のルート `/ops/explanations/drain`・`status`（鍵 POX_EXPLAIN_DRAIN_TOKEN。enqueue・verify）。カードごとの版の記録は向きのレコードの meta_json
- [ ] 指示書81 追補1（走らせない決まり・画面のギャップを直す）は順1 まで終えて止まった（`docs/reports/81_addendum1.md`）。ジョブは起動していない。順2 は KAORU の明示の許可の後
  - 決定ログ 2026-11-24 は未受領（`docs/decisions/` にまだ無い。届いたら置く）
  - §2（費用 0）：llama.cpp と vLLM（harmony）が描くプロンプトは 24 件すべて一字同じ（トークン数も同じ・Reasoning: medium）。違いは重みの形（注意・埋め込み・出力が BF16→Q8_0）
  - 選ぶ段は `docs/prompts/explanation_select_v0.2.txt`（b767abf2…。v0.1 は残す）。流れのカードの ① は「可能性があります。」で終わる文だけ。禁止の語 forbid-v0.5（同種・ラベルの英語の名前）
  - ① の番号（部品・組・候補。括弧ごと）は `card.strip_ids` で消し、数を記録の form_fixes.ids_removed に。言い切りの数は meaning_end_ng
  - 縛りのスイッチ `EXPLAIN_BOUND_FOR_NO_PURPOSE`（設定 `[pipeline] bound_for_no_purpose`。既定は切る。版の鍵に入る）
  - 申し出の画面は、カードの route の meaning_purposes で目的を最初に選ぶ
- [ ] 指示書81 追補2（保存した出力で画面を出し直す）は報告して止まった（`docs/reports/81_addendum2.md`）。モデルは走らせていない
  - 同じ後段（追補1）に通すと、v06 の ① は 2/2 が出る。81 は 0/2（文の終わりの確かめ あり）・1/2（なし）。悪くなったのは 81 の出力の側（言い切り・ラベルの語・組の選びが v06 と同じ 0）
  - 手元の一式は `local/t81_addendum2/`（鍵の照合を外すのと型の ① は手元だけ。本番は変えていない）。画面写しは `local/kaoru_pa_screen/t81_addendum2/`
  - 決定ログ 2026-11-24 はまだ受け取っていない（プロジェクトのファイルがこの環境から開けない）
- [ ] 指示書81 追補3（v06 の条件を本番の版 explain-v06 にする）は本番の比べで止まった（`docs/reports/81_addendum3.md` §3-2）。本番の既定は explain-v06（PR #228）。p81 は `POX_EXPLAIN_VERSION=p81`
  - 本番の入力が v06 の記録と違う（入力 2/2・部品 69→57・候補 178→88・B・C が 0）。保存出力は入れていない。どうするかは KAORU が決める
  - 比べて入れる口は `/ops/explanations/replay`（drain の鍵・モデルは動かさない）。送る中身（記録と出力）は `local/t81_addendum3/request.json` だけ
  - HF Jobs のジョブは `POX_EXPLAIN_JOBS_PERMITTED=1` のときだけ起動する
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
