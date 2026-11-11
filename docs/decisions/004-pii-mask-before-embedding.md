# 004 埋め込みの前に PII をマスクする

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: PR #2（Step 2: 構造的な PII のマスク）・v4 仕様の I 章
- 決めた人: 未確認

---

## 決めたこと

- 埋め込み・LLM・保存の前に、構造的な PII を型つきのトークンに置き換える
  - 対象はメール・電話・URL・郵便番号・カード番号・@ハンドル
- 意味の上の PII（人名など）は、まだ扱わない

## 理由

- 生の文を埋め込みのサーバーに送らないため（v4 仕様の I 章「raw を直接埋め込まない」）

## 捨てた選択肢と理由

- **意味の上の PII の検出:** 先送りした（Step 2 のコミット「semantic PII deferred」）

## 影響範囲

- `src/pii_redaction.py`・`src/embedding_service.py`・`src/necessities.py`・`src/v5.py`・`src/db_v4.py`
- 計測のスクリプト（`scripts/element_probe*.py`）も、マスクしてから送る

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
