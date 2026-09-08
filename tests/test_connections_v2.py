"""指示書17 段階3: 接続の RMW 解体・イベント化（connection.established / closed）。

- 片方向承認は connection_requests（通常DB）に置き、台帳に載せない（§1-3）。
- 双方向でのみ connection.established が台帳に載る。closed_at は成立で埋めない（§4-6）。
- 状態はイベント列から導出。vessel_json への書き込みは全廃（§4-5）。
- close_connection で connection.closed。鎖は常に verify_chain で健全。
"""
import os, sys, tempfile, json
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import ledger
import ledger_events as le
from db_connect import get_connection


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_one_sided_approval_is_not_in_ledger():
    db = _db()
    r = ledger.approve("alice", "bob", db_path=db)
    assert r["established"] is False
    # 片側承認では台帳に connection イベントは載らない（成立の前段は通常DB）。
    assert le.get_events(type_="connection.established", db_path=db) == []
    v = ledger.load_all_vessels(db_path=db)[0]
    assert v["is_connected"] is False and v["founder"] == "alice"


def test_mutual_establishes_one_ledger_event_and_closed_at_null():
    db = _db()
    ledger.approve("alice", "bob", db_path=db)
    r = ledger.approve("bob", "alice", db_path=db)
    assert r["established"] is True
    evs = le.get_events(type_="connection.established", db_path=db)
    assert len(evs) == 1                                   # 成立は1件だけ台帳に
    v = ledger.load_all_vessels(db_path=db)[0]
    assert v["is_connected"] is True
    assert v["joins"][0]["established_at"]                 # 立っている
    assert v["joins"][0]["closed_at"] is None             # §4-6: 成立では閉じない
    assert v["joins"][0]["terminal_state"] == "active"
    # 承認集合が両者ぶん導出される
    approvers = {a["from"] for a in v["joins"][0]["approvals"]}
    assert approvers == {"alice", "bob"}


def test_no_vessel_json_write_happens():
    db = _db()
    ledger.approve("alice", "bob", db_path=db)
    ledger.approve("bob", "alice", db_path=db)
    # 新経路は vessels テーブルに1行も書かない（RMW 解体・§4-5）。
    with get_connection(db) as con:
        n = con.execute("SELECT COUNT(*) FROM vessels").fetchone()[0]
    assert n == 0


def test_idempotent_reapprove_no_duplicate_event():
    db = _db()
    ledger.approve("alice", "bob", db_path=db)
    ledger.approve("bob", "alice", db_path=db)
    ledger.approve("bob", "alice", db_path=db)            # 再承認
    assert len(le.get_events(type_="connection.established", db_path=db)) == 1
    assert le.verify_chain(db_path=db)["ok"] is True


def test_close_connection_sets_closed_at_and_ended():
    db = _db()
    ledger.approve("alice", "bob", db_path=db)
    ledger.approve("bob", "alice", db_path=db)
    c = ledger.close_connection("alice", "bob", by="alice", reason="", db_path=db)
    assert c["closed"] is True
    v = ledger.load_all_vessels(db_path=db)[0]
    assert v["joins"][0]["closed_at"] is not None
    assert v["joins"][0]["terminal_state"] != "active"
    assert le.verify_chain(db_path=db)["ok"] is True
    # 理由は payload に事実として残るが、導出 vessel に reason を混ぜない
    assert "reason" not in v["joins"][0]


def test_snapshots_paired_at_establishment():
    db = _db()
    hook = lambda f, j: {f: "sF", j: "sJ"}
    ledger.approve("alice", "bob", db_path=db, establish_hook=hook)
    ledger.approve("bob", "alice", db_path=db, establish_hook=hook)
    v = ledger.load_all_vessels(db_path=db)[0]
    assert v["snapshots"] == {"alice": "sF", "bob": "sJ"}


def test_legacy_vessel_row_still_visible():
    db = _db()
    # 旧 vessels 行（移行前データ）を直接投入 → 読み取りフォールバックで見える
    with get_connection(db) as con:
        con.execute("CREATE TABLE IF NOT EXISTS vessels (vessel_id TEXT PRIMARY KEY, vessel_json TEXT NOT NULL)")
        con.execute("INSERT INTO vessels (vessel_id, vessel_json) VALUES (%s,%s)",
                    ("v_old_pair", json.dumps({"vessel_id": "v_old_pair", "founder": "old",
                                               "is_connected": True, "joins": [{"joiner": "pair"}]})))
    vids = [v["vessel_id"] for v in ledger.load_all_vessels(db_path=db)]
    assert "v_old_pair" in vids


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nconnections v2 テスト: {len(tests)} 件 全 PASS")
