"""指示書17 §5: コミュニティ構成イベント（member.joined / member.left）。Nomic 非依存。

- create_community: 創設者を member.joined（before=[]・after=[founder]）
- request_join は台帳に載せない（申請=通常DB・§1-3）
- approve_member: active になった瞬間だけ member.joined（approved_by=[founder]）。再承認は無イベント
- leave_community: member.left。追い出しは無し
- active_members_from_events でイベント列から現在メンバーを導出できる
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import community as C
import member_ledger as ML
import ledger_events as le


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_create_emits_founder_joined():
    db = _db()
    c = C.create_community("founder", "名前", "説明", db_path=db)
    evs = le.get_events(type_="member.joined", db_path=db)
    assert len(evs) == 1
    p = evs[0]["payload"]
    assert p["ctx"] == c["id"] and p["subject_id"] == "founder"
    assert p["approved_by"] == ["founder"]
    assert p["members_before_hash"] == ML.members_hash([])
    assert p["members_after_hash"] == ML.members_hash(["founder"])


def test_request_join_not_in_ledger_then_approve_emits():
    db = _db()
    c = C.create_community("f", "n", "d", db_path=db)
    C.request_join(c["id"], "bob", db_path=db)
    assert len(le.get_events(type_="member.joined", db_path=db)) == 1   # 申請は載らない
    C.approve_member(c["id"], "bob", db_path=db)
    evs = le.get_events(type_="member.joined", db_path=db)
    assert len(evs) == 2
    p = evs[1]["payload"]
    assert p["subject_id"] == "bob" and p["approved_by"] == ["f"]
    assert p["members_after_hash"] == ML.members_hash(["f", "bob"])


def test_reapprove_is_idempotent():
    db = _db()
    c = C.create_community("f", "n", "d", db_path=db)
    C.request_join(c["id"], "bob", db_path=db)
    C.approve_member(c["id"], "bob", db_path=db)
    C.approve_member(c["id"], "bob", db_path=db)                        # 再承認
    assert len(le.get_events(type_="member.joined", db_path=db)) == 2   # 増えない
    assert le.verify_chain(db_path=db)["ok"] is True


def test_leave_emits_member_left_and_derivation():
    db = _db()
    c = C.create_community("f", "n", "d", db_path=db)
    C.request_join(c["id"], "bob", db_path=db)
    C.approve_member(c["id"], "bob", db_path=db)
    assert ML.active_members_from_events(c["id"], db_path=db) == {"f", "bob"}
    r = C.leave_community(c["id"], "bob", db_path=db)
    assert r["status"] == "left"
    assert le.get_events(type_="member.left", db_path=db)[0]["payload"]["subject_id"] == "bob"
    assert ML.active_members_from_events(c["id"], db_path=db) == {"f"}   # 導出が追従
    # 非メンバーの離脱は no-op
    assert C.leave_community(c["id"], "nobody", db_path=db)["status"] == "not_member"
    assert le.verify_chain(db_path=db)["ok"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nmember_ledger テスト: {len(tests)} 件 全 PASS")
