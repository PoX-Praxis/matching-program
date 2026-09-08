"""指示書17 §5・§5-8: 意志形成（intent）とガバナンス導出。AI/Nomic 非依存・決定論的。

- proposed→agreed→completed のループ。本文はハッシュのみ・n/basis_seq/ruleset_version 固定
- ブートストラップ: 初回 completed まで創設者のみ議決権（§1-6）
- is_agreed は提起時 ruleset（r1=全員一致）で判定・過大判定しない
- completed は1回（§5-6）・未合意では完了不可・cancel
- ブートストラップ超（完了済み ctx）の議決権/合意は未算出（None・§2-1）
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import community as C
import intent_ledger as IL
import ledger_events as le


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _community(db):
    c = C.create_community("founder", "n", "d", db_path=db)
    return c["id"]


def test_propose_records_hashes_and_basis():
    db = _db()
    ctx = _community(db)
    r = IL.propose_intent(ctx, "founder", body="翻訳基盤v1", declaration="宣言",
                          db_path=db)
    ev = le.get_events(type_="intent.proposed", db_path=db)[0]["payload"]
    assert ev["intent_id"] == r["intent_id"] and ev["ctx"] == ctx and ev["n"] == 1
    assert len(ev["body_hash"]) == 64 and "body" not in ev        # 本文は載らない
    assert ev["basis_seq"] == r["basis_seq"] and ev["ruleset_version"] == "r1"


def test_bootstrap_founder_only_voting_and_agree_complete():
    db = _db()
    ctx = _community(db)
    C.request_join(ctx, "bob", db_path=db)
    C.approve_member(ctx, "bob", db_path=db)               # bob も active だが議決権は無い
    r = IL.propose_intent(ctx, "founder", body="x", db_path=db)
    iid = r["intent_id"]
    assert IL.voting_power(ctx, "founder", r["basis_seq"], db_path=db) == 1.0
    assert IL.voting_power(ctx, "bob", r["basis_seq"], db_path=db) == 0.0

    # bob が合意しても議決権が無いので可決しない
    IL.agree_intent(iid, "bob", db_path=db)
    assert IL.is_agreed(iid, db_path=db) is False
    # founder が合意で全員一致（議決権者＝創設者のみ）
    IL.agree_intent(iid, "founder", db_path=db)
    assert IL.is_agreed(iid, db_path=db) is True
    assert IL.get_intent(iid, db_path=db)["status"] == "agreed"

    # 完了（実績）
    done = IL.complete_intent(iid, "founder", result="v1", db_path=db)
    assert done["status"] == "completed"
    assert IL.get_intent(iid, db_path=db)["status"] == "completed"
    assert le.verify_chain(db_path=db)["ok"] is True


def test_complete_requires_agreement_and_is_once():
    db = _db()
    ctx = _community(db)
    r = IL.propose_intent(ctx, "founder", body="y", db_path=db)
    iid = r["intent_id"]
    assert IL.complete_intent(iid, "founder", db_path=db)["error"] == "not_agreed"
    IL.agree_intent(iid, "founder", db_path=db)
    assert IL.complete_intent(iid, "founder", db_path=db)["status"] == "completed"
    # 2回目は skip（§5-6）
    assert IL.complete_intent(iid, "founder", db_path=db)["skipped"] is True


def test_agree_is_idempotent_per_subject():
    db = _db()
    ctx = _community(db)
    r = IL.propose_intent(ctx, "founder", db_path=db)
    IL.agree_intent(r["intent_id"], "founder", db_path=db)
    dup = IL.agree_intent(r["intent_id"], "founder", db_path=db)
    assert dup["skipped"] is True
    assert len([e for e in le.get_events(type_="intent.agreed", db_path=db)]) == 1


def test_cancel_blocks_complete():
    db = _db()
    ctx = _community(db)
    r = IL.propose_intent(ctx, "founder", db_path=db)
    IL.cancel_intent(r["intent_id"], "founder", reason="やめた", db_path=db)
    assert IL.get_intent(r["intent_id"], db_path=db)["status"] == "cancelled"
    IL.agree_intent(r["intent_id"], "founder", db_path=db)
    assert IL.complete_intent(r["intent_id"], "founder", db_path=db)["error"] == "cancelled"


def test_post_bootstrap_governance_is_undetermined():
    db = _db()
    ctx = _community(db)
    # 1本目を完了させてブートストラップを抜ける
    r1 = IL.propose_intent(ctx, "founder", body="a", db_path=db)
    IL.agree_intent(r1["intent_id"], "founder", db_path=db)
    IL.complete_intent(r1["intent_id"], "founder", result="done", db_path=db)
    # 2本目: 完了 intent が存在する時点の議決権/合意は未算出（発行式未確定・§2-1）
    r2 = IL.propose_intent(ctx, "founder", body="b", db_path=db)
    assert IL.voting_power(ctx, "founder", r2["basis_seq"], db_path=db) is None
    assert IL.is_agreed(r2["intent_id"], db_path=db) is None


def test_list_and_participant():
    db = _db()
    ctx = _community(db)
    r = IL.propose_intent(ctx, "founder", db_path=db)
    IL.join_participant(r["intent_id"], "bob", introduced_by="founder",
                        approved_by=["founder"], db_path=db)
    pj = le.get_events(type_="intent.participant.joined", db_path=db)[0]["payload"]
    assert pj["participant"] == "bob" and pj["introduced_by"] == "founder"
    assert pj["intent_snapshot_hash"]                       # 提起時点への参照
    assert [i["intent_id"] for i in IL.list_intents(ctx, db_path=db)] == [r["intent_id"]]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nintent_ledger テスト: {len(tests)} 件 全 PASS")
