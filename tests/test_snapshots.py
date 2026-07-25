"""指示書12 フェーズ1: user_snapshots の保存・churn 防止（SQLite）。"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from snapshots import save_snapshot, latest_snapshot_id, get_snapshots


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_save_and_get_full_content():
    db = _db()
    sid = save_snapshot("u1", will_text="w1", state={"state_have": "h"},
                        supporting={"生テキスト": ["x"]},
                        necessity={"necessity_text": "n", "evidence_span": "e",
                                   "gate_s": 0.6, "src_input_hash": "H1"},
                        src_input_hash="H1", db_path=db)
    assert sid
    snaps = get_snapshots("u1", db_path=db)
    assert len(snaps) == 1
    s = snaps[0]
    assert s["will_text"] == "w1" and s["state"]["state_have"] == "h"
    # 全保存: necessity は数値・evidence_span も含む（絞りは表示側の責務）
    assert s["necessity"]["necessity_text"] == "n" and s["necessity"]["evidence_span"] == "e"
    assert s["necessity"]["gate_s"] == 0.6
    assert latest_snapshot_id("u1", db_path=db) == sid


def test_churn_same_hash_skipped():
    db = _db()
    save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                  src_input_hash="H1", db_path=db)
    dup = save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                        src_input_hash="H1", db_path=db)
    assert dup is None
    assert len(get_snapshots("u1", db_path=db)) == 1   # 同一内容再登録は増えない


def test_different_hash_adds_point():
    db = _db()
    save_snapshot("u1", will_text="w", state={}, supporting={}, necessity={},
                  src_input_hash="H1", db_path=db)
    s2 = save_snapshot("u1", will_text="w2", state={}, supporting={}, necessity={},
                       src_input_hash="H2", db_path=db)
    assert s2 and len(get_snapshots("u1", db_path=db)) == 2


def test_empty_when_none():
    assert get_snapshots("nobody", db_path=_db()) == []
    assert latest_snapshot_id("nobody", db_path=_db()) is None


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nsnapshots テスト: {len(tests)} 件 全 PASS")
