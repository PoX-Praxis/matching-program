"""指示書17 段階5: 必要像の 1:N 化の基盤（storage＋イベント＋不変条件）。

- publish_necessity: necessities 行＋necessity.published イベント（n・prev_necessity 鎖）
- content_hash = H(text ‖ 数値素材 ‖ commit(evidence_span, salt))・evidence 束縛
- generator 正規化（モデルファミリ）
- self_declared の gate_u クランプ（≥0.6）
- liveness 導出（置換・取り下げ）
- evidence 平文/salt の削除で開けなくなる
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import necessities as N
import ledger_events as le


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_generator_normalization():
    assert N.normalize_generator("Claude Opus 4.8") == "claude-opus"
    assert N.normalize_generator("gpt-4o") == "gpt"
    assert N.normalize_generator("nomic-embed-text-v2-moe") == "nomic"
    assert N.normalize_generator("") == ""
    assert N.normalize_generator("MysteryModel-9") == "other"


def test_gate_u_clamp_only_for_self_declared():
    assert N.clamp_gate_u("self_declared", 0.2) == 0.6
    assert N.clamp_gate_u("self_declared", None) == 0.6
    assert N.clamp_gate_u("self_declared", 0.9) == 0.9
    assert N.clamp_gate_u("generated", 0.2) == 0.2      # generated はクランプしない


def test_content_hash_binds_evidence_commitment():
    salt = N.make_salt()
    c1 = N.evidence_commitment("根拠A", salt)
    c2 = N.evidence_commitment("根拠B", salt)
    assert c1 != c2                                     # 根拠が変われば commit が変わる
    nums = {"gate_s": 0.5, "gate_u": 0.3, "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0}
    h1 = N.compute_content_hash("必要像", nums, c1)
    h2 = N.compute_content_hash("必要像", nums, c2)
    assert h1 != h2                                     # 根拠差し替えは content_hash に響く
    nums2 = dict(nums, gate_s=0.6)
    assert N.compute_content_hash("必要像", nums2, c1) != h1   # 数値素材も content_hash に入る


def test_publish_writes_row_and_ledger_event_with_n_and_prev():
    db = _db()
    nec = {"necessity_text": "翻訳できる開発者", "will_text": "つなぐ",
           "gate_s": 0.6, "gate_u": 0.3, "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0,
           "evidence_span": "原文の一部", "generator_name": "Claude Opus 4.8"}
    r1 = N.publish_necessity("u1", "subject", nec, origin="generated", db_path=db)
    assert r1["n"] == 1 and r1["prev_necessity"] is None
    r2 = N.publish_necessity("u1", "subject", dict(nec, necessity_text="支える人"),
                             origin="generated", db_path=db)
    assert r2["n"] == 2 and r2["prev_necessity"] == r1["necessity_id"]

    evs = le.get_events(type_="necessity.published", db_path=db)
    assert len(evs) == 2
    assert evs[0]["payload"]["generator"] == "claude-opus"       # 正規化されている
    assert evs[0]["payload"]["content_hash"] == r1["content_hash"]
    # 本文は台帳に載せない（content_hash のみ）
    assert "necessity_text" not in evs[0]["payload"]
    assert le.verify_chain(db_path=db)["ok"] is True


def test_self_declared_gate_u_persisted_clamped():
    db = _db()
    r = N.publish_necessity("u2", "subject",
                            {"necessity_text": "誰か", "will_text": "w", "gate_u": 0.1},
                            origin="self_declared", db_path=db)
    row = N.get_necessities("u2", db_path=db)[0]
    assert row["gate_u"] == 0.6 and row["origin"] == "self_declared"
    _ = r


def test_liveness_supersede_and_retire():
    db = _db()
    r1 = N.publish_necessity("u3", "subject", {"necessity_text": "x", "will_text": "w"}, db_path=db)
    r2 = N.publish_necessity("u3", "subject", {"necessity_text": "y", "will_text": "w"}, db_path=db)
    assert r2["necessity_id"] != r1["necessity_id"]              # 内容が違えば新レコード
    assert N.is_live(r1["necessity_id"], db_path=db) is False    # 後続に置換された
    assert N.is_live(r2["necessity_id"], db_path=db) is True     # 最新は生きている
    live = N.get_live_necessities("u3", db_path=db)
    assert [x["necessity_id"] for x in live] == [r2["necessity_id"]]
    # 自主取り下げ
    N.retire_necessity(r2["necessity_id"], reason="やめた", db_path=db)
    assert N.is_live(r2["necessity_id"], db_path=db) is False
    assert le.verify_chain(db_path=db)["ok"] is True


def test_churn_skips_identical_content():
    db = _db()
    nec = {"necessity_text": "同じ", "will_text": "w", "evidence_span": "根拠", "gate_s": 0.5}
    r1 = N.publish_necessity("u5", "subject", nec, db_path=db)
    r2 = N.publish_necessity("u5", "subject", dict(nec), db_path=db)   # 同一内容（salt は別でも）
    assert r2["skipped"] is True and r2["necessity_id"] == r1["necessity_id"]
    assert len(N.get_necessities("u5", db_path=db)) == 1
    assert len(le.get_events(type_="necessity.published", db_path=db)) == 1


def test_evidence_delete_makes_commitment_unopenable():
    db = _db()
    r = N.publish_necessity("u4", "subject",
                            {"necessity_text": "x", "will_text": "w", "evidence_span": "秘密の根拠"},
                            db_path=db)
    nid = r["necessity_id"]
    assert N.open_evidence(nid, db_path=db) == "秘密の根拠"
    N.delete_evidence(nid, db_path=db)
    assert N.open_evidence(nid, db_path=db) is None              # salt ごと消えて開けない
    # content_hash は残る（必要像レコードは生きたまま検証可能）
    assert N.get_necessities("u4", db_path=db)[0]["content_hash"]


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nnecessities テスト: {len(tests)} 件 全 PASS")
