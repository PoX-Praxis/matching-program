"""指示書17 §5-2/§5-3: profile.structured（主体の構造化時点の台帳記録）。Nomic 非依存。

- 意志＋現状4スロットの content_hash・n 単調・prev_snapshot 鎖
- churn（同一内容）はスキップ・変化で n 進行
- 本文は payload に載せない（content_hash のみ）
- best-effort ヘルパ経由でも同様
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import subject_ledger as SL
import ledger_events as le
import app as appmod


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


_P = {"will_text": "つなぐ", "state_have": "知識", "state_can_type": "型",
      "state_bound": "", "state_unsorted": "", "supporting_raw": {}}


def test_publish_records_hash_n_and_chain():
    db = _db()
    r1 = SL.publish_profile_structured("u1", dict(_P), db_path=db)
    assert r1["skipped"] is False and r1["n"] == 1 and r1["prev_snapshot"] is None
    r2 = SL.publish_profile_structured("u1", dict(_P, will_text="つなぎ続ける"), db_path=db)
    assert r2["n"] == 2 and r2["prev_snapshot"] == r1["content_hash"]
    evs = le.get_events(type_="profile.structured", db_path=db)
    assert len(evs) == 2
    assert "will_text" not in evs[0]["payload"] and "state_have" not in evs[0]["payload"]
    assert evs[0]["payload"]["members_after_hash"] is None      # individual
    assert le.verify_chain(db_path=db)["ok"] is True


def test_churn_skips_identical_profile():
    db = _db()
    SL.publish_profile_structured("u2", dict(_P), db_path=db)
    r = SL.publish_profile_structured("u2", dict(_P), db_path=db)   # 同一内容
    assert r["skipped"] is True
    assert len(le.get_events(type_="profile.structured", db_path=db)) == 1


def test_content_hash_depends_on_will_and_state():
    h0 = SL.profile_content_hash(_P)
    assert SL.profile_content_hash(dict(_P, state_bound="制約")) != h0
    assert SL.profile_content_hash(dict(_P, will_text="別")) != h0


def test_best_effort_helper_wires():
    db = _db()
    appmod.DB = db
    appmod._publish_profile_structured_best_effort("u3", dict(_P))
    assert len(le.get_events(type_="profile.structured", db_path=db)) == 1
    # 例外は握りつぶす（不正入力でも本体を止めない）
    appmod._publish_profile_structured_best_effort("u3", None)   # will raise内部→skip
    assert le.verify_chain(db_path=db)["ok"] is True


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nsubject_ledger テスト: {len(tests)} 件 全 PASS")
