# 003 本人の層と公開の層を分ける

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: 2026-06-14 のコミット「two-layer profile system」（PR 以前）・`CLAUDE.md` のセキュリティ制約
- 決めた人: 未確認

---

## 決めたこと

- 本人だけが見る層（seeker）と、公開の層（profile_view）を分ける
- `GET /profile/<id>` は `get_profile_view()` だけを呼び、`get_seeker()` を呼ばない
- `seeker` の列は、viewer の API と公開 API に出さない

## 理由

- 生テキスト・根拠を第三者に出さないため（`CLAUDE.md` のセキュリティ制約）

## 捨てた選択肢と理由

- 記録なし（要確認）

## 影響範囲

- `src/profile_view.py`・`src/db.py`・`app.py`（`/api/profile/<id>`・`/profile/<id>`・`/u/<handle>`）・`templates/profile.html`・`templates/_profile_view.html`

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
