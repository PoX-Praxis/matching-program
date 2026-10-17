"""指示書65B-5 — 提示の記録と、申し出への要素の記録（テスト 363〜366）。

提示の記録は通常 DB（台帳ではない）。**記録の開始はフラグで閉じる**（POX_PRESENTATION_LOG_OPEN。既定は閉じる）。
表・書き込みの経路・保存期間の設定までを実装する。申し出には、どの要素についての申し出かを記録する。
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import ledger_events as le  # noqa: E402
import presentations as P  # noqa: E402
import v5  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import _match, world2  # noqa: E402,F401


# ── 363 既定で閉じる。閉じている間は何も書かない（照合・開いたか）──────────────────────────────
def test_t363_closed_by_default_writes_nothing(world2, monkeypatch):
    monkeypatch.delenv("POX_PRESENTATION_LOG_OPEN", raising=False)
    assert P.recording_open() is False
    d, _, _ = _match()
    assert d["results"]
    r = _cli("me").post("/api/presentations/opened", json={"run_id": d["match_run_id"], "candidate_id": "c_full"})
    assert r.status_code == 204
    assert P.rows_for(db_path=appmod.DB) == []


# ── 364 開けると、誰に・誰を・目的・要素・向き・判定の版・時刻・見せた／開いたが書かれる（台帳には書かない）──────────
def test_t364_rows_written_when_open(world2, monkeypatch):
    monkeypatch.setenv("POX_PRESENTATION_LOG_OPEN", "1")
    monkeypatch.setenv("POX_LOWER_LAYER_OPEN", "1")
    before = len(le.get_events(db_path=appmod.DB))
    d, g, pids = _match()
    rows = P.rows_for("me", db_path=appmod.DB)
    upper = [r for r in rows if r["layer"] == "upper"]
    lower = [r for r in rows if r["layer"] == "lower"]
    nec = v5.live_necessities_v5("me", db_path=appmod.DB)[0]["necessity_id"]
    assert [(r["counterpart"], r["purpose_id"], r["necessity_ref"], r["direction"]) for r in upper] == \
        [("c_full", pids[0], nec, "a")]
    assert {(r["counterpart"], r["element_idx"]) for r in lower if r["purpose_id"] == pids[0]} == \
        {("c_full", 0), ("c_full", 1), ("c_part", 1)}
    ver = json.loads(rows[0]["judgment_version"])
    assert set(ver) >= {"background", "transform", "upper", "lower", "rules", "embedding", "embedding_revision"}
    assert all(r["run_id"] == d["match_run_id"] and r["shown"] == 1 and r["opened_at"] is None for r in rows)
    _cli("me").post("/api/presentations/opened", json={"run_id": d["match_run_id"], "candidate_id": "c_full"})
    assert all(r["opened_at"] for r in P.rows_for("me", db_path=appmod.DB) if r["counterpart"] == "c_full")
    assert len(le.get_events(db_path=appmod.DB)) == before                     # 台帳には書かない
    for r in rows:
        assert "g" not in r and "score" not in r                                # 数値は持たない


# ── 365 保存期間（設定）を過ぎた行は消える ─────────────────────────────────────────────────
def test_t365_retention(db, monkeypatch):
    monkeypatch.setenv("POX_PRESENTATION_LOG_OPEN", "1")
    monkeypatch.setenv("POX_PRESENTATION_RETENTION_DAYS", "30")
    assert P.retention_days() == 30
    P.record("v", "run_old", [{"counterpart": "c", "direction": "a", "layer": "upper"}], db_path=appmod.DB)
    P.purge(now=datetime.now(timezone.utc) + timedelta(days=31), db_path=appmod.DB)
    assert P.rows_for(db_path=appmod.DB) == []
    monkeypatch.delenv("POX_PRESENTATION_RETENTION_DAYS")
    assert P.retention_days() == P.RETENTION_DAYS_DEFAULT


# ── 366 申し出に、どの要素についての申し出かを記録する（通常 DB）────────────────────────────────
def test_t366_offer_records_element(world2):
    pids = [n["purpose_id"] for n in v5.live_necessities_v5("me", db_path=appmod.DB)]
    nec = v5.live_necessities_v5("me", db_path=appmod.DB)[0]["necessity_id"]
    assert _cli("me").post("/approve", json={"to_id": "c_part", "purpose_id": pids[0], "element_idx": 5}).status_code == 400
    assert _cli("me").post("/approve", json={"to_id": "c_part", "element_idx": 1}).status_code == 400   # 目的なしは不可
    assert _cli("me").post("/approve", json={"to_id": "c_part", "purpose_id": pids[0], "element_idx": 1}).status_code == 200
    import ledger
    with ledger._connect(appmod.DB) as con:
        r = con.execute("SELECT element_ref FROM connection_requests WHERE from_subject='me' AND to_subject='c_part'").fetchone()
    assert r[0] == f"{nec}:1"
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert 'data-element="${i}"' in html and "element_idx: btn.dataset.element" in html
