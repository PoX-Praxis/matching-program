# 005 非対称の検索（必要像＝query、相手の文＝passage）

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: PR #3（束2a・nomic の採用）・PR #33（指示書17 §7-4）・PR #128（指示書57）・PR #134（指示書61）
- 決めた人: 未確認

---

## 決めたこと

- 必要像の文は query（`search_query: `）として埋め込む
- 与え像の文と現状は passage（`search_document: `）として埋め込む
- 目的の向かう先どうしの共鳴は symmetric で比べる
- 照合は、必要像の文を起点（query の単位）にする

## 理由

- 「求める側」の文と「自己紹介の側」の文は、役割が違うため
- nomic が推奨する prefix の使い方に合わせた（`src/embedding_config.py` の注記）
- 必要像を照合の起点にする（指示書17 §7-4）

## 捨てた選択肢と理由

- **人ごとの全文ベクトルで比べる v4 の方式（`matcher_v4`）:** v5 の文単位に置き換えた（指示書56・57）。いまは監査ルートにだけ残る

## 影響範囲

- `src/embedding_config.py`（prefix）・`src/embedding_service.py`・`src/v5.py`（`save_sentence_vectors`）・`src/matcher_v5.py`

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
