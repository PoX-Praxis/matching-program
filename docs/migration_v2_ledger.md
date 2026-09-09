# 移行手順書 — 台帳 v2 / 認証 本番反映（指示書18 §4）

**作成日:** 2026-09-08
**対象:** 指示書17（台帳 v2 基盤）＋指示書18（接続の根拠是正）の本番反映
**確定事項:** §4-1 の確認結果＝**既存データにメールアドレスは保持されていない** → **§4-3 を適用**

---

## 0. 前提（§4-1 の確認結果）

`email` を持つテーブルは `auth_identities` / `auth_tokens`（指示書17 PR#27 のマジックリンク認証）**のみ**。
`profiles` / `seekers` に email 列は無く、認証前の登録経路 `post_seeker` も収集していない。

→ 既存プロフィールに紐づくメールアドレスは**存在しない**。よって「既存 `subject_id` を引き取らせる」ことは**安全にできない**（アドレス無しに引き取りを許すと任意の id を誰でも取得できる）。**指示書18 §4-3 のとおり、既存プロフィールは引き継がず作り直す。**

---

## 1. 方針（§4-3）

| 対象 | 扱い |
|---|---|
| 既存 `subject_id`（認証なしの旧アカウント） | **引き継がない。** メール紐づけ経路は作らない（乗っ取り防止） |
| 既存 `profiles` / `seekers` | **読み取り専用の履歴として残す。** 台帳へは移さない |
| 既存 `vessels`（旧 RMW 由来の接続） | 読み取りフォールバックで表示（`ledger.load_all_vessels`）。**台帳イベントには移さない** |
| 既存 `user_snapshots` | 読み取り専用の履歴として残す |
| 旧 pending vessel（片側承認のみ） | **成立させない。** `connection_requests` は空なので、相手が改めて申請する |
| 新規利用者 | マジックリンクで新規登録。`subject.created` / `terms.accepted` が台帳に載る |
| 既存登録者の必要像 1:N 化 | **再構造化で `necessities` 行ができるまで待つ。** それまで照合は人起点フォールバック（現行どおり） |

**identity のバックフィルは行わない。** コード変更を伴う「移行スクリプト」は不要。本書は運用手順の明文化である。

---

## 2. 本番反映の手順（順序を守る）

### 2-1. 環境変数（Render / pox-web）

| キー | 値 | 備考 |
|---|---|---|
| `POX_SECRET_KEY` | 長いランダム秘密（32バイト以上推奨） | セッション署名・email ハッシュ/暗号の鍵。**必ず設定**（未設定だと開発既定鍵で警告起動） |
| `POX_ANCHOR_TOKEN` | 長いランダム秘密 | 日次 root を外部スケジューラから起動する場合（§2-4） |
| `POX_SMTP_HOST` / `POX_SMTP_USER` / `POX_SMTP_PASS` | メール配信 | 未設定なら開発モード（リンクをログ出力・本番では設定する） |
| `POX_SMTP_PORT` / `POX_SMTP_FROM` | 任意（既定 587 / USER） | |
| 既存: `DATABASE_URL` / `ANTHROPIC_API_KEY` / `POX_EMBED_BACKEND=nomic` / `POX_NOMIC_ENDPOINT` / `POX_NOMIC_API_KEY` / `POX_EMBED_MODEL_TAG=nomic-emb-v2` | 現行のまま | Nomic は自宅PC＋Cloudflare Tunnel |

### 2-2. スキーマ

- デプロイ時 `schema.init()` が全テーブルを冪等作成（`auth_*` / `ledger_events` / `attestations` / `anchors` / `connection_requests`(＋`predicted_role`/`channel`/`match_run_id`) / `necessities` / `necessity_evidence`）。
- 既存テーブルは非破壊。`connection_requests` への列追加は Postgres では DDL 冪等、SQLite では自己修復 ALTER。

### 2-3. 一括投入と単一ライター

- **本方針では一括投入は無い**（identity 移行をしないため）。よって「一括投入中に新規イベントを待たせる」オペレーションは不要。
- 将来、何らかの一括投入を行う場合は、`ledger_events.append_event` が唯一の書き込み経路（プロセス内 Lock＋Postgres advisory lock）である前提を守り、投入も同経路を通すこと。別経路で `ledger_events` に直接 INSERT しない。

### 2-4. 日次 root バッチ（指示書20・二重化＋停止検知）

**方式: GitHub Actions と cron-job.org の二重化。** どちらも `POST /ledger/anchor` を毎日叩く。
`run_daily` はバックフィル冪等なので二重に叩いても害はない（既アンカー日はスキップ）。片方が
止まってももう片方が刻む。Render Cron（`render.yaml` の `pox-anchor`）は有料のため使わない
（定義は残置可）。**完了日（前日 UTC）まで**をアンカー・空の日も空 root・落ちた日はバックフィル。

**運営が設定するもの:**

1. **GitHub repository secrets**（2件）
   - `POX_ANCHOR_TOKEN` … Render に設定したものと同値
   - `POX_ANCHOR_URL` … **`https://pox-web.onrender.com/ledger/anchor`**
     （本番ホストは `pox-web.onrender.com`。render.yaml のサービス名 `pox-web` に対応。
     旧記載の `pox-box` は誤り＝存在しないサービスで 404 になる。正確な URL は Render
     ダッシュボードの pox-web ページ最上部でも確認できる）
   - workflow は導入済み: `.github/workflows/daily_anchor.yml`（毎日 00:10 UTC・手動起動可・
     2xx 以外で失敗・Render コールドスタートに備えリトライ3回）／
     `.github/workflows/anchor_monitor.yml`（週1・`status?strict=1` を叩き 503 で失敗＝メール通知）

2. **cron-job.org のジョブ**（2件・GitHub の60日無活動停止や Render 障害から独立に検知）
   | ジョブ | メソッド/URL | 間隔 | ヘッダ |
   |---|---|---|---|
   | アンカー | `POST /ledger/anchor` | 毎日 00:20 UTC | `X-Anchor-Token: <値>` |
   | 監視 | `GET /ledger/anchor/status?strict=1` | 毎日 06:00 UTC | なし |
   - アンカーは GitHub（00:10）とずらして 00:20（同時でも単一ライターで直列化され壊れないが、無駄な競合回避）。
   - 監視ジョブは**失敗時（＝503）にメール通知**する設定にする。`days_behind>=2` で 503 が返る。

**停止検知の要点:** `GET /ledger/anchor/status` は `{last_anchor_date, last_anchor_seq, days_behind}`
を返す（認証不要）。`days_behind` は「今日 UTC − 最終アンカー日」の日数で、毎日刻めていれば
**正常時は 1**。`?strict=1` を付けると次のとき **503**、それ以外 200:
- `days_behind >= 2`（刻み損ね）
- **アンカー皆無だが台帳にイベントがある**（使い始めているのにアンカーが無い＝スケジューラ設定忘れ等の異常）

台帳が**空**（イベントもアンカーも無い＝未使用のデプロイ直後）のみ 200。よって「デプロイしたが
スケジューラ設定を忘れ、使い始めてもアンカーが刻まれない」最悪の状態を strict 監視が確実に検知する。
**二重化しても、この監視が失敗通知を出せることが最重要**（刻み損ねに気づく唯一の経路）。

---

## 3. 反映後の実機確認（自宅 Nomic 稼働時）

1. **認証**: `/login` → メール受信 → リンク → `/mypage` に着地。初回のみ `subject.created` / `terms.accepted` が台帳に載る（`GET /ledger/verify/<日付>` で翌日以降 root に含まれる）。
2. **登録→ベクトル化**: 構造化JSONを登録 → `202` → `generation_status: ready`。`necessities` に行ができ `will_vec`/`necessity_vec` が実体化（stub でなく nomic 次元 768）。`profile.structured` / `necessity.published` が台帳に載る。
3. **接続の根拠**: 双方が登録済み（`profile.structured` あり）の状態で相互承認 → `connection.established` が **grounded**（`grounding_report()` で確認）。片方未登録なら成立しない（`missing_profile_structured`）。
4. **照合**: `/v4/match` が必要像レコードのある人では `query_unit:"necessity"`、無ければ人起点フォールバック。個人は同一結果。

---

## 4. 既知の限界（本番反映前に判断が要るもの・指示書18 §7）

| 項目 | 判断 |
|---|---|
| `visibility.changed` の表示反映 | イベント/フラグは記録されるが **private 化が表示を変えない**。本番前に「表示制御を実装する」か「UI から公開範囲変更を出さない」かを決める。現状 API は本人限定で叩けるだけなので、UI に露出しなければ実害は無い |
| `is_live` の intent 完了失効 | `owner_kind='intent'` の必要像失効は未導出。**目的別必要像の動線を出す前に**実装する |
| `necessity.retired` の UI | 自主取り下げ動線は後段 |
| 認証ゲートの全面適用 | `mypage`/`profile` は依然 `?id=` で開ける（§7-5 の限界）。ポリシー §8 に告知済み。全面ゲートは認証成熟後 |
| ベクトルの pgvector 化 | `necessities.will_vec/necessity_vec` は現状 TEXT(JSON)。照合本番切替が育ってから |
| 日次 root の1日遅れ | 完了日のみアンカーする設計の帰結。鎖に頼る窓が実質2日分。アンカー間隔は Layer 1 の値で後から変更可 |

---

## 5. 要約

**既存データにメールが無いため identity 移行はしない（作り直し）。既存 profiles/vessels/snapshots は読み取り専用の履歴として残し、台帳には移さない。旧 pending は成立させない。必要像1:N は再構造化でバックフィル。本番反映は環境変数（とくに `POX_SECRET_KEY`）→ スキーマ冪等作成 → 日次 root スケジューラ、の順。反映後に自宅 Nomic 稼働下で 認証・登録ベクトル化・接続の grounded・照合 を実機確認する。**
