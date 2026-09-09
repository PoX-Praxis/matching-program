"""指示書23 §1-2/§3-1: 宣言の確定（agreed で profile.structured / necessity.published）。

- policy（全体方針）: agreed で ctx の profile.structured が書かれる
- recruit（目的別募集）: agreed で owner_ref=intent_id の necessity.published（self_declared）
- 宣言なし/自由記述: 何も確定しない
- 完了で結果本文が DB に保存され、実績ビューに出る
- ブートストラップ: 初回 completed 前は創設者のみ議決権
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import community as C
import intent_ledger as IL
import intent_content as IC
import ledger_events as le
import necessities as NEC


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _ctx(db):
    return C.create_community("founder", "n", "d", db_path=db)["id"]


def test_policy_declaration_writes_profile_structured_on_agree():
    db = _db(); ctx = _ctx(db)
    decl = {"kind": "policy", "will_text": "現場を実装に翻訳する", "state_have": "知識"}
    r = IL.propose_intent(ctx, "founder", body="全体方針を定める", declaration=decl, db_path=db)
    iid = r["intent_id"]
    # 提起時点では profile.structured は無い（合意前）
    assert not [e for e in le.get_events(type_="profile.structured", db_path=db)
                if e["payload"]["subject_id"] == ctx]
    res = IL.agree_intent(iid, "founder", db_path=db)
    assert res["agreed"] is True
    assert res["declaration_confirmed"]["kind"] == "policy"
    ps = [e for e in le.get_events(type_="profile.structured", db_path=db)
          if e["payload"]["subject_id"] == ctx]
    assert len(ps) == 1
    # 最新宣言が読める（本文は DB）
    d = IL.latest_policy_declaration(ctx, db_path=db)
    assert d["will_text"] == "現場を実装に翻訳する" and d["state_have"] == "知識"


def test_recruit_declaration_writes_self_declared_necessity_with_clamped_gate_u():
    db = _db(); ctx = _ctx(db)
    decl = {"kind": "recruit", "will_text": "翻訳基盤を作る",
            "necessity_text": "現場と実装の間を埋められる人"}
    r = IL.propose_intent(ctx, "founder", body="人を募る", declaration=decl, db_path=db)
    iid = r["intent_id"]
    IL.agree_intent(iid, "founder", db_path=db)
    pub = [e for e in le.get_events(type_="necessity.published", db_path=db)
           if e["payload"]["owner_ref"] == iid]
    assert len(pub) == 1 and pub[0]["payload"]["owner_kind"] == "intent"
    assert pub[0]["payload"]["origin"] == "self_declared"
    # self_declared は gate_u を 0.6 以上にクランプ（§1-5・禁則で外してはならない）
    rows = NEC.get_necessities(iid, db_path=db)
    assert rows and rows[0]["gate_u"] >= 0.6


def test_no_declaration_confirms_nothing():
    db = _db(); ctx = _ctx(db)
    r = IL.propose_intent(ctx, "founder", body="ただ何かをする", declaration="", db_path=db)
    IL.agree_intent(r["intent_id"], "founder", db_path=db)
    assert not [e for e in le.get_events(type_="profile.structured", db_path=db)
                if e["payload"]["subject_id"] == ctx]
    assert not le.get_events(type_="necessity.published", db_path=db)


def test_free_text_declaration_confirms_nothing():
    db = _db(); ctx = _ctx(db)
    r = IL.propose_intent(ctx, "founder", declaration="自由記述の宣言", db_path=db)
    res = IL.agree_intent(r["intent_id"], "founder", db_path=db)
    assert res["declaration_confirmed"] is None


def test_confirmation_is_idempotent_across_repeated_agree():
    db = _db(); ctx = _ctx(db)
    decl = {"kind": "policy", "will_text": "方針"}
    r = IL.propose_intent(ctx, "founder", declaration=decl, db_path=db)
    IL.agree_intent(r["intent_id"], "founder", db_path=db)
    IL.agree_intent(r["intent_id"], "founder", db_path=db)  # 重複合意（churn skip）
    ps = [e for e in le.get_events(type_="profile.structured", db_path=db)
          if e["payload"]["subject_id"] == ctx]
    assert len(ps) == 1   # 宣言確定は1回だけ


def test_completion_stores_result_body_and_detail_view():
    db = _db(); ctx = _ctx(db)
    r = IL.propose_intent(ctx, "founder", body="翻訳基盤v1を作る", db_path=db)
    iid = r["intent_id"]
    IL.agree_intent(iid, "founder", db_path=db)
    IL.complete_intent(iid, "founder", result="v1 を公開した", db_path=db)
    det = IL.get_intent_detail(iid, db_path=db)
    assert det["status"] == "completed"
    assert det["body"] == "翻訳基盤v1を作る" and det["result"] == "v1 を公開した"
    assert det["completed_at"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n宣言確定テスト: {len(tests)} 件 全 PASS")
