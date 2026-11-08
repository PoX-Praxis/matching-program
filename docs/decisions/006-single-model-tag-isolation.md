# 006 埋め込みのモデルは 1 つにし、タグで隔離する

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: PR #2（model_tag の隔離）・PR #3（nomic の採用）・PR #120（指示書55-2 B-1-4）・PR #147（指示書65B-1）・`docs/matching.md` §1
- 決めた人: 未確認

---

## 決めたこと

- 埋め込みのモデルは 1 つにする（いまは `nomic-emb-v2`・768 次元）
- ベクトルは model_tag をつけて保存し、違うタグどうしを比べない
- stub のタグは常に `stub-d<次元>` にし、実モデルのタグと混ざらないようにする
- 現行のタグのベクトルを持たない人は、その人だけを照合から外す

## 理由

- 尺度の違うベクトルどうしの比較は、意味をなさないため
- 1 人の未整備で全員の照合を止めないため（指示書65B-1）

## 捨てた選択肢と理由

- **Qwen3-Embedding・EmbeddingGemma:** 実験ブランチ（`exp/embedding-model-eval`）で比べ、nomic を採った（`docs/production_design_notes.md`）
- **未整備の人がいたら全員の照合を止める旧い動作:** 指示書65B-1 で改めた

## 影響範囲

- `src/embedding_config.py`・`src/db_v4.py`（`missing_tag_ids`）・`src/necessities.py`・`app.py`（`/v4/match`）・`nomic_server/`
- **既定値が古いまま:** `src/embedding_config.py` の既定値は、いまも Qwen3・1024 次元（`docs/status-report.md` §5-3）

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
