# 台帳の既知の限界と確認済み事項（指示書45 A-4・A-5）

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
