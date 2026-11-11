# 011 追記専用の台帳と、日次のアンカー

- 日付: 2026-11-08（`docs/status-report.md` §7 から起こした日。決めた日は根拠の PR を見る）
- 根拠: PR #27〜#29（指示書17）・PR #42（指示書18）・PR #44・#45（指示書20）・PR #113〜#115（指示書45B・45C）・`docs/migration_v2_ledger.md`
- 決めた人: 未確認

---

## 決めたこと

- 接続・合意・削除などの事実は、追記専用の台帳（`ledger_events`）に書く
- 各イベントは seq と prev_hash で前のイベントと鎖にし、正準化して保存する
- 毎日の Merkle root（アンカー）を、外の 2 系統（GitHub Actions・cron-job.org）から刻む。止まったら検知する

## 理由

- 記録を後から改ざんできないようにするため
- 1 系統が止まっても刻めるようにするため（`docs/migration_v2_ledger.md`）

## 捨てた選択肢と理由

- **接続の行を読んで書き換える方式（RMW）:** 指示書17 段階3 で解体した（PR #28）
- **Render の cron:** 有料なので使わない（`docs/migration_v2_ledger.md`）。ただし `render.yaml` に宣言が残る（`docs/status-report.md` §5-3）

## 影響範囲

- `src/ledger_events.py`・`src/anchor.py`・各台帳（`subject_ledger`・`member_ledger`・`intent_ledger`・`governance`・`redaction`）・`.github/workflows/daily_anchor.yml`・`.github/workflows/anchor_monitor.yml`・`scripts/daily_anchor.py`

## 状態

**下書き（要確認）**
- 2026-11-08 に `docs/status-report.md` §7 の行から起こした
- 判断する人の確認を受けたら「確定」にする
