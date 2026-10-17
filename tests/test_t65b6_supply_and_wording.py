"""指示書65B-6 — 与え像だけの人を候補に・根拠が無い理由の区別と文言（テスト 367〜369）。

与え像だけの人（必要像が無い）は、供給側（あなたの目的を満たす相手）にだけ出る（v4 の人が求める側にだけ出るのと対）。
根拠が無い理由を書き分ける: 要素・目的全体・可用性・下層が閉じている間（65-B §5）。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import v5  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from test_t61_resonance_gate import X, Y, Z, _cli, _confirm, _doc, _put, db  # noqa: E402,F401
from test_t65b2_two_layer import _match, world2  # noqa: E402,F401


def _offer_only(store, sid, offers, dest=X):
    """与え像だけの人（目的・必要像が無い）。"""
    store.save_profile(sid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={}, supporting_redacted={},
                       pii_redaction_status="none", migrated_from=None, generation_status="ready")
    store.save_vectors(sid, MODEL_TAG, {"will_symmetric": dest, "will_passage": dest, "state_passage": Z,
                                        "necessity_query": Z})        # 必要像の本文は無い（v4 の代わりにもならない）
    _confirm(sid, _doc(purposes=[]))
    off = v5.latest_offer(sid, db_path=appmod.DB)
    _put("offer", off["offer_id"], offers, texts=[x["文"] for x in off["sentences"]])


# ── 367 与え像だけの人が候補に出る（供給側だけ）───────────────────────────────────────────
def test_t367_offer_only_person_is_supply_candidate(world2):
    _offer_only(world2, "s_only", offers=[X, Y])
    assert v5.live_necessities_v5("s_only", db_path=appmod.DB) == []
    d, g, pids = _match()
    assert "s_only" in [r["candidate_id"] for r in g[pids[0]]["results"]]          # あなたの目的をすべて覆う人として
    theirs = next((x for x in d["groups"] if x["purpose_id"] == "_theirs"), {"results": []})
    assert "s_only" not in [r["candidate_id"] for r in theirs["results"]]           # 求める側には出ない
    _offer_only(world2, "s_none", offers=[[0.0, 0.0, -1.0]] * 2)
    d, g, pids = _match()
    assert all(r["candidate_id"] != "s_none" for x in d["groups"] for r in x["results"])   # 応えなければ出ない


# ── 368 可用性は別の文言（いま照合できません）。受信箱の根拠も「根拠はありません」と分ける ─────────────────
def test_t368_availability_wording(world2, monkeypatch):
    assert _cli("c_part").post("/approve", json={"to_id": "me"}).status_code == 200
    monkeypatch.setattr(appmod, "_matching_available", lambda: False)
    r = _cli("me").get("/api/connections/reason?with=c_part").get_json()
    assert r == {"reason": None, "status": "unavailable"}
    part = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    body = part[part.index("function approvalHtml("):part.index("/* 申し出る前の根拠")]
    assert "いま照合できません" in body and "照合による根拠はありません" in body
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "いま照合できません" in html


# ── 369 文言の書き分け（要素・目的全体・可用性・下層が閉じている間）──────────────────────────────
def test_t369_wording_split():
    connect = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    reason = open(os.path.join(ROOT, "templates", "_match_reason.html"), encoding="utf-8").read()
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "この要素に応える人が見つかりませんでした" in connect                 # 要素（言い切る）
    assert "いまは、この目的をすべて満たす相手はいません" in connect             # 目的全体（控えめに）
    assert "いま照合できません" in connect and "いま照合できません" in reason   # 可用性
    assert 'PREVIEW_PREPARING = "要素単位の照合は準備中です"' in src            # 下層が閉じている間
    # 閉じている間の申し出前の根拠で「あなたの必要に応える文は見つかりませんでした」と言い切らない
    assert "PREVIEW_NONE if lower_open else PREVIEW_PREPARING" in src
