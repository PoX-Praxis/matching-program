"""指示書12改訂: user_snapshots の保存・churn・schema_version・伏せトグル（SQLite）。"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from snapshots import (save_snapshot, latest_snapshot_id, get_snapshots,
                       set_snapshot_hidden)


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_save_and_get_full_content():
    db = _db()
    sid = save_snapshot("u1", will_text="w1", state={"state_have": "h"},
                        supporting={"生テキスト": ["x"]},
                        necessity={"necessity_text": "n", "evidence_span": "e",
                                   "gate_s": 0.6, "src_input_hash": "H1"},
                        src_input_hash="H1", schema_version="v4.3", db_path=db)
    assert sid
    s = get_snapshots("u1", db_path=db)[0]
    assert s["will_text"] == "w1" and s["state"]["state_have"] == "h"
    assert s["schema_version"] == "v4.3"            # 記録時のスキーマ版
    assert s["vulnerable_hidden"] is False          # 既定は非伏せ
    assert s["necessity"]["evidence_span"] == "e" and s["necessity"]["gate_s"] == 0.6
    assert latest_snapshot_id("u1", db_path=db) == sid


def test_churn_same_hash_skipped():
    db = _db()
    save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                  src_input_hash="H1", db_path=db)
    dup = save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                        src_input_hash="H1", db_path=db)
    assert dup is None and len(get_snapshots("u1", db_path=db)) == 1


def test_different_hash_adds_point():
    db = _db()
    save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                  src_input_hash="H1", db_path=db)
    assert save_snapshot("u1", will_text="w2", state={}, supporting={}, necessity={},
                         src_input_hash="H2", db_path=db)
    assert len(get_snapshots("u1", db_path=db)) == 2


def test_set_hidden_owner_only():
    db = _db()
    sid = save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                        src_input_hash="H", db_path=db)
    # 他人は変更できない
    assert set_snapshot_hidden(sid, "someone_else", True, db_path=db) is False
    assert get_snapshots("u1", db_path=db)[0]["vulnerable_hidden"] is False
    # 所有者は伏せ/戻しできる
    assert set_snapshot_hidden(sid, "u1", True, db_path=db) is True
    assert get_snapshots("u1", db_path=db)[0]["vulnerable_hidden"] is True
    assert set_snapshot_hidden(sid, "u1", False, db_path=db) is True
    assert get_snapshots("u1", db_path=db)[0]["vulnerable_hidden"] is False


def test_empty_when_none():
    assert get_snapshots("nobody", db_path=_db()) == []
    assert latest_snapshot_id("nobody", db_path=_db()) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nsnapshots テスト: {len(tests)} 件 全 PASS")
