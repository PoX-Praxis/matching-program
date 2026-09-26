# 台帳の既知の限界・不変条件・確認済み事項（指示書45 A-4・A-5・45B）

更新: 2026-09-26

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
- **参照の誤りの記録**: 指示書45 §2-2 の「43 §3-3」は用語と文言の節で、この範囲を定義していない。範囲はこれまで**コードにのみ**存在し、本節で初めて文書に固定した。
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
- **本文を伏せる操作と申立ての受付経路はまだ無い**（45B §4）。当面は運用者が通常DBで発言の本文を伏せ字（例: `［削除済み］`）に置き換え（**行は消さない**。順序と投稿者を保つ）、その後 `redaction.record_redaction(...)` を呼んで記録する。`decided_by` には運用者のアカウントを入れる。

## legacy の合意（#101 以前・45B §3）

- **境界**: 合意済みトークへの追記を止めた **PR #101（`c92757f`）が main に入った時刻 `2026-09-23T08:49:23Z`**。これより前に記録された合意は、合意後の追記が止められていなかったため、再計算して一致しなくても**改ざんと判定しない**（台帳の保存値を正とし、`legacy_unverified` と返す）。legacy の連鎖も台帳の保存値から始める。
- 判定は `redaction.LEGACY_BOUNDARY_AT`（時刻）で行い、**継承の印は台帳に書かない**。
- **境界の seq**: 本番台帳の seq はこのセッションから参照できないため未確定。運用側で次を実行して値を確定し、`redaction.LEGACY_BOUNDARY_SEQ` に設定する（設定後は seq で判定する）:
  `SELECT MAX(seq) FROM ledger_events WHERE at < '2026-09-23T08:49:23.000Z';`
  （Render の自動デプロイは merge の数分後に反映されるため、厳密には「デプロイ完了時点」が境界。確定時にデプロイ時刻で置き換えてよい。）
- 現行 main で合意済みトークへの追記が不可であることは `tests/test_redaction.py::test_t045b_agreed_talk_rejects_append`（ほか `test_t031`・`test_t076`）で確認している。

## 台帳のイベント型一覧（2026-09-26 時点）

| 型 | 状態 |
|---|---|
| `subject.created` / `member.joined` / `member.left` | 使用中 |
| `purpose.agreed` / `intent.launched` / `intent.participant.joined` / `intent.completed` | 使用中（指示書41 §4 で固定） |
| `intent.proposed` / `intent.agreed` / `intent.cancelled` | 凍結（新規に書かない・読み取りのみ） |
| `profile.structured` / `visibility.changed` / `necessity.published` / `necessity.retired` | 使用中 |
| `connection.established` / `connection.closed` / `anchor.published` | 使用中 |
| `redaction.recorded` | 使用中（45B で新設） |
| `content.removed` | **予約済み・不採用**。指示書17 §5-3 で定義されたが実装されなかった。**実装しない**。削除の記録は `redaction.recorded` が担う（同じ目的の型を 2 つ置かない）。指示書17 の本文が出てきた場合も再利用とはせず、「17 §5-3 の要求を `redaction.recorded` が満たしているか」の確認として扱う |

**指示書17 §5-3 の `content.removed` は実装しない。削除の記録は `redaction.recorded` が担う。**
