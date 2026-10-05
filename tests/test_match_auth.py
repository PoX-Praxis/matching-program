"""指示書55 PR-A — 照合 API の本人限定・数値の非公開・「最も効いた軸」・同型の認証漏れの一巡。

161: 照合 API は本人のみ（未ログイン 401／他人 403。body の id を信じない）
162: /v4/match の応答に score・attribution が入らない
165: 説明は「最も効いた軸」（律速軸を「効いている」と出さない）
"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import necessities as N
from db_v4 import MemoryStore
from embedding_config import MODEL_TAG
from matcher_v4 import attribution, effective_axis
from necessity_gen import compute_gamma


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


# 合成ベクトル（2 次元）。照合の数式は次元に依存しない。me と c1 は噛み合い、c2 は逆向き。
V = {"me": [1.0, 0.0], "c1": [1.0, 0.0], "c2": [-1.0, 0.0]}


def _vecs(x):
    return {k: list(x) for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query")}


@pytest.fixture
def v4(monkeypatch):
    """Postgres の代わりに MemoryStore で /v4/match を通す。"""
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    store = MemoryStore()
    gamma = compute_gamma(0.6, 0.3)
    for pid, will, have, nec in (("me", "つなぐ意志", "設計知識", "翻訳できる開発者"),
                                 ("c1", "実装したい", "実装力", "設計できる人"),
                                 ("c2", "研究したい", "分析力", "データ基盤の人")):
        store.save_profile(pid, fields={"will_text": will, "state_have": have}, supporting_raw={},
                           supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {"necessity_text": nec, "gate_s": 0.6, "gate_u": 0.3,
                                              "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0,
                                              "gamma": gamma, "evidence_span": ""})
        store.save_vectors(pid, MODEL_TAG, _vecs(V[pid]))
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)      # 実バックエンド相当
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))       # 全員が本人と紐づく
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


# ── 161: 照合 API は本人のみ ──────────────────────────────────────────────
def test_t161_v4_match_self_only(v4):
    assert _cli().post("/v4/match", json={"seeker_id": "me"}).status_code == 401
    assert _cli("c1").post("/v4/match", json={"seeker_id": "me"}).status_code == 403   # 他人の分
    assert _cli("me").post("/v4/match", json={"seeker_id": "me"}).status_code == 200
    assert _cli("me").post("/v4/match", json={}).status_code == 200                     # 省略時は本人


def test_t161_legacy_match_self_only(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    assert _cli().post("/match", json={"seeker_id": "me"}).status_code == 401
    assert _cli("c1").post("/match", json={"seeker_id": "me"}).status_code == 403


def test_t161_necessity_id_of_others_is_forbidden(v4, monkeypatch):
    monkeypatch.setattr(N, "get_necessity", lambda nid, db_path=None: {"owner_ref": "c1"})
    r = _cli("me").post("/v4/match", json={"seeker_id": "me", "necessity_id": "n_of_c1"})
    assert r.status_code == 403


# ── 162: 応答に score・attribution・順位が入らない（中立の順） ───────────────────
def test_t162_no_numbers_in_response(v4):
    d = _cli("me").post("/v4/match", json={"seeker_id": "me"}).get_json()
    blob = json.dumps(d)
    for k in ("score", "attribution", "a_sim", "b_sim", "c_sim", "final", "limiting_axis",
              "log_contrib", "rank", "gamma", "alpha", "beta"):
        assert k not in blob, k
    assert [r["candidate_id"] for r in d["results"]] == ["c1"]                # c2 は入口の閾値未満
    assert set(d["results"][0]) == {"candidate_id", "handle", "name", "one_liner", "axis", "reasons"}
    assert "pool_size" not in d                                                 # 件数も出さない（170）


def test_t162_legacy_match_returns_ids_only(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    monkeypatch.setattr(appmod, "get_seeker", lambda uid, db_path=None: {"意志": "x"})
    monkeypatch.setattr(appmod, "list_candidate_pool",
                        lambda uid, db_path=None: [{"id": "b", "profile": "実装"}, {"id": "a", "profile": "x"}])
    d = _cli("me").post("/match", json={"seeker_id": "me"}).get_json()
    assert d["ranking"] == [{"id": "a"}, {"id": "b"}]                         # 数値・理由文・順位なし


# ── 165: 最も効いた軸（律速軸の逆） ─────────────────────────────────────────
def test_t165_effective_axis_is_strongest_not_limiting():
    # a（意志）が弱く、b（補完）が強い相手: 律速軸は a、最も効いた軸は b
    attr = {"ga": 0.55, "gb": 0.95}
    assert effective_axis(attr) == "b"
    assert effective_axis({"ga": 0.9, "gb": 0.6}) == "a"        # c は候補にしない（指示書56）
    # 実際の attribution でも律速軸とは異なる軸を返しうる（同じ値にならないことを確認）
    import math
    v = lambda x: [x, math.sqrt(1 - x * x)]
    seeker = {"will_symmetric": v(0.0), "necessity_query": v(1.0), "will_passage": v(1.0)}
    cand = {"will_symmetric": [0.0, -1.0], "state_passage": v(1.0), "will_passage": v(1.0)}
    at = attribution(seeker, cand, gamma=0.0)
    assert at["limiting_axis"] == "a" and effective_axis(at) == "b"


def test_t165_ui_no_longer_uses_limiting_axis_or_score():
    html = _cli().get("/connect").get_data(as_text=True)      # 共通部品（_match_reason.html）を含む描画結果
    assert "limiting_axis" not in html and "総合" not in html and "sortMode" not in html   # 163・164 の先取り
    assert "PoXReason.reasonHtml(r)" in html and "意志が近い" in html and "足りないところを埋める" in html


# ── 同型の一巡: プロフィールの編集は本人のみ（以前は未認証で書き換えられた）─────────────
@pytest.mark.parametrize("method,path,body", [
    ("post", "/api/profile/u_a/core", {"意志": "乗っ取り"}),
    ("put", "/api/profile/u_a/overrides", {"overrides": {"x": 1}}),
])
def test_t194_t161_profile_edit_self_only(monkeypatch, method, path, body):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    assert getattr(_cli(), method)(path, json=body).status_code == 401
    assert getattr(_cli("u_evil"), method)(path, json=body).status_code == 403
    assert getattr(_cli("u_a"), method)(path, json=body).status_code in (200, 404)   # 本人は通る


# ── 同型の一巡: ログイン不要の書き込み経路は、理由のある許可リストだけ ─────────────────
ALLOWED_UNAUTH_WRITES = {
    "/auth/request", "/auth/logout",                       # ログインそのもの
    "/ledger/anchor",                                      # X-Anchor-Token（外部スケジューラ）
    "/ledger/admin/purge-accounts",                        # X-Anchor-Token（一回限りのアカウント整理・使用後に閉じる）
    "/seekers",                                            # 410（閉鎖済みの旧登録）
    "/api/community/<community_id>/intent/propose", "/api/community/<community_id>/declare",
    "/api/intent/<intent_id>/agree", "/api/intent/<intent_id>/complete",
    "/api/intent/<intent_id>/cancel", "/api/intent/<intent_id>/participant/join",   # 410（凍結）
}


def test_t193_no_unexpected_unauthenticated_write_routes():
    bad = []
    for r in appmod.app.url_map.iter_rules():
        if not ({"POST", "PUT", "PATCH", "DELETE"} & set(r.methods)):
            continue
        fn = appmod.app.view_functions[r.endpoint]
        guarded = getattr(fn, "__wrapped__", None) is not None      # login_required は functools.wraps
        if not guarded and r.rule not in ALLOWED_UNAUTH_WRITES:
            bad.append(r.rule)
    assert bad == [], bad
