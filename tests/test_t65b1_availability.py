"""指示書65B-1 — 可用性の修正（テスト 326〜329）。

現行タグのベクトルを持たない登録者が 1 人でもいると、以前は全員の照合と根拠が止まった（自分の判定が他人の登録で
変わる＝母集団への依存）。いまは**その人だけ**を照合から外し、外したことはシステムの状態として伝える。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import v5  # noqa: E402
from embedding_config import MODEL_TAG  # noqa: E402
from test_t61_resonance_gate import X, _cli, _groups, db, world  # noqa: E402,F401

OLD = "nomic-emb-v1"


def _stale(store, sid):
    """旧タグのベクトルだけを持つ人（モデルの切替の途中）。"""
    store.save_profile(sid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={}, supporting_redacted={},
                       pii_redaction_status="none", migrated_from=None, generation_status="ready")
    store.save_vectors(sid, OLD, {k: list(X) for k in ("will_symmetric", "will_passage", "state_passage",
                                                        "necessity_query")})


# ── 326 版の揃わない人が 1 人いても、他の人の照合は動く。その人は候補に出ない ──────────────────
def test_t326_others_keep_matching(world):
    before, _ = _groups()
    _stale(world, "u_old")
    assert world.count_missing_tag(MODEL_TAG) == 1
    d, by = _groups()
    assert d["status"] == "ok" and d["partial_pool"] is True
    assert "u_old" not in [r["candidate_id"] for r in d["results"]]
    assert sorted(r["candidate_id"] for r in d["results"]) == sorted(r["candidate_id"] for r in before["results"])
    assert "partial_pool" not in before                                       # 揃っていれば印は付かない


# ── 327 本人が揃っていなければ、本人だけ「準備中」 ─────────────────────────────────────────
def test_t327_self_stale_unavailable(world):
    world.vectors = {k: v for k, v in world.vectors.items() if k[0] != "c_ok"}
    world.save_vectors("c_ok", OLD, {k: list(X) for k in ("will_symmetric", "will_passage", "state_passage",
                                                           "necessity_query")})
    d = _cli("c_ok").post("/v4/match", json={}).get_json()
    assert d["status"] == "unavailable" and "あなたの登録" in d["reason"]
    assert _groups()[0]["status"] == "ok"                                     # 他の人は動く


# ── 328 根拠: 関わらない人の切替では止まらない。揃っていない人との根拠だけ出さない ───────────────────
def test_t328_reason_not_stopped_by_others(world):
    pid = v5.live_necessities_v5("c_ok", db_path=appmod.DB)[0]["purpose_id"]
    assert _cli("c_ok").post("/approve", json={"to_id": "me", "purpose_id": pid}).status_code == 200
    _stale(world, "u_old")
    r = _cli("me").get("/api/connections/reason?with=c_ok").get_json()
    assert r["reason"] and r["reason"]["reasons"]                             # 以前は {"reason": None} になった
    assert _cli("u_old").post("/approve", json={"to_id": "me"}).status_code == 200
    assert _cli("me").get("/api/connections/reason?with=u_old").get_json() == {"reason": None, "status": "unavailable"}


# ── 329 画面: システムの状態として伝える（個人の情報は出さない） ────────────────────────────────
def test_t329_notice_is_system_state():
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "照合の準備が済んでいない登録者がいるため、その人たちは、いまの照合の対象に入っていません。" in html
    assert "data.partial_pool" in html
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "store.count_missing_tag(MODEL_TAG)" not in src                     # 全員を止める判定は残っていない
