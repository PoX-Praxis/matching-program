"""指示書63 段階1 PR-B — 監査ルートに文単位の対（テスト 282〜286）。

`GET /ledger/audit/match?pair=a,b&detail=sentences`。運営者がトークンで確かめるためだけの窓口で、利用者の画面・
公開 API は変えない。必要像・与え像・現状の文は公開の範囲なので返す。生テキスト・根拠・evidence_span は返さない。
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import pytest  # noqa: E402

import app as appmod  # noqa: E402
import v5  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from matcher_v5 import judge, judge_direction  # noqa: E402
from test_t61_resonance_gate import STORY, X, Y, db, world  # noqa: E402,F401  （同じ照合の世界を使う）

TOKEN = "tok"


@pytest.fixture
def audit(world, monkeypatch):
    monkeypatch.setenv("POX_ANCHOR_TOKEN", TOKEN)
    monkeypatch.setattr(appmod, "_seeker_live_necessity_id", lambda sid, db_path=None: None)

    def get(pair, detail="sentences", token=TOKEN):
        q = f"/ledger/audit/match?pair={pair}" + (f"&detail={detail}" if detail else "")
        h = {"X-Anchor-Token": token} if token else {}
        return appmod.app.test_client().get(q, headers=h)
    return get


def _sides(a, b):
    store = appmod._v4_store()
    return appmod._side_of(store, a, MODEL_TAG), appmod._side_of(store, b, MODEL_TAG)


def test_t282_sentences_cover_all_needs_and_match_judgement(audit):
    d = audit("me,c_gate").get_json()["v5"]["sentences"]
    sa, sb = _sides("me", "c_gate")
    for entry in d:
        mine, other = (sa, sb) if entry["direction"] == "a_to_b" else (sb, sa)
        p = next(x for x in mine["purposes"] if x["purpose_id"] == entry["purpose_id"])
        assert [r["need"] for r in entry["sentences"]] == [n["text"] for n in p["needs"]]      # 全必要像の文
        for r, n in zip(entry["sentences"], p["needs"]):
            best = next(o for o in other["offers"] if o["text"] == r["best"])
            assert r["passed"] == judge(n["vec"], best["vec"]) and r["must"] == bool(n["required"])
            assert r["best_source"] == "与え像" and isinstance(r["g"], float) and round(r["g"], 2) == r["g"]
        ok, _, info = judge_direction(p, other)
        assert entry["passed"] == ok and entry["gate"]["gate"] == info["gate"]                 # 判定と一致
        assert entry["must_all_paired"] == all(r["passed"] for r in entry["sentences"] if r["must"])
    assert {e["direction"] for e in d} == {"a_to_b", "b_to_a"}


def test_t283_top3_sorted_desc_at_most_three(audit):
    for e in audit("me,c_ok").get_json()["v5"]["sentences"]:
        gs = [t["g"] for t in e["top3"]]
        assert len(gs) <= 3 and gs == sorted(gs, reverse=True)


def test_t284_no_raw_text_evidence_or_generator(audit):
    blob = json.dumps(audit("me,c_ok").get_json(), ensure_ascii=False)
    for k in ("生テキスト", "根拠", "evidence_span", "generator", STORY[:10], "事業を一緒に立ち上げる人がほしい"):
        assert k not in blob, k


def test_t285_token_required(audit):
    assert audit("me,c_ok", token=None).status_code == 404
    assert audit("me,c_ok", token="wrong").status_code == 404


def test_t286_without_detail_unchanged(audit):
    d = audit("me,c_ok", detail=None).get_json()["v5"]
    assert "sentences" not in d and set(d) >= {"a_to_b", "b_to_a", "has_offer", "res_gate_min"}


def test_t63b_v4_counterpart_full_state_and_display_field(audit, monkeypatch):
    """v4 の相手は判定の単位が現状の全文。表示用の欄は display_field として別に添える（番号なし）。"""
    pvs = {"c_v4ok": {"state_have": "試作の場を持っている", "state_can_type": "人をつなぐ"}}
    monkeypatch.setattr(appmod, "get_profile_view", lambda sid, db_path=None: pvs.get(sid))
    v5.save_state_slot_vectors("c_v4ok", ["試作の場を持っている", "人をつなぐ", "", ""], MODEL_TAG,
                               lambda t, r: X if t.startswith("試作") else Y, db_path=appmod.DB)
    d = audit("me,c_v4ok").get_json()["v5"]["sentences"]
    a = [e for e in d if e["direction"] == "a_to_b"]
    row = a[0]["sentences"][0]
    assert row["best_source"] == "現状（全文）"
    assert row["display_field"] == {"field": "持っているもの", "text": "試作の場を持っている"}
