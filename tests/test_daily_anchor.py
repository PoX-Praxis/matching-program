"""指示書17 §6: 日次 root バッチ run_daily（バックフィル・空の日も空 root・冪等）。"""
import os, sys, tempfile, subprocess
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import anchor
import ledger_events as le
from canon import sha256_hex


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_backfill_is_continuous_no_gaps():
    db = _db()
    # 3日ぶんのイベントを別々の日付で（at を直接持つ event を作る代わりに、run_daily の
    # 連続性だけ見るため today を指定してレンジを作る）
    le.append_event("u1", "subject.created", {"subject_id": "u1", "kind": "individual"}, db_path=db)
    # 最古イベント日を today として run_daily → 1日ぶん
    d0 = le.get_events(db_path=db)[0]["at"][:10]
    out = anchor.run_daily(db_path=db, up_to=d0)
    assert out["count"] == 1
    # 2日後まで進める → 欠けた中日も空 root で埋まる（連続）
    from datetime import date, timedelta
    d2 = (date.fromisoformat(d0) + timedelta(days=2)).isoformat()
    out2 = anchor.run_daily(db_path=db, up_to=d2)
    dates = [a["date"] for a in _all_anchor_dates(db)]
    d1 = (date.fromisoformat(d0) + timedelta(days=1)).isoformat()
    assert dates == [d0, d1, d2]                       # gap 無し
    # 中日はイベントゼロ → 空 root
    v = anchor.verify_date(d1, db_path=db)
    assert v["match"] is True
    assert anchor.get_anchor(d1, db_path=db)["root"] == sha256_hex(b"")


def _all_anchor_dates(db):
    from db_connect import get_connection
    with get_connection(db) as con:
        rows = con.execute("SELECT date FROM anchors ORDER BY date ASC").fetchall()
    return [{"date": r[0]} for r in rows]


def test_run_daily_idempotent():
    db = _db()
    anchor.run_daily(db_path=db, up_to="2026-03-10")
    before = len(_all_anchor_dates(db))
    anchor.run_daily(db_path=db, up_to="2026-03-10")    # 同じ up_to で再実行
    assert len(_all_anchor_dates(db)) == before          # 増えない
    assert le.verify_chain(db_path=db)["ok"] is True


def test_empty_history_records_up_to_only():
    db = _db()
    out = anchor.run_daily(db_path=db, up_to="2026-05-01")   # イベントもアンカーも無い
    assert out["count"] == 1
    assert anchor.get_anchor("2026-05-01", db_path=db)["root"] == sha256_hex(b"")


def test_default_up_to_is_yesterday_not_today():
    # 当日は含めない（同日イベントで root が後から変わるのを防ぐ）
    db = _db()
    anchor.run_daily(db_path=db)
    assert anchor.get_anchor(anchor._today_utc(), db_path=db) is None       # 今日は未アンカー
    assert anchor.get_anchor(anchor._yesterday_utc(), db_path=db) is not None  # 昨日は確定


def test_runner_script_executes():
    db = _db()
    env = dict(os.environ, POX_DB=db)
    env.pop("DATABASE_URL", None)                         # sqlite で実行
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "daily_anchor.py")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "[daily_anchor]" in r.stdout
    assert anchor.get_anchor(anchor._yesterday_utc(), db_path=db) is not None   # 前日を確定


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\ndaily_anchor テスト: {len(tests)} 件 全 PASS")
