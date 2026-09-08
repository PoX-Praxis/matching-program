"""指示書20: /ledger/anchor/status（停止検知）と二重起動の安全性（§6）。"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import anchor
import ledger_events as le
import app as appmod
from datetime import date, timedelta


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── anchor_status / days_behind ──────────────────────────────────────────────
def test_status_none_when_no_anchors():
    st = anchor.anchor_status(db_path=_db())
    assert st == {"last_anchor_date": None, "last_anchor_seq": None, "days_behind": None}


def test_days_behind_1_when_anchored_yesterday():
    db = _db()
    anchor.publish_anchor(anchor._yesterday_utc(), db_path=db)
    st = anchor.anchor_status(db_path=db)
    assert st["days_behind"] == 1            # 正常（前日まで刻んでいる）
    assert st["last_anchor_date"] == anchor._yesterday_utc()


def test_days_behind_grows_when_stale():
    db = _db()
    old = (date.fromisoformat(anchor._yesterday_utc()) - timedelta(days=5)).isoformat()
    anchor.publish_anchor(old, db_path=db)
    assert anchor.anchor_status(db_path=db)["days_behind"] == 6   # today - (yesterday-5)


# ── エンドポイント（strict の 200/503） ──────────────────────────────────────
def test_endpoint_status_and_strict():
    db = _db()
    appmod.DB = db
    c = appmod.app.test_client()
    # アンカー皆無: strict でも 200（まだ走っていないだけ・警報にしない）
    assert c.get("/ledger/anchor/status?strict=1").status_code == 200
    # 前日まで刻めば days_behind=1 → strict 200
    anchor.publish_anchor(anchor._yesterday_utc(), db_path=db)
    assert c.get("/ledger/anchor/status").status_code == 200
    assert c.get("/ledger/anchor/status?strict=1").status_code == 200
    # 停止（古い日付だけ）→ strict 503、非 strict は 200
    db2 = _db(); appmod.DB = db2
    old = (date.fromisoformat(anchor._yesterday_utc()) - timedelta(days=3)).isoformat()
    anchor.publish_anchor(old, db_path=db2)
    assert c.get("/ledger/anchor/status").status_code == 200
    r = c.get("/ledger/anchor/status?strict=1")
    assert r.status_code == 503 and r.get_json()["days_behind"] >= 2


# ── §6 二重起動の安全性 ──────────────────────────────────────────────────────
def test_double_invocation_is_idempotent():
    db = _db()
    appmod.DB = db
    os.environ["POX_ANCHOR_TOKEN"] = "sekret"
    os.environ.pop("POX_DEBUG", None)
    c = appmod.app.test_client()
    h = {"X-Anchor-Token": "sekret"}
    # GitHub Actions と cron-job.org が同じ日に二重に叩く状況を模擬
    r1 = c.post("/ledger/anchor", json={}, headers=h).get_json()
    r2 = c.post("/ledger/anchor", json={}, headers=h).get_json()
    # 1回目で前日まで刻まれ、2回目は同じ範囲＝すべて skip（二重アンカーは起きない）
    def _dates(db_):
        from db_connect import get_connection
        with get_connection(db_) as con:
            return [x[0] for x in con.execute("SELECT date FROM anchors ORDER BY date").fetchall()]
    after1 = _dates(db)
    assert after1, "1回目で少なくとも前日はアンカーされる"
    assert _dates(db) == after1                    # 2回目で件数は増えない
    assert all(x.get("skipped") for x in r2["results"]) or r2["count"] == 0
    assert le.verify_chain(db_path=db)["ok"] is True   # 鎖は健全
    os.environ.pop("POX_ANCHOR_TOKEN", None)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nanchor status テスト: {len(tests)} 件 全 PASS")
