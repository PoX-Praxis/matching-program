"""指示書17 §7-5 step3: /v4/match の必要像起点への切替（フォールバック付き）。

MemoryStore＋build_vectors(stub) と necessities(sqlite) で、
_match_by_necessity が person 起点 match_v4 と同一順位・スコアになることを確認（1:1）。
未ベクトル化・必要像なしでは LookupError/None になり呼び出し側が人起点へ落ちる。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import necessities as N
from db_v4 import MemoryStore, match_v4
from embedding_service import build_vectors
from embedding_config import MODEL_TAG
from necessity_gen import compute_gamma

_GAMMA = compute_gamma(0.6, 0.3)   # 本番と同じ: 保存 gamma は gate から導出される


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.DB = db
    return db


def _profile(will, have, ntext):
    return {"will_text": will, "state_have": have, "state_can_type": "",
            "state_bound": "", "state_unsorted": "", "supporting_raw": {}}, ntext


_NUMS = {"gate_s": 0.6, "gate_u": 0.3, "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0,
         "necessity_text": "翻訳できる開発者", "src_input_hash": "H", "is_generated": True,
         "generator_name": "Claude Opus 4.8"}


def _seed_store(store):
    # owner と候補3人を build_vectors(stub) で保存
    people = {
        "own": _profile("つなぐ意志", "設計知識", "翻訳できる開発者"),
        "c1": _profile("実装したい", "実装力", "設計できる人"),
        "c2": _profile("研究したい", "分析力", "データ基盤の人"),
        "c3": _profile("広めたい", "発信力", "作れる人"),
    }
    for pid, (prof, ntext) in people.items():
        store.save_profile(pid, fields={k: prof.get(k, "") for k in
                           ("will_text", "state_have", "state_can_type", "state_bound",
                            "state_unsorted", "background", "will_where", "will_why",
                            "will_origin", "one_liner")},
                           supporting_raw={}, supporting_redacted={},
                           pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {**_NUMS, "necessity_text": ntext,
                                              "gamma": _GAMMA, "evidence_span": ""})
        vecs = build_vectors({k: prof.get(k, "") for k in
                              ("will_text", "state_have", "state_can_type",
                               "state_bound", "state_unsorted")}, ntext)
        store.save_vectors(pid, MODEL_TAG, vecs)


def test_necessity_match_equals_person_match_for_owner():
    db = _setup()
    store = MemoryStore()
    _seed_store(store)

    # 必要像レコードを owner に発行＋ベクトル化（build_vectors と同一 embed 経路）
    own_prof, own_ntext = _profile("つなぐ意志", "設計知識", "翻訳できる開発者")
    appmod._publish_necessity_best_effort("own", own_prof,
                                          {**_NUMS, "necessity_text": own_ntext})

    nec_id = appmod._seeker_live_necessity_id("own", db_path=db)
    assert nec_id is not None                          # 生きた必要像が見つかる

    by_nec = appmod._match_by_necessity(store, nec_id, model_tag=MODEL_TAG, write_ledger=False)
    person = match_v4(store, "own", write_ledger=False)

    assert by_nec["query_unit"] == "necessity"
    assert [r["candidate_id"] for r in by_nec["results"]] == \
           [r["candidate_id"] for r in person["results"]]      # 同一順位
    assert [round(r["score"], 9) for r in by_nec["results"]] == \
           [round(r["score"], 9) for r in person["results"]]   # 同一スコア


def test_fallback_when_no_necessity():
    db = _setup()
    assert appmod._seeker_live_necessity_id("nobody", db_path=db) is None   # → 人起点へ落ちる


def test_lookup_error_when_not_vectorized():
    db = _setup()
    store = MemoryStore()
    _seed_store(store)
    # 必要像は発行するがベクトル化しない
    r = N.publish_necessity("own", "subject",
                            {**_NUMS, "will_text": "つなぐ意志"}, db_path=db)
    import pytest
    with pytest.raises(LookupError):
        appmod._match_by_necessity(store, r["necessity_id"], model_tag=MODEL_TAG,
                                   write_ledger=False, db_path=db)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
