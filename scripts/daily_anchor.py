#!/usr/bin/env python3
"""
PoX 日次 root バッチ（指示書17 §6）。1日1回、その日までのアンカーを追記する。

Render Cron（render.yaml の pox-anchor）や任意のスケジューラから:
    python scripts/daily_anchor.py

- DATABASE_URL があれば Postgres（本番）、無ければ SQLite（POX_DB / 既定 pox.db）。
- schema.init() を先に呼びテーブル存在を保証（Web が未起動でも動く・冪等）。
- run_daily は最後のアンカーの翌日〜今日を連続記録（欠けた日はバックフィル・gap 無し）。
- Nomic・認証には一切依存しない。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def main() -> int:
    db = os.environ.get("POX_DB", "pox.db")
    import schema
    schema.init(db)                      # 冪等: anchors / ledger_events を保証
    import anchor
    out = anchor.run_daily(db_path=db)
    print(f"[daily_anchor] {out['from']}..{out['to']} count={out['count']}")
    for r in out["results"]:
        print(f"  {r.get('date')} root={(r.get('root') or '')[:12]} "
              f"events={r.get('event_count')} skipped={r.get('skipped', False)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
