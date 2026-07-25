"""
指示書08 フェーズ1: update_seeker_core の changed_core 判定（SQLite）。

意志/現状4スロットの実変化のみ True。空白差分・v3互換フィールドのみは False。
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from db import save_profile, update_seeker_core


def _tmpdb():
    d = tempfile.mkdtemp()
    return os.path.join(d, "t.db")


def _seed(db):
    save_profile("u1", {
        "_meta": {"schema_version": "v4"}, "意志": "元の意志",
        "現状": {"持っているもの": "A", "できること_型": "B",
                 "縛られているもの": "C", "未分類": ""},
        "supporting_material": {"要約文": "x"},
    }, db_path=db)


def test_changed_when_will_edited():
    db = _tmpdb(); _seed(db); out = {}
    assert update_seeker_core("u1", {"意志": "新しい意志"}, db_path=db, out=out) is True
    assert out["changed_core"] is True


def test_changed_when_state_edited():
    db = _tmpdb(); _seed(db); out = {}
    update_seeker_core("u1", {"state_have": "新A"}, db_path=db, out=out)
    assert out["changed_core"] is True


def test_not_changed_whitespace_only():
    db = _tmpdb(); _seed(db)
    update_seeker_core("u1", {"意志": "新しい意志"}, db_path=db)  # 先に変更
    out = {}
    update_seeker_core("u1", {"意志": "  新しい意志  "}, db_path=db, out=out)  # 空白のみ差分
    assert out["changed_core"] is False


def test_not_changed_v3compat_field_only():
    db = _tmpdb(); _seed(db); out = {}
    update_seeker_core("u1", {"求めている": "foo"}, db_path=db, out=out)
    assert out["changed_core"] is False


def test_out_optional_backward_compat():
    db = _tmpdb(); _seed(db)
    # out を渡さなくても従来どおり bool を返す
    assert update_seeker_core("u1", {"意志": "z"}, db_path=db) is True
    assert update_seeker_core("missing", {"意志": "z"}, db_path=db) is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nchanged_core テスト: {len(tests)} 件 全 PASS")
