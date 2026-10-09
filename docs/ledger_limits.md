# 台帳の既知の限界・不変条件・確認済み事項（指示書45 A-4・A-5・45B・45C）

更新: 2026-09-27

## R-5: `basis_seq` は申告値（A-4）

`purpose.agreed.basis_seq`（およびトークの基準点）は、トークを立ち上げた時点でサーバーが記録した**申告値**であり、目的の提起の時刻そのものは台帳からは検証できない（受容済みの限界）。
将来、提起の時点で**内容を持たない参照イベント**（本文のハッシュも載せない、時点だけを固定するイベント）を台帳に足せば、基準点を台帳上で検証できるようになる。

## R-2: `approvals` は鍵を要求しない（A-5）

- `purpose.agreed` / `member.joined` / `intent.participant.joined` / `intent.completed` の `approvals[]` は、**サーバーが記録したアカウント（subject_id）の列**である。署名・公開鍵は含まない。
- 鍵を持たないアカウントだけで、提起・投票・合意・台帳への記録まで全機能が動く（`tests/test_ledger_hygiene.py::test_t088_approvals_work_without_keys`）。
- **是認ログ（attestations）**: テーブル `attestations(event_hash, by, pubkey, sig, at, level, canon_version)` は `src/schema.py` に**定義されているが、書き込む経路は無い**（空のまま。日次アンカーの Merkle 葉には含める実装だけがある）。したがって**現時点で署名機構は無い**（approvals はサーバー記録のアカウント列）。本指示書では新設しない。

## 台帳に書かないもの（不変条件・45A 追補）

- **加入の見送り**は台帳に書かない（`admission.rejected` を作らない）。承認だけが既存の `member.joined` で記録される。設計は `docs/admission.md`。
- **休眠**は台帳に書かない（表示時の導出）。設計は `docs/dormancy.md`。

## `discussion_hash` v1（凍結・45B §2）

> **`discussion_hash` v1**: 合意対象トークの**発言のみ**を、**挿入順（`talk_posts.ins_seq` 昇順）**で `投稿者id: 本文` の形にし、改行（`\n`）で連結した文字列（UTF-8）の SHA-256（16 進小文字）。

- **題名・収束案・票は含めない**（収束案は `purpose.agreed.conclusion_hash`、票は `approvals[]` が担う）。
- **並び順はタイムスタンプではなく挿入順**。`ins_seq` はトーク内で 1 から振る連番で、`(talk_id, ins_seq)` は一意。
- 実装: `talks._discussion_text` → `governance.discussion_hash`。
- **参照の誤りの記録**: 指示書45 §2-2 の「43 §3-3」は用語と文言の節で、この範囲を定義していない。**指示書41 §4 は `discussion_hash` というフィールドを固定したが、対象範囲を決めていなかった**。範囲はこれまで**コードにのみ**存在し、本節で初めて文書に固定した。
- **本文の正規化**: 発言の本文は投稿時に前後の空白を除いて保存される（`POST /api/talks/<id>/posts` の `strip()`）。再計算は**保存値をそのまま**使い、追加の正規化（空白・改行・Unicode 正規化）は**しない**。
- **並び順の変更の記録**: 45B 以前の実装は `created_at` 昇順（タイムスタンプ順）で並べていた。45B で `ins_seq`（挿入順）に改め、既存の発言には**それまでの並び（`created_at` 昇順、同時刻は `post_id` 昇順）どおりに**番号を補完した。したがって既存の合意のハッシュは変わらない（以前は同時刻の発言の順序が不定だった）。
- **未決（v1 の対象外）**: 属性への言及が**収束案・題名**に載る場合の削除。v1 の `redaction.recorded` は発言（`hash_kind = "discussion"`）だけを扱う。

## 削除の台帳記録 `redaction.recorded`（45B）

```
redaction.recorded {
  talk_id,        合意対象トーク
  target_ref,     影響を受ける合意イベントの event_hash（purpose.agreed 等）
  hash_kind,      "discussion"（v1 はこれのみ）
  canon_version,  "v1"（上の discussion_hash v1）
  prev_hash,      削除前の discussion_hash（初回は合意の保存値、2 回目以降は直前の result_hash）
  result_hash,    削除後の本文で再計算した discussion_hash
  scope_digest,   削除された発言 id 集合の正準化ハッシュ（本文は含めない）
  reason_class,   "legal" | "subject_request"
  decided_by,     削除を確定したアカウント（運用者）
  recorded_at
}
```

- **削除された本文そのもの**と**申立てた人物**は台帳に書かない。
- **検証**（`redaction.verify_discussion`）: 現行本文から v1 で再計算し、最新の `result_hash`（削除記録が無ければ合意の保存値）と比べる。一致すれば「削除された状態（`redacted`）」または「無傷（`intact`）」、不一致または連鎖の切れは**改ざん（`tampered`）**。削除記録を台帳から剥ぎ取ると、保存値と一致しなくなるか連鎖が切れるので、改ざんとして検出される。
- **本文を伏せる操作と申立ての受付経路はまだ無い**（45B §4。予約 53）。当面の削除は運用者が次の**削除手順**で行う。

### 削除手順（45C §2）

1. **粒度は発言単位**。**発言内の部分的な削除は行わない**（`scope_digest` は発言 id の集合であり、発言内の範囲を表現できない。部分削除を許すなら範囲表現が要る＝後続）。
2. 削除する発言の本文を、**伏せ字の定数 `【非表示】` そのもの**に置き換える（`redaction.REDACTED_BODY`。**前後に空白・改行を付けない**）。**行は消さない**（行を消すと並びと投稿者が失われ、再計算できない）。
   `UPDATE talk_posts SET body = '【非表示】' WHERE post_id IN (...);`
3. `redaction.record_redaction(talk_id, target_ref, redacted_post_ids=[...], reason_class=..., decided_by=<運用者のアカウント>)` を呼ぶ。本文が定数そのものでない発言・存在しない発言が含まれていれば**記録を拒否する**（同じ入力から必ず同じ `result_hash` が出るようにするため）。
- `scope_digest` は発言 id の**集合**の正準化ハッシュで、**順序に依存しない**。
- 伏せ字の定数そのもの（前後の空白を除いて `【非表示】` だけ）の発言は**投稿できない**（400）。削除済みの発言と区別できなくなるため。

## legacy の合意と境界（45B §3・45C §1）

- **境界 seq 以下の合意**は、合意後の追記が止められていなかった（PR #101 以前）か、並び順が挿入順に確定していなかった（PR #113 以前）ため、再計算が一致しなくても**改ざんと判定しない**（台帳の保存値を正とし `legacy_unverified` と返す）。legacy の連鎖も台帳の保存値から始める。**継承の印は台帳に書かない**。
- **境界は 2 つある**: **#101**（合意後の追記が不可になった時点・`2026-09-23T08:49:23Z` に main 反映）と **#113**（並び順が挿入順に確定した時点）。#101〜#113 の間に閉じた合意に同時刻の投稿があると、当時の並びを再現できない可能性がある。
- **境界 seq は環境変数 `POX_LEGACY_BOUNDARY_SEQ`**（0 以上の整数）で与える。**未設定・不正なら本番は起動しない**（`app.py` の起動時ガード。フォールバックしない＝「legacy なし」にも「全件 legacy」にも倒さない）。起動後に未設定になっても `redaction.verify_discussion` は例外を投げる。

### 境界を決める監査（2 段・読み取りのみ）

本番（`DATABASE_URL` が設定された環境）で実行する:

1. `python scripts/audit_legacy_boundary.py`
   → #101 の時刻より前の最後の seq を境界候補とし、それより後の合意（`purpose.agreed`・`member.joined`・`intent.participant.joined`・`intent.completed` のうち `discussion_hash` を持つもの）を**総当たりで再計算**して、**不一致の一覧**（`talk_id`・保存値・再計算値・投稿数）を出す。
2. **不一致 0 件** → 出力の `boundary_seq` を採用する。
   **1 件以上** → **#113 のデプロイ完了時刻**（マージ時刻ではない）で `python scripts/audit_legacy_boundary.py --boundary-at <ISO8601Z>` を再実行し、**0 件を確認**してからその `boundary_seq` を採用する（例外を増やさず境界を後ろにずらす）。
3. 採用した値を Render の環境変数 `POX_LEGACY_BOUNDARY_SEQ` に設定し、下の表に記録する。

| 項目 | 値 |
|---|---|
| 採用した境界 | **#101**（`2026-09-23T08:49:23.000Z`） |
| 境界 seq | **24**（`POX_LEGACY_BOUNDARY_SEQ = 24` を本番に設定済み・起動確認済み） |
| 確認した合意 | 3 件（未解決 0 件） |
| 不一致件数 | **0 件**（#113 への引き上げは不要） |
| 確認日時 | 2026-09-28（`GET /ledger/audit/legacy-boundary` で実施。運用からの報告） |

**以後この値は変えない。**

- 現行 main で合意済みトークへの追記が不可であることは `tests/test_redaction.py::test_t045b_agreed_talk_rejects_append`（ほか `test_t031`・`test_t076`）で確認している。

## 台帳のイベント型一覧（2026-09-26 時点）

| 型 | 状態 |
|---|---|
| `subject.created` / `member.joined` / `member.left` | 使用中 |
| `purpose.agreed` / `intent.launched` / `intent.participant.joined` / `intent.completed` | 使用中（指示書41 §4 で固定） |
| `intent.proposed` / `intent.agreed` / `intent.cancelled` | 凍結（新規に書かない・読み取りのみ） |
| `profile.structured` / `visibility.changed` / `necessity.published` / `necessity.retired` | 使用中。**指示書57（①v5）で任意キーを追加**（型は変えない）: `profile.structured.canon_version`（"p2"。無い＝c1）／`necessity.published.purpose_id`・`offer_hash`（目的ごとの必要像・与え像のピン留め。ハッシュ規則 c3 は `necessities.canon_version`）。**指示書61 で版を上げた**（遡及しない）: 宣言は **p3**（p2 ＋ `意志_なぜ`・`経験`）、必要像は **c4**（数値を `{gate_s, gate_u}` に。alpha・beta を外す）。p2・c3 で書いたものはその版の規則で検証する（`necessities.verify_content_hash`・`profile_content_hash(input, "p2")`）。`necessity.published.generator` はモデルの系統（例 `claude-opus`）、`generator_tag` は `<_meta.source>/<系統>`（例 `v5r3-A/claude-opus`。source が無ければ `v5r2`）。どちらも内容ハッシュの対象ではない |
| `connection.established` / `connection.closed` / `anchor.published` | 使用中 |
| `redaction.recorded` | 使用中（45B で新設） |
| `intent.participant.left` | 使用中（指示書51 で新設）。プロジェクトからの離脱。`member.left`（コミュニティ所属の離脱）とは目的が別 |
| `content.removed` | **予約済み・不採用**。指示書17 §5-3 で定義されたが実装されなかった。**実装しない**。削除の記録は `redaction.recorded` が担う（同じ目的の型を 2 つ置かない）。指示書17 の本文が出てきた場合も再利用とはせず、「17 §5-3 の要求を `redaction.recorded` が満たしているか」の確認として扱う |

**指示書17 §5-3 の `content.removed` は実装しない。削除の記録は `redaction.recorded` が担う。**
