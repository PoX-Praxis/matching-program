# 014 安全に関わる設定が無ければ起動を止める

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: 指示書26（ソルト）・PR #114（指示書45C §1-1）・PR #120（指示書55-2 B-1）・指示書55-3 §3-1
- 決めた人: 未確認

---

## 決めたこと

- 安全に関わる設定が無いときや食い違うときは、既定値に倒さず起動を止める。対象は次のとおり
  - `POX_EMAIL_SALT`
  - `POX_LEGACY_BOUNDARY_SEQ`
  - 埋め込みの backend とタグの食い違い
  - 本番での stub・Render での `POX_TEST_ALLOW_STUB`
- 開発時（`POX_DEBUG=1`）だけ、既定値を許す

## 理由

- 「既定値にこっそり倒れる」事故を防ぐため（fail-fast）

## 捨てた選択肢と理由

- **既定値へのフォールバック:** 採らない。たとえば `POX_LEGACY_BOUNDARY_SEQ` は、未設定のとき「legacy なし」にも「全件 legacy」にも倒さない（`docs/ledger_limits.md`）

## 影響範囲

- `app.py`（起動時のガード）・`src/auth.py`・`src/redaction.py`・`src/embedding_config.py`・`render.yaml`

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
