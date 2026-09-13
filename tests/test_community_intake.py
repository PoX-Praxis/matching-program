"""指示書28 段階3: コミュニティ版①の受理（全体用・目的別用）。

- community_overall 宣言 → 合意で profile.structured + necessity.published(generated・非クランプ)。
- intent_necessity 宣言 → 合意で necessity.published(owner_kind=intent・generated)、profile.structured なし。
- できること_型 "実績なし（型は導出不能）" を受理。
- app マッパー _community_declaration_from_payload が2種を判別。
"""
import os, sys, tempfile, sqlite3
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import ledger_events as le
import intent_ledger as IL
import community as C
import app as appmod


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _ctx(db):
    return C.create_community("founder", "n", "d", db_path=db)["id"]


def _events(db, type_):
    return [e for e in le.get_events(type_=type_, db_path=db)]


def _nec_row(db, owner_ref):
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT origin, gate_u, seeking, canon_version FROM necessities WHERE owner_ref=?",
            (owner_ref,)).fetchone()
    finally:
        con.close()


def test_community_overall_writes_profile_and_generated_necessity():
    db = _db()
    ctx = _ctx(db)
    decl = {"kind": "community_overall", "will_text": "地域の医療を変える",
            "state_have": "場", "state_can_type": "実績なし（型は導出不能）",
            "necessity": {"necessity_text": "現場を翻訳できる人", "gate_s": 0.6,
                          "gate_u": 0.1, "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0,
                          "evidence_span": "一緒に背負える人", "seeking": "実装できる人",
                          "generator": "Claude Opus 4.8", "generator_tag": "community-overall-v1.0/claude",
                          "attempt_n": 2}}
    r = IL.propose_intent(ctx, "founder", body="全体方針", declaration=decl, db_path=db)
    res = IL.agree_intent(r["intent_id"], "founder", db_path=db)
    conf = res["declaration_confirmed"]
    assert conf["kind"] == "community_overall"
    # profile.structured（コミュニティ）と necessity.published が書かれる
    assert len(_events(db, "profile.structured")) == 1
    nps = _events(db, "necessity.published")
    assert len(nps) == 1
    p = nps[0]["payload"]
    assert p["owner_ref"] == ctx and p["owner_kind"] == "subject" and p["origin"] == "generated"
    assert p["source_snapshot_hash"] == conf["profile_structured"]["content_hash"]  # §3-1 ピン留め
    assert p["generator_tag"] == "community-overall-v1.0/claude" and p["attempt_n"] == 2
    # gate_u は generated なのでクランプされない（0.1 のまま。self_declared なら 0.6）
    origin, gate_u, seeking, canon = _nec_row(db, ctx)
    assert origin == "generated" and abs(gate_u - 0.1) < 1e-9
    assert seeking == "実装できる人" and canon == "c2"


def test_intent_necessity_writes_only_intent_necessity():
    db = _db()
    ctx = _ctx(db)
    # 先に全体宣言を確定（source_snapshot の基準）
    IL.agree_intent(IL.propose_intent(ctx, "founder", declaration={
        "kind": "community_overall", "will_text": "W",
        "necessity": {"necessity_text": "N", "gate_s": 0.0, "gate_u": 0.0,
                      "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0}}, db_path=db)["intent_id"],
        "founder", db_path=db)
    base_ps = len(_events(db, "profile.structured"))
    # 目的別
    decl = {"kind": "intent_necessity", "purpose_text": "経理を整える",
            "necessity": {"necessity_text": "会計に強い人", "gate_s": 0.0, "gate_u": 0.3,
                          "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0,
                          "generator": "Gemini"}}
    r = IL.propose_intent(ctx, "founder", declaration=decl, db_path=db)
    res = IL.agree_intent(r["intent_id"], "founder", db_path=db)
    conf = res["declaration_confirmed"]
    assert conf["kind"] == "intent_necessity"
    # profile.structured は増えない（意志・現状は既に確定・§4-3）
    assert len(_events(db, "profile.structured")) == base_ps
    # owner_kind=intent の necessity.published が intent_id で書かれる
    p = [e["payload"] for e in _events(db, "necessity.published")
         if e["payload"]["owner_ref"] == r["intent_id"]][0]
    assert p["owner_kind"] == "intent" and p["origin"] == "generated"
    assert p["source_snapshot_hash"]   # 確定済み宣言の content_hash が入る


# ── §4-4 完了した取り組みの決定的提示（0 / 1-3 / 4+）─────────────────────────────
def _complete_one(db, ctx, purpose):
    # 選定規則の検証なので議決フローを介さず、提起＋完了イベントを直接作る。
    import intent_content as IC
    from canon import sha256_hex
    r = IL.propose_intent(ctx, "founder", body=purpose, declaration="", db_path=db)
    iid = r["intent_id"]
    IC.save_result(iid, f"done:{purpose}", db_path=db)
    le.append_event("founder", "intent.completed",
                    {"intent_id": iid, "result_hash": sha256_hex(purpose)}, db_path=db)
    return iid


def test_completed_episodes_deterministic_rule():
    db = _db(); ctx = _ctx(db)
    # 0件
    assert IL.completed_episodes_for_prompt(ctx, db_path=db) == {"count": 0, "episodes": []}
    # 1〜3件 → 全件
    _complete_one(db, ctx, "P1"); _complete_one(db, ctx, "P2")
    r = IL.completed_episodes_for_prompt(ctx, db_path=db)
    assert r["count"] == 2 and len(r["episodes"]) == 2
    # 4件以上 → 直近3件（新しい順）
    _complete_one(db, ctx, "P3"); _complete_one(db, ctx, "P4"); _complete_one(db, ctx, "P5")
    r = IL.completed_episodes_for_prompt(ctx, db_path=db)
    assert r["count"] == 5 and len(r["episodes"]) == 3
    assert r["episodes"][0]["body"] == "P5"   # 最新が先頭


# ── app マッパー ───────────────────────────────────────────────────────────────
def test_mapper_detects_both_types():
    overall = {"subject_kind": "community", "seeker": {"意志": "W",
               "現状": {"持っているもの": "H", "できること_型": "実績なし（型は導出不能）"}},
               "supporting_material": {"求めている": "実装者"},
               "necessity": {"necessity_text": "N", "gate_s": 0.3, "gate_u": 0.3,
                             "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "generator": "X"},
               "_meta": {"source": "コミュニティ構造化プロンプト（全体用）v1.0"}}
    d1 = appmod._community_declaration_from_payload(overall)
    assert d1["kind"] == "community_overall" and d1["will_text"] == "W"
    assert d1["state_can_type"] == "実績なし（型は導出不能）"      # §4-2 受理
    assert d1["necessity"]["seeking"] == "実装者"

    purpose = {"schema_version": "v4", "kind": "intent_necessity", "purpose_text": "P",
               "supporting_material": {"求めている": "未取得"},
               "necessity": {"necessity_text": "N", "gate_s": 0.0, "gate_u": 0.0,
                             "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0}}
    d2 = appmod._community_declaration_from_payload(purpose)
    assert d2["kind"] == "intent_necessity" and d2["purpose_text"] == "P"
    assert d2["necessity"]["seeking"] == ""                       # "未取得" は空に

    assert appmod._community_declaration_from_payload({"foo": 1}) is None


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-q"])
