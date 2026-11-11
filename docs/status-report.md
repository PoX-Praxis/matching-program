# PoX matching-program 棚卸し（2026-11-08 時点）

**目的:** 引き継ぎ無しで、どの AI もこのリポジトリから現状を読み取れるようにするための土台。
**範囲:** `main`（`39bb2c5`・#163 のマージ後）。調査だけで、コードは変えていない。

**調べ方**
- 時系列は、GitHub の PR 一覧（#1〜#163）とコミットの記録から作った
  - 手元の clone は浅い（2026-09-18 以降の 82 コミットだけ）ので、それより前は GitHub から読んだ
- 構成・機能・状態は、ファイルとコードを読んで判断した。テストは走らせていない（キャッシュのファイルを作らないため）
- 本番（Render・DB）には触れていない。本番で実際に動いているかどうかは、コードと設定の記述からの判断である

**表記**
- 印の無い記述: コード・設定・記録から確かめた事実
- **【推測】**: 確かめていない推測

---

## 0. 要約

- **本体は 1 つの Flask アプリ**（`app.py` 4,091 行・ルート約 110）と、`src/` のモジュール 45 個でできている。本番は Render（`pox-web`）と Postgres（pgvector）
- データの形は、**v3 → v4 → v5 の 3 世代が並んで残っている**
  - いまの登録は v5（目的ごとの必要像・与え像を文の単位で持つ）
  - v4（意志と現状の 4 欄）は読み込みの互換として残っている
  - v3（seekers）は登録の入口を閉じ、旧データの読み出しと遅延移行のためだけに残っている
- **処理の流れ:**
  - 構造化: 利用者が自分の AI で行う
  - 非対称検索: nomic の埋め込み（必要像＝query、与え像・現状＝passage）
  - 判定: 上層（欠かせない要素の連言）と共鳴の門
  - 理由の示し方: LLM の生成文ではなく、文の対の引用
- **LLM による言語化は、本番の照合には入っていない**
  - サーバーが LLM を呼ぶ処理は 2 つ残っているが、どちらも古い経路である
    - v3 の `/match`
    - v3 から v4 へ移すときの必要像の生成
  - 2B 級の LLM の判定（L）は計測のスクリプトでだけ試した。規則を満たさなかった（指示書66C）
- **製品のコードは 2026-10-18 以降変わっていない**（`app.py` の最終更新は #149〜#154）。それ以降のコミットは、下層を開けられるかの計測（指示書65-A・66）だけ
- **引き継ぎの情報がリポジトリの外にある。** これが「引き継ぎ不要」を目指すうえでの最大の課題
  - 指示書の本文
  - 各指示書の報告
  - 計測の結果（`local/`）
  - `CLAUDE.md` と `README.md` は古い

---

## 1. 時系列

コミットの粒度は「1 指示書 ≒ 1〜数 PR」で、PR のタイトルに指示書の番号が入っている。時期は PR のマージ日で分けた。

| 時期 | PR | 何が作られ・どう変わったか |
|---|---|---|
| **2026-06-14〜06-19** 最初の骨組み（v3） | 直接コミット〜#1 | 手元の資料をアップロードしてリポジトリを整えた。次のものを作った:<br>・SQLite の最小 DB・`/seekers`・`/match`（LLM で照合）<br>・承認の台帳（vessels）・DM・コミュニティ・マイページ・プロフィール<br>・2 層（seeker は本人だけ、profile_view は公開）<br>・登録の UPSERT・編集の 2 タブ<br>PostgreSQL・pgvector への移行と、Render の設定（`render.yaml`）を入れた（#1） |
| **06-23〜06-25** v4 の埋め込み照合 | #2 と直接コミット | v4 の DB（profiles_v4・profile_vectors・derived_necessity・ledger_v4）を作った。あわせて PII のマスク、埋め込み（当初は Qwen3 想定・stub）、②必要像の生成（LLM）、照合（入れ子の補完べき乗平均）、v3.1 → v4 の遅延移行、Qwen3 の推論サーバーを入れた。<br>本体の形を、二軸（意志・現状）の v4 に移した |
| **07-09〜07-27** nomic 採用・v4.3・公開面 | #3〜#22 | 実験ブランチ（`exp/embedding-model-eval`）の結果から **nomic を採用**し、DB を 768 次元にした（束2a・2b）。<br>そのほかの変更:<br>・構造化プロンプト v4.2・v4.3<br>・マイページとプロフィールの v4.3 構成<br>・公開 API の遮断<br>・「つながる」（/connect）<br>・必要像の公開<br>・軌跡の MVP<br>・プライバシーポリシーと登録時の同意 |
| **08-06** 公開ページ | #23〜#26 | ランディングページを「PoX とは」（/about）に統合した。ポリシーの用語もそろえた |
| **09-08〜09-15** 認証・台帳 v2 | #27〜#78 | 次のものを入れた:<br>・マジックリンク認証<br>・**追記専用のイベント台帳**（ledger_events）と日次の Merkle アンカー<br>・必要像の 1:N 化（necessities）・必要像を起点にした照合<br>・意志形成（intent）<br>・認証ゲートの配線・ナビの整理・スマホ対応<br>・メール送信（Resend）<br>・下書きと確定（drafts）・表示名<br>・**サーバー側の必要像生成（fallback B）の廃止**（#69） |
| **09-17〜09-30** 統治・トーク・権利・照合の監査 | #79〜#125 | 次のものを入れた:<br>・移行の上書きとスナップショットの修正<br>・ポリシーの事実合わせ<br>・共通レイアウトと UI の規格<br>・ガバナンス台帳・合意の判定・トーク 4 種・プロジェクト参加<br>・行為権・休眠・加入の closure<br>・削除の台帳記録（redaction）と境界の監査<br>・軌跡の版ツリー・離脱<br>・**照合 API の本人限定と数値の非公開**（指示書55）<br>・ハンドル<br>・接続の取り下げ・申し出の文 |
| **10-04〜10-06** v5 の照合（段1〜3） | #126〜#132 | 照合の式を整理した（指示書56）。v5 の受信（目的ごとの必要像・与え像・文単位の照合）を入れた（指示書57）。<br>登録のプロンプトを v5 にし、根拠の表示と受信箱の承認待ちを作った（指示書58） |
| **10-09〜10-13** v5 の改訂・表示 | #133〜#146 | 次のものを入れた:<br>・構造化プロンプト v5 の改訂2〜rev4（A 対話・B 自分で書く）<br>・共鳴の門・生成元の記録・正準化 p3/c4<br>・壊れた JSON の補正<br>・v5 の人の編集の停止<br>・監査ルートの文単位<br>・プロフィールの v5 表示・受信箱のカード化<br>・与え像の公開（指示書64）<br>・照合の見分ける力の調査スクリプト |
| **10-17〜10-23** 要素単位の計測と 2 層の判定 | #147〜#155 | 次のものを入れた:<br>・可用性の修正（版のそろわない人だけを照合から外す）<br>・**2 層の判定**（上層＝目的、下層＝要素。下層の表示はフラグで閉じる）<br>・門のラベル・申し出る前の根拠・提示の記録・与え像だけの人<br>計測（65-A）の決定規則を事前に登録して測った。**製品のコードの最後の変更はここ**（10-18） |
| **10-24〜11-07** 計測の第2段（指示書66） | #156〜#163 | 次のものを作った:<br>・事前登録 v2（`scripts/probe_data/registration_v2.md`）<br>・背景の文集 bg-v2<br>・埋め込み・クロスエンコーダの案（66B）<br>・上層の連言の参考値<br>・L（小さな LLM）の判定の計測（66C）<br>**いずれも規則を満たさず、下層は開けない**。66D（役割で書き直した必要像）は、進め方の確認待ちで止まっている |

**閉じたまま・開いたままの PR**
- #122 と #131 は、マージせずに閉じた
- **#138（接続の終了 API と connection.closed の payload。#122-b の作り直し）は 10-09 から開いたまま**

**ファイルの最終更新**（手元の浅い履歴から）
- 2026-09-18 以前で止まっているもの: `README.md`・`SHARE.md`・`archive/`・`docs/` の手順書の多く・`.github/workflows/` の 2 つ（アンカー）
- 2026-10-18 が最後のもの: `app.py`・`src/` の照合と v5 まわり・主な画面
- 11-07 に更新されたもの: `scripts/` の計測のスクリプトと `spec/test_item_map.md` だけ

---

## 2. 構成

### 2-1. ルート

| ファイル | 役割 |
|---|---|
| `app.py` | Flask の本体。全ルート・認証ゲート・非同期ジョブ（ベクトル化）・照合の応答の組み立て |
| `render.yaml` | Render の設定（web `pox-web`・Postgres `pox-db`・cron `pox-anchor`）。環境変数の多くは「手入力」とだけ書かれている |
| `requirements.txt` | 本体の依存（5 行） |
| `CLAUDE.md` | AI 向けの引き継ぎ。**内容が古い**（§5） |
| `README.md` | 2026-06 の「②マッチング・メカニズム」の引き継ぎ文書のまま。**いまの本体の説明になっていない** |
| `SHARE.md`・`run_public.sh`・`run_public.bat` | Render 以前に、手元の PC をトンネルで一時公開する手順 |
| `.gitignore` | `local/`・`*.db`・`.env`・`static/uploads/*` などを外す |

### 2-2. `src/`（45 モジュール）

| 区分 | モジュール | 役割 |
|---|---|---|
| DB の土台 | `db_connect.py` | SQLite と Postgres の切り替え（`DATABASE_URL` の有無） |
| | `schema.py` | 全テーブルの DDL（Postgres 用・集中管理） |
| | `schema_v4.py` | v4 のテーブルの DDL |
| 構造化の受け取り | `profile_view.py` | 構造化 JSON の読み取り（コードの囲み・スマートクォートの補正）と、seeker → profile_view の変換（v3/v4/v5） |
| | `v5.py` | v5 の受信の検証（規則 1〜15）と保存（purposes・offers・profile_v5・sentence_vectors） |
| | `drafts.py` | 下書き → 確定 |
| | `pii_redaction.py` | 埋め込みの前の構造的な PII マスク |
| | `canon.py` | 正準化とハッシュ |
| | `db.py` | v3 の seekers・profiles |
| | `db_v4.py` | v4 の保存・ベクトル化・照合（`match_v4`） |
| | `migrate_v4.py` | v3.1 → v4 の遅延移行 |
| 埋め込み | `embedding_config.py` | モデルのタグ・次元・prefix・バックエンド（stub・qwen3・nomic など）の単一の設定元 |
| | `embedding_service.py` | `embed(text, role)`・g＝(1+cos)/2・現状の連結・8 本のベクトル |
| | `vector_cache.py` | モデルごとの npz キャッシュ（テストからだけ使われる） |
| 必要像 | `necessities.py` | 必要像の 1:N 台帳・ベクトル |
| | `necessity_gen.py` | ②必要像の生成（LLM）。いまは利用者側の必要像の検証・クランプ（`build_user_necessity`）が主な用途 |
| | `matching_necessity.py` | 必要像を起点にした v4 の順位付け |
| 照合 | `matcher_v5.py` | **本番の照合**。上層の連言・下層の要素・共鳴の門・文の対の引用 |
| | `matcher_v4.py` | v4 の入れ子べき乗平均。いまは監査ルートと軸の名前にだけ使う |
| | `match_config.py` | 作動点・門・フラグの単一の設定元 |
| | `presentations.py` | 提示の記録（フラグで閉じている） |
| 接続・台帳 | `ledger.py` | 承認・申し出・見送り・取り下げ・接続の状態 |
| | `ledger_events.py` | 追記専用の台帳。seq と prev_hash で鎖にする |
| | `anchor.py` | 日次の Merkle root |
| | `subject_ledger.py`・`member_ledger.py`・`intent_ledger.py`・`intent_content.py` | 主体・加入・意志形成の台帳 |
| | `redaction.py` | 削除の記録と検証 |
| | `snapshots.py`・`trajectory.py` | スナップショットと軌跡 |
| コミュニティ | `community.py`・`governance.py`・`agreement.py`・`talks.py` | コミュニティ・統治・合意の判定・トーク 4 種 |
| | `messages.py` | DM とコミュニティのチャット |
| 認証・表示名 | `auth.py` | マジックリンク。メールは鍵付きハッシュで持つ |
| | `mailer.py` | Resend の API・SMTP |
| | `handles.py`・`display_names.py` | ハンドルと表示名 |
| v3 の名残 | `connection_layer.py` | v3 の LLM 判定。`/match` から使う |
| | `matching_engine_live.py`・`github_transition_builder.py`・`validate_and_convert.py` | 2026-06 の仕様 v0.5/v0.6 の実験。**どこからも import されていない** |
| | `distribution_log.py` | スコアの分布の記録（テストからだけ使われる） |

### 2-3. `templates/`（20 画面と部品）

| 画面 | パス | 呼ぶ API |
|---|---|---|
| `about.html` | `/about`（`/` から転送） | — |
| `privacy.html` | `/privacy` | — |
| `login.html` | `/login` | `/auth/request` |
| `register.html` | `/register` | `/v4/drafts`・`/v4/drafts/<id>/confirm`・`/api/handle/check` |
| `mypage.html` | `/mypage` | `/api/my/*`・`/api/profile/<id>`・`/v4/seekers/<id>/status`・`retry` など |
| `edit.html` | `/edit` | `/api/profile/<id>/edit`・`core`・`overrides`・`/api/my/purposes` |
| `profile.html` | `/profile/<id>`・`/u/<handle>` | `/api/profile/<id>`・`/api/connections/preview`・`state`・`withdraw`・`/approve` |
| `connect.html` | `/connect` | `/v4/match`・`/seekers`（一覧）・`/approve`・`/v4/seekers/<id>/status`・`retry` |
| `inbox.html` | `/inbox` | `/api/inbox`・`/api/my/offers`・`/approve`・`/api/connections/decline` |
| `conversation.html` | `/conversation` | `/api/conversation`・`/messages`・`/api/upload`・申し出の取り下げ |
| `communities.html`・`community.html`・`talk.html` | `/communities`・`/community/<id>`・`/talk/<id>` | コミュニティ・トーク・プロジェクトの API |
| `trajectory.html` | `/trajectory/<id>` | `/api/profile/<id>` |
| `index.html` | `/dev`（`POX_DEBUG=1` のときだけ。本番は 404） | 旧 v3 の `/match`・`/seekers`・`/ledger` |

部品は `_base.html`・`_nav.html`・`_profile_view.html`・`_match_reason.html`・`_trajectory.html`。

### 2-4. `scripts/`

| 区分 | ファイル |
|---|---|
| 運用 | `init_schema.py`・`init_schema_v4.py`・`migrate_sqlite_to_pg.py`・`daily_anchor.py`・`audit_legacy_boundary.py`・`audit_inventory.py`・`purge_accounts.py`・`change_handle.py` |
| 計測（手元で実行。本番に触れない） | `discrimination_probe.py`（64）・`manual_pairs.py`・`element_probe.py`（65-A）・`element_probe_v2.py`（66B）・`element_probe_l.py`（66C）・`upper_distinct_ref.py`・`l_token_check.py`・`requirements-probe.txt` |
| 計測のデータ | `probe_data/`: `registration_v2.md`（事前登録）・`background_v1/v2.json`（背景の文集）・`element_sets_v1/v2.json`（正解集合）・`injection_v1.json`・`probe_people.json`・`probe_sentences.json` |

### 2-5. `docs/`・`spec/`・そのほか

| 場所 | 中身 |
|---|---|
| `docs/prompts/` | 構造化プロンプト v5 rev4（A 対話・B 自分で書く）。**画面はここから読み込む＝唯一の正** |
| `docs/matching.md` | 照合の仕組みと運用の注意（指示書55〜65B） |
| `docs/ledger_limits.md`・`migration_v2_ledger.md`・`schema_migrations.md` | 台帳の不変条件・移行手順・スキーマ変更の仕組み |
| `docs/admission.md`・`dormancy.md`・`leave.md`・`handles.md`・`trajectory.md`・`account_purge.md` | 機能ごとの設計メモ |
| `docs/deploy_*.md`・`render_fullsetup_guide.md`・`email_login_operations.md` | 構築・運用の手順。nomic のホストの手順は Render・Hetzner・自宅 PC の 3 通り |
| `docs/HANDOVER_2026-07_nomic_v4.md`・`production_design_notes.md`・`spec_v0.8_patch.md` | 2026-07〜09 の引き継ぎと設計ノート |
| `docs/ui_inventory.md`・`approval_flow_todo.md` | 2026-09 の UI 棚卸しと、未処理の TODO の記録 |
| `docs/measure/65A_decision_rules_v1.md` | 65-A の事前登録 |
| `spec/` | 仕様 v0.6・必要像生成プロンプト v1・構造化プロンプト v4/v5（旧）・`test_item_map.md`（受入項目とテストの対応表） |
| `archive/` | 仕様 v0.5・旧照合エンジン 3 版・実演の記録（2026-06） |
| `qwen3_server/`・`nomic_server/` | 埋め込みの推論サーバー。本番は nomic |
| `schemas/`・`examples/` | v3 時代の接続台帳の JSON Schema とサンプル |
| `tests/`（102 ファイル・テスト関数 866） | `.github/workflows/tests.yml` が main への push と PR で `pytest -q` を走らせる（Python 3.11） |
| `.github/workflows/` | `tests.yml`（CI）・`daily_anchor.yml`（毎日 `/ledger/anchor` を叩く）・`anchor_monitor.yml`（週 1 回の監視） |

---

## 3. 機能（PoX の処理の流れに沿って）

### 3-1. 構造化（①）

- **利用者が自分の AI で構造化する。** サーバーは構造化のための LLM を呼ばない
  - 登録画面（`/register`）が `docs/prompts/v5_A_dialogue.txt`（対話・推奨）か `v5_B_selfwrite.txt`（簡易）を表示する
  - 利用者はそれを自分の AI に渡し、出てきた JSON を貼る
- 受け口:
  1. `profile_view.parse_registration_text_report` が、コードの囲み・スマートクォート・壊れた JSON を補正し、補正したことを告知する
  2. `POST /v4/drafts` で下書きにする
  3. `POST /v4/drafts/<id>/confirm` で確定する
- 確定のとき、次の順で処理する:
  1. `v5.validate` で検証する
  2. 台帳（`profile.structured`・`necessity.published`）に**同期で先に**書く
  3. ベクトル化を非同期で回す
- **v5 の形:** 目的（最大 3）ごとに、向かう先・手段・必要像（最大 5 文・型 4 種・必須 最大 2）・数値（gate_s・gate_u）を持つ。ほかに、与え像・関心・現状 4 欄・補足の材料を持つ
- 生成元は `_meta.source` に記録する（v5r2〜v5r4）
- **閉じた入口:**
  - v3 の `POST /seekers` は 410 を返す
  - v4 の `POST /v4/seekers` はまだ受け付けるが、画面からは呼ばれていない
  - v5 の人には、v4 の編集・再試行を拒む（409）

### 3-2. nomic による非対称検索（②）

- 埋め込みは `nomic-embed-text-v2-moe`（タグ `nomic-emb-v2`・768 次元）
  - 呼び出しは `embedding_service.embed(text, role)`
  - 本番の推論サーバー（`nomic_server/model.py`）は、モデルの revision を固定していない。`POX_EMBED_MODEL_REVISION` は提示の記録に書くだけ。計測のスクリプトは revision を固定している
  - prefix は `embedding_config` にあり、query＝`search_query: `、passage＝`search_document: `
- **非対称:**
  - 必要像の文 → query
  - 与え像の文・現状（4 欄・全文）→ passage
  - 向かう先どうしの共鳴 → 対称（symmetric）
- 文ごとのベクトルは `sentence_vectors`、人ごとのベクトルは `profile_vectors` に入る
- 埋め込みの前に、必ず PII をマスクする
- 推論サーバーは `nomic_server/`。`render.yaml` の注記では、本番は自宅 PC のトンネルの URL（`POX_NOMIC_ENDPOINT`）を指す
- **モデルの隔離（`model_tag`）:**
  - 違うモデルのベクトルどうしは比べない
  - 現行のタグのベクトルが無い人は、その人だけを照合から外す（65B-1）
- stub（意味の無い擬似ベクトル）の間は、照合の結果を出さない（`POX_DEBUG=1` を除く）

### 3-3. LLM による言語化（③）

- **本番の照合の流れには入っていない。** 照合の理由は、LLM の生成文ではなく、**必要像の文と相手の文の対を引用して**示す
  - `_match_reason.html`・`/api/connections/reason`・`preview`
  - 引用は文の区切りまでで切る（#132）
- サーバーが LLM（Claude の API）を呼ぶコードは残っている。ただし、どれも古い経路である
  - `connection_layer.py`（v3 の `/match` の判定）
    - `/match` はログイン済みの本人なら呼べるが、呼ぶ画面は `/dev`（本番は 404）だけ
    - 鍵（`ANTHROPIC_API_KEY`）が無ければ demo の判定になる
  - `necessity_gen.generate_necessity`（②必要像の生成）
    - 非同期ジョブからの「fallback B」は廃止済み（#69）。必要像の無い入力はエラーにする
    - ただし、`/v4/match` の中の v3.1 → v4 の遅延移行（`migrate_v4` → `ingest_profile_v4`）からは、まだ呼ばれる（§5）
- 計測では、2B 級の LLM の含意の判定（L）を `scripts/element_probe_l.py` で試した
  - 規則を満たさず、本番には入れていない
  - 結論は 2B 級のモデルについてのもので、L という方法そのものを否定するものではない

### 3-4. マッチング（照合）（④）

- 入口は `POST /v4/match`。本人だけが呼べ、応答は `_v5_match_response` → `matcher_v5.match_pair` が作る
- **上層（目的を満たす相手）:**
  - その方向の目的の欠かせない要素（必須）がすべて、相手の文で作動点を超えたら成立する（連言）
  - 作動点はいま g＝0.70（`match_config.UPPER_OPERATING_POINT`）
- **共鳴の門:**
  - 門の強さ＝gate_s × (1 − gate_u)。0.5 以上のときだけ門を立てる
  - 門が立ったら、向かう先どうしの共鳴が 0.70 未満の方向は不成立にする
  - 閲覧者自身の目的の門だけをラベルにする（65B-3）
- **下層（この要素に応える相手）:**
  - 計算と保持はする
  - **表示は `POX_LOWER_LAYER_OPEN` で閉じている**（既定は閉）
  - 開けるかどうかは、計測（65-A・66）の結果を見て KAORU が決める
- **応答の決まり:**
  - 数値（スコア・g）を画面と公開 API に出さない
  - 候補の並びは中立（id 順）
  - 数値は監査ルート（`/ledger/audit/match`）にだけ出す
- **v4 の人の扱い:** v4 の人（与え像なし）は、v5 の照合の中で「目的 1 つ・必要像の全文 1 つ・相手の側は現状」として読む
- 旧 v4 の入れ子べき乗平均（`matcher_v4`・`matching_necessity`）は、いまは監査ルートと「最も効いた軸」の名前にだけ使われる

### 3-5. 照合の後（接続・会話・コミュニティ・記録）

- **申し出と承認:**
  - 申し出は `/approve`（申し出の文つき）。受信箱で承認するか見送る。取り下げられる
  - 相互の承認で接続が成立する。DM は接続の後だけ
  - 申し出の前に、プロフィールで「あなたの必要から見た、この人」（根拠）を見られる（65B-4）
- **台帳:**
  - 追記専用の `ledger_events` に、seq と prev_hash で鎖にして書く
  - 毎日の Merkle root（アンカー）は `/ledger/anchor`。GitHub Actions と cron-job.org の二重化
  - 検証は `/ledger/verify/<date>`・`/ledger/anchor/status`
  - 削除の記録（redaction）は境界の seq つき（`POX_LEGACY_BOUNDARY_SEQ`。未設定なら本番は起動しない）
- **コミュニティ:**
  - 加入（申請・承認・見送り後の再申請）・離脱
  - トーク 4 種と合意の判定・プロジェクト参加・行為権・休眠
- **軌跡:** スナップショットの版ごとのツリー（`/trajectory/<id>`）。本人は中身を伏せられる
- **認証:**
  - マジックリンク（Resend）。セッションは 30 日
  - メールは鍵付きハッシュ（`POX_EMAIL_SALT`。変えると全アカウントに入れなくなる）
- **ハンドルと表示名:** 「表示名（@ハンドル）」で出す。同一性の根拠は subject_id（画面には出さない）

---

## 4. 状態

| 部分 | 状態 | 根拠 |
|---|---|---|
| v5 の登録（下書き → 確定）・プロフィール・マイページの表示 | **動いている** | 画面から呼ばれ、テストがある。CI がある |
| nomic の埋め込み・文単位のベクトル | **動いている【推測】** | コードと `render.yaml` の注記は nomic を前提にしている。本番の推論サーバー（自宅 PC とトンネル）がいま起動しているかは、リポジトリからは分からない |
| 照合（上層・共鳴の門・引用の根拠）・申し出・承認・受信箱・DM | **動いている** | 画面から呼ばれる。指示書63〜65B で整えた |
| 下層の表示・提示の記録 | **作りかけ（意図的に閉じている）** | フラグ（`POX_LOWER_LAYER_OPEN`・`POX_PRESENTATION_LOG_OPEN`）で閉じている。下層を開ける条件は計測で満たせていない |
| 下層を開けるための計測（66） | **作りかけ** | 66B・66C は終わった（規則を満たさない）。66D は進め方の確認待ち。順9（KAORU の判断）が残る |
| 構造化プロンプト rev5（65-C） | **作りかけ（本文待ち）** | 65-C の実装は、Claude が書く本文が届くのを待っている（指示書65-C §6） |
| 台帳・アンカー・削除の記録・コミュニティ・トーク・軌跡・認証・ハンドル | **動いている【推測】** | 9 月にまとめて作り、テストがある。10 月以降は手を入れていない。本番で使われている程度は分からない |
| 接続の終了 API（connection.closed） | **作りかけ** | PR #138 が開いたまま |
| v4 の直接登録（`POST /v4/seekers`）・v4 の編集の再ベクトル化 | **放置気味** | 画面からは呼ばれない。v4 の人の編集の経路としてだけ残る |
| v3（`/seekers` の一覧・`/match`・`/ledger`・`/dev`）・`connection_layer.py` | **放置・旧データの互換** | 登録の入口は 410。`/match` を呼ぶ画面は `/dev`（本番は 404）だけ |
| `matching_engine_live.py`・`github_transition_builder.py`・`validate_and_convert.py`・`schemas/`・`examples/`・`archive/` | **未使用** | どこからも import されていない。2026-06 の v0.5/v0.6 の名残 |
| `vector_cache.py`・`distribution_log.py` | **未使用（テストだけ）** | 実験の資産を還流したもの。本体からは呼ばれない |
| `qwen3_server/` | **未使用【推測】** | 本番は nomic。`embedding_config` の既定値と `CLAUDE.md` だけが Qwen3 を指している |
| `SHARE.md`・`run_public.*` | **未使用【推測】** | Render の前の一時公開の手順 |
| `docs/deploy_2a_nomic_service.md`・`deploy_hetzner_nomic.md` | **未使用の別案【推測】** | `render.yaml` の注記は自宅 PC のトンネルを指す |

---

## 5. 重複・矛盾

### 5-1. 引き継ぎの文書がいまとずれている

- **`CLAUDE.md`:**
  - 「継続中タスク」が Qwen3 のローカルサーバーと `POX_EMBED_BACKEND=qwen3` になっている。本番は nomic（`render.yaml` の注記と `docs/matching.md`）
  - 「主要ファイル構成」は 10 ファイルほどしか挙げていない
  - スキーマの節は v4 だけで、v5 の説明が無い
- **`README.md`:** 2026-06 の「②マッチング・メカニズム」の引き継ぎで、`spec/matching_spec_v0.6.md` を読めと書いてある。いまの本体（Flask・v5・台帳）の入口になっていない
- **`docs/HANDOVER_2026-07_nomic_v4.md`・`production_design_notes.md`・`spec_v0.8_patch.md`:** 時点の記録で、いまの正ではない。どれが正かの印も無い
- **`spec/structuring_prompt_v4_interview.md`・`spec/structuring_prompt_v5.md`:**
  - 旧版のプロンプト
  - v5 のほうは冒頭に「置き換え済み」とあるが、v4 のほうには無い
  - 正は `docs/prompts/`

### 5-2. 古い実装の残り

- **v3・v4・v5 のデータが並んで残っている:**

  | 世代 | テーブル |
  |---|---|
  | v3 | `seekers`・`profiles` |
  | v4 | `profiles_v4`・`derived_necessity`・`profile_vectors`・`ledger_v4` |
  | v5 | `profile_v5`・`purposes`・`offers`・`sentence_vectors` |
  | 1:N（指示書17） | `necessities` |

  表示の正は profiles_v4 と profile_v5。v3 は移行の元としてだけ残る
- **サーバー側の必要像生成が 1 か所に残っている:**
  - 「fallback B は廃止（#69）・サーバー側の必要像生成はしない」とある
  - ところが `/v4/match` の中の v3.1 → v4 の遅延移行（`migrate_v4.ensure_migrated` → `ingest_profile_v4` → `generate_necessity`）は、まだサーバー側で必要像を作る（鍵があれば LLM、無ければ demo）
  - 【推測】本番に v3 だけの人がもういなければ、実害は無い
- **`POST /v4/seekers` の説明文が古い:** 「B. 必要像フィールド無し → サーバー生成」と書いてあるが、実際の非同期ジョブはエラーにする
- **照合のエンジンが 2 つある:**
  - `matcher_v4`（べき乗平均）と `matcher_v5`（文単位）
  - 本番は v5。v4 は監査ルートにだけ残る。監査ルートが出す v4 の値（入口の閾値 0.70 の判定など）は、本番の判定と別の式である
- **LLM 判定のコードが 3 つある:** `connection_layer.py`・`matching_engine_live.py`・`archive/matching_engine*.py` は、同じ v3 の LLM 判定の世代違い
- **台帳が何系統もある:**
  - `ledger.py`（vessels・connection_requests）
  - `ledger_events`（追記専用の正）
  - `ledger_v4`（v4 の照合の監査）
  - 主体・加入・意志形成・ガバナンスの各台帳（すべて ledger_events の上に乗る）
  - 【推測】`vessels` と `ledger_v4` は旧い系統で、新しい書き込みは ledger_events に寄っている

### 5-3. 設定の不一致

- **`embedding_config` の既定値が古い:**
  - `POX_EMBED_MODEL_TAG` の既定は `qwen3-embedding-0.6b-d1024`（1024 次元）、`POX_EMBED_BACKEND` の既定は `stub`
  - 本番の DB は 768 次元（束2a）
  - 環境変数が無いと、stub の 1024 次元になる
  - backend とタグが食い違うと起動しない、というガードはある
- **TODO が残ったまま:** `embedding_config` の「nomic の prefix は実機確認後に追記」「NOMIC_ENDPOINT はホスト確定後に設定」は、すでに決まっている
- **`render.yaml` に要らない cron が残っている:**
  - 有料の cron `pox-anchor` が宣言されている
  - `docs/migration_v2_ledger.md` は「Render Cron は有料のため使わない。GitHub Actions と cron-job.org の二重化」としている
  - 【推測】`render.yaml` を Blueprint として同期すると、要らない cron ができる
- **`render.yaml` に載っていない環境変数:** コードが読む次の変数が載っていない
  - `POX_LOWER_LAYER_OPEN`・`POX_PRESENTATION_LOG_OPEN`・`POX_PRESENTATION_RETENTION_DAYS`
  - `POX_OFFER_PUBLIC_SINCE`・`POX_NECESSITY_PUBLIC_SINCE`
  - `POX_ANCHOR_TOKEN`・`POX_NOMIC_API_KEY`・`POX_EMBED_MODEL_REVISION`
  - 照合の係数（`POX_ALPHA`・`POX_BETA`・`POX_GAMMA_MAX`・`POX_P_SHARPNESS`・`POX_SHORTLIST_K`）など

  本番でどの値になっているかは、リポジトリからは分からない
- **使われない係数が残っている:** 照合の係数 γ・p・α・β は「照合では使わない（指示書56）」とあるが、生成時の互換の値として `match_config` と DB の列に残る
- **DDL が 2 か所にある:**
  - `schema.py` に全テーブルの DDL がある
  - 各モジュール（`v5.py`・`ledger.py`・`auth.py` など）も自分で `CREATE TABLE IF NOT EXISTS` を持つ
  - 【推測】SQLite の自己初期化と Postgres の集中 DDL の二重管理。列を足すときに片方を忘れる恐れがある（`docs/schema_migrations.md` が冪等の移行の仕組みを説明している）

### 5-4. 枝と PR

- リモートの枝が 55 本ある（`claude/t60-*`〜`t66c-*`・`continuation-2k4jfj-50`〜`57` など）。ほとんどはマージ済み
- `exp/embedding-model-eval` は実験の保管用（`docs/production_design_notes.md` が参照している）
- 開いたままの PR が 1 つある（#138）

---

## 6. 未コミットの変更・TODO・FIXME

**未コミットの変更**
- 無い。`git status` は、追跡しているファイルに変更が無い。stash も無い
- 追跡していないものは、`.gitignore` の対象だけ（`local/`・`__pycache__/`・`.pytest_cache/`）
- **`local/`（コミットしない決まり）にある計測の材料と結果:**
  - KAORU・PA の文
  - 65-A・66 の結果（JSON と MD）
  - 埋め込みのキャッシュ
  - 指示書66C のスコア
- これらはリポジトリの外にあり、ほかの AI からは見えない

**TODO・FIXME**（`tests/` と `spec/test_item_map.md` を除く。FIXME・XXX・HACK は無い）

| 場所 | 内容 |
|---|---|
| `src/embedding_config.py:45` | embgemma・nomic の prefix を実機確認後に追記 → nomic はすでに設定済み。**古い TODO** |
| `src/embedding_config.py:107-108` | EMBGEMMA・NOMIC の endpoint はホスト確定後に設定 → nomic は環境変数で設定済み。**古い TODO** |
| `nomic_server/model.py:17`・`run_nomic.bat:19` | 実機で次元を確かめて NOMIC_DIM に設定 → 768 で確定済み。**古い TODO**【推測】 |
| `src/embedding_service.py:16・173` | prefix が None のモデルは ValueError（TODO を埋めること） |
| `templates/about.html:225` | 指示書09・14 の適用後に「作っている人のプロフィール」への導線 |
| `templates/login.html:69` | **運営: 実在する問い合わせ先アドレスに差し替える**（pox-praxis.com のメールボックスを用意する） |
| `docs/approval_flow_todo.md` | 指示書27 時点の「承認の動線」の未処理 TODO の記録。【推測】指示書55-5・58・63 で多くは片付いているが、文書は更新されていない |

---

## 7. コードから読み取れる設計上の判断

| 判断 | 何をしているか | なぜか（コードの注記から。注記が無いものは推測） |
|---|---|---|
| **構造化は利用者の AI にさせ、サーバーは判断しない** | プロンプトを渡して JSON を受け取るだけ。必要像はプラットフォームが生成しない（#69・#82・#87） | ポリシーの事実（必要像の生成主体は利用者側）に合わせるため。「判断しない採集者」。サーバーに LLM の費用と責任を持たせない【推測】 |
| **受け取った値は無検証で信頼しない** | 範囲の検証・クランプ。供給された γ は使わない。目的の id はサーバーが振る | 利用者の AI の出力は壊れていたり、範囲外だったりするため |
| **本人の層と公開の層を分ける** | seeker（本人）と profile_view（公開）。`GET /profile/<id>` は `get_profile_view()` だけを呼ぶ | 生テキスト・根拠を第三者に出さないため（`CLAUDE.md` のセキュリティ制約） |
| **埋め込みの前に PII をマスクする** | 構造的な PII（メール・電話・URL など）を型つきのトークンに置き換える | 生の文を埋め込みのサーバーに送らないため（I 章） |
| **非対称の検索** | 必要像＝query、相手の文＝passage | 「求める側」と「自己紹介の側」は文の役割が違うため。nomic の推奨する prefix の使い方 |
| **モデルを 1 つに限り、タグで隔離する** | 違う model_tag のベクトルを混ぜない。現行タグを持たない人だけを外す | 尺度の違うベクトルの比較は意味をなさないため。1 人の未整備で全員の照合を止めないため（65B-1） |
| **数値を画面と公開 API に出さない・並びを中立にする** | 照合の応答は id と軸の名前だけ。数値は監査ルートだけ | スコアが順位や序列として読まれ、体験が「割り当て」になるのを避けるため（指示書55） |
| **理由は生成しない・引用で示す** | 根拠は必要像の文と相手の文の対 | 生成文で理由を作らないため（指示書66 の禁則）。もっともらしい作り話を避けるため【推測】 |
| **上層は連言、下層は閉じる** | 目的の必須がすべて満たされたときだけ上層が成立する。下層はフラグで閉じる | 1 要素の偶然の一致は人数が増えると必ず起きるため（65-A）。誤って開けると体験が割り当てになる害が、取りこぼしの害より大きいため（registration_v2 §5-1） |
| **閾値は暫定とし、事前登録した規則で決める** | 0.70 の根拠（無関係な文でも g≈0.6 になる異方性）をコメントに残している。作動点は計測の前に規則を登録する | 数値を見た後に規則を変えると、楽観に寄るため（registration_v2・65A_decision_rules_v1） |
| **追記専用の台帳と日次のアンカー** | 書き換えない。seq と prev_hash の鎖。Merkle root を外の 2 系統で毎日刻む | 接続・合意・削除の記録を後から改ざんできないようにするため。1 系統が止まっても刻めるようにするため |
| **台帳を先に、外部依存を後に** | 確定は台帳とスナップショットを同期・必須で書き、ベクトル化を非同期にする | 埋め込みのサーバーの障害が「確定した事実」に影響しないようにするため（指示書35） |
| **メールは鍵付きハッシュで持つ** | ソルトは SECRET_KEY と独立。本番で未設定なら起動しない | メールアドレスを DB に平文で残さないため。ソルトの取り違えで全アカウントを失わないため |
| **未設定なら起動を止める** | `POX_EMAIL_SALT`・`POX_LEGACY_BOUNDARY_SEQ`・backend とタグの食い違い | 「既定値にこっそり倒れる」事故を防ぐため（fail-fast） |
| **stub では照合の結果を出さない** | 本番で `ALLOW_STUB` を拒否する | 意味の無いベクトルの結果を本物と取り違えないため |
| **古い経路は消さずに閉じる** | v3 の登録は 410。dual-write の関数は保持する | 既存データの整合と、将来の移行の判断のため（`post_seeker` の注記） |

---

## 8. 判断が必要な点

**引き継ぎを不要にするために**
- **リポジトリの入口の文書を作り直すか。** いまの `README.md`（2026-06）と `CLAUDE.md` は古い。どれを正とし、どれを消すか・アーカイブに移すか
  - 案: `README.md` を本体の説明にし、`CLAUDE.md` は決まりと現状への道案内だけにする
- **指示書と報告をリポジトリに入れるか。** いまは次のものがリポジトリの外（チャットとセッションの作業領域）にある
  - 指示書の本文（指示書06〜66・追補）
  - 各指示書の報告（`PoX_指示書XX_報告.md`）

  入れるなら、置き場所（例 `docs/instructions/`・`docs/reports/`）と、生テキスト・根拠・PA の数値を除く決まりを決める
- **計測の結果をどこまでリポジトリに残すか。** `local/` には KAORU・PA の文が入っているため、コミットしない決まりがある
  - 文の本文を除いた集計（判定・曲線・AUC など）だけを `docs/measure/` に残すか
  - 再現に要る材料（KAORU・PA の文）をどう渡すか
- **「時点の記録」と「いまの正」を分ける印を付けるか。** 対象は `docs/HANDOVER_*`・`spec/`・`docs/deploy_*` など

**整理**
- **未使用のコードと文書を消すか、アーカイブに移すか:**
  - `matching_engine_live.py`・`github_transition_builder.py`・`validate_and_convert.py`・`schemas/`・`examples/`
  - `SHARE.md`・`run_public.*`・`qwen3_server/`
  - nomic のホストの別案の手順 2 つ
- **v3 の経路を閉じるか:** `/match`（LLM の判定）・`/seekers` の一覧・`/ledger`・`/dev`・`connection_layer.py`
  - 決める前に、本番に v3 だけの人が残っているかを確かめる必要がある（遅延移行の中のサーバー側の必要像生成も、これで要否が決まる）
- **v4 の直接登録（`POST /v4/seekers`）と、v4 の人の編集の経路を残すか**
- **`render.yaml` の `pox-anchor` cron を消すか。** 載っていない環境変数を一覧として書き足すか（値は書かない）
- **`embedding_config` の既定値を、本番に合わせて nomic・768 にするか。** 古い TODO を消すか
- **本番の nomic の推論サーバーでモデルの revision を固定するか。** いまは固定していないので、上流の更新でベクトルが変わりうる【推測】
- **DDL の二重管理（`schema.py` と各モジュール）を一本にするか**
- **開いたままの PR #138（接続の終了 API）を進めるか、閉じるか。** 55 本のリモートの枝を消すか

**製品と計測**
- **下層を開けるか**（順9・KAORU の判断）。66B・66C では、どの案も規則を満たさなかった
- **66D（役割で書き直した必要像）の進め方:** 書き直しの指示の文面・文の数・C 群の数・忠実性の 10 対の組み方
- **構造化プロンプト rev5（65-C）の本文**（Claude が作る予定）を待つか
- **上層の連言を「必須ごとに別の文」に変えるか。** 参考値は出してある。採るなら、新しい事前登録で決まりを先に固定してから測る
- **`login.html` の問い合わせ先のアドレスの用意**（運営の TODO）
