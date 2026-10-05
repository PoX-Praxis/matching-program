"""指示書56（v3）— 照合の段1: 壊れた数式を真っ当な形に戻す（計算の整理のみ）。

196 γ が計算に使われていない／197 c が判定に使われていない（共鳴は a のみ）
198 補完A・補完B を別々に判定（片方向だけ通る組が出る）／199 互いに埋めるは両方が閾値以上のときだけ
200 p = 0 固定／201 WILL_FLOOR_G が撤去されている／202 数値が画面にも API にも出ない
203 0 件のとき本人の側の理由だけを 1 行／204 c の設計からのずれが docs に記録されている
"""
import inspect, json, math, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import match_config
import matcher_v4 as M
from db_v4 import MemoryStore
from embedding_config import MODEL_TAG


def _u(c):
    return [c, math.sqrt(max(0.0, 1 - c * c))]


def _seeker(a=1.0, need=1.0, state=1.0, wp=1.0):
    return {"will_symmetric": _u(1.0), "necessity_query": _u(1.0), "state_passage": _u(state),
            "will_passage": _u(wp)}


def _cand(a=1.0, b=1.0, d=1.0, c=1.0):
    # seeker の各ベクトルは e1 方向なので、相手側のベクトルの第 1 成分がそのまま cos になる
    return {"will_symmetric": _u(a), "state_passage": _u(b), "necessity_query": _u(d), "will_passage": _u(c)}


def _rank(cand, **kw):
    s = {"will_symmetric": _u(1.0), "necessity_query": _u(1.0), "state_passage": _u(1.0), "will_passage": _u(1.0)}
    return M.rank_candidates(s, [("x", cand)], **kw)[0]


# ── 196 γ ──────────────────────────────────────────────────────────────────────
def test_t196_gamma_not_used():
    c = _cand(a=0.5, b=0.4, d=0.3, c=0.1)
    assert _rank(c, gamma=0.0)["score"] == _rank(c, gamma=0.5)["score"]
    src = inspect.getsource(M)
    assert "GAMMA" not in src and "complement" not in src
    assert not hasattr(match_config, "GAMMA_EPS")


# ── 197 c ──────────────────────────────────────────────────────────────────────
def test_t197_c_not_used_resonance_is_a_only():
    lo, hi = _rank(_cand(c=0.0)), _rank(_cand(c=1.0))
    assert lo["score"] == hi["score"] and lo["score_b"] == hi["score_b"]
    assert "c_sim" not in lo["attribution"] and "gc" not in lo["attribution"]
    assert M.public_axis({"ga": 0.9, "gb": 0.6, "gd": 0.6}) == "will"           # 共鳴は a だけで決まる


# ── 198 方向別の判定 ────────────────────────────────────────────────────────────
def test_t198_directions_judged_separately():
    r = _rank(_cand(a=0.6, b=0.9, d=-0.6))      # 自分は相手を必要とするが、相手は自分を必要としていない
    assert r["score"] >= match_config.MATCH_ENTRY_THRESHOLD > r["score_b"]
    assert M.passes_entry(r)                     # 片方向だけで通る（平均で薄まって落ちない）
    avg = (r["attribution"]["gb"] + r["attribution"]["gd"]) / 2
    alt = M.power_mean([r["attribution"]["ga"], avg], [1.0, 1.0], 0.0)
    assert alt < match_config.MATCH_ENTRY_THRESHOLD    # 平均していたら落ちていた組
    r2 = _rank(_cand(a=0.6, b=-0.6, d=0.9))     # 逆向きだけでも通る
    assert M.passes_entry(r2) and r2["score"] < match_config.MATCH_ENTRY_THRESHOLD


# ── 199 互いに埋める ────────────────────────────────────────────────────────────
def test_t199_mutual_only_when_both_directions_pass():
    lv = match_config.MATCH_ENTRY_THRESHOLD
    assert M.public_axis({"ga": 0.5, "gb": lv, "gd": lv}) == "mutual"
    assert M.public_axis({"ga": 0.5, "gb": 0.95, "gd": lv - 0.01}) == "fill_mine"
    assert M.public_axis({"ga": 0.5, "gb": lv - 0.01, "gd": 0.95}) == "fill_theirs"
    assert "require_will" not in inspect.signature(M.public_axis).parameters


# ── 200 p = 0 固定 ──────────────────────────────────────────────────────────────
def test_t200_p_fixed_at_zero():
    c = _cand(a=0.9, b=0.1)
    assert _rank(c, p=0.0)["score"] == _rank(c, p=-3.0)["score"] == _rank(c, p=2.0)["score"]
    ga, gb = (1 + 0.9) / 2, (1 + 0.1) / 2
    assert _rank(c)["score"] == pytest.approx(math.sqrt(ga * gb))   # α=β=1 の幾何平均
    assert M.P_FIXED == 0.0


# ── 201 WILL_FLOOR_G の撤去 ────────────────────────────────────────────────────
def test_t201_will_floor_removed():
    assert not hasattr(match_config, "WILL_FLOOR_G") and not hasattr(match_config, "WILL_REQUIREMENT_MIN")
    for name in ("will_floor", "will_required", "will_requirement"):
        assert not hasattr(M, name), name
    assert "floor" not in inspect.signature(M.passes_entry).parameters
    assert "_will_rule" not in open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()


# ── 202・203 数値を出さない／0 件の理由は本人の側だけ ────────────────────────────────
@pytest.fixture
def v4(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    store = MemoryStore()
    for pid, v in (("me", [1.0, 0.0]), ("c1", [0.8, 0.6]), ("far", [-1.0, 0.0])):
        store.save_profile(pid, fields={"will_text": "w", "state_have": "s"}, supporting_raw={},
                           supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {"necessity_text": f"n{pid}", "gate_s": 0.9, "gate_u": 0.3,
                                              "p_sharpness": 0.0, "alpha": 1.2, "beta": 1.0, "gamma": 0.465,
                                              "evidence_span": ""})
        store.save_vectors(pid, MODEL_TAG, {k: list(v) for k in
                                            ("will_symmetric", "will_passage", "state_passage", "necessity_query")})
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    monkeypatch.setattr(appmod, "_seeker_live_necessity_id", lambda sid, db_path=None: None)
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


def _cli(sid):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["subject_id"] = sid
    return c


def test_t202_no_numbers_in_api_or_screen(v4):
    d = _cli("me").post("/v4/match", json={}).get_json()
    assert [r["candidate_id"] for r in d["results"]] == ["c1"]
    blob = json.dumps(d, ensure_ascii=False)
    for k in ("gate_s", "gate_u", "alpha", "beta", "score", "gamma", "p_sharpness", "0.", "総合"):
        assert k not in blob, k
    html = appmod.app.test_client().get("/connect").get_data(as_text=True)
    for k in ("gate_s", "alpha", "総合", "score"):
        assert k not in html, k


def test_t203_zero_result_reason_is_own_side_only(v4):
    v4.vectors = {k: v for k, v in v4.vectors.items() if k[0] in ("me", "far")}
    d = _cli("me").post("/v4/match", json={}).get_json()
    assert d["status"] == "none" and d["results"] == []
    assert set(d) <= {"seeker_id", "necessity_id", "query_unit", "model_tag", "status", "results", "match_run_id"}
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "まだ照合できる相手がいません" in html and "まだ必要像がありません" in html
    assert "excluded" not in html and "not_linked" not in html                # 相手ごとの除外理由は出さない


# ── 204 c のずれの記録 ─────────────────────────────────────────────────────────
def test_t204_c_deviation_recorded_in_docs():
    doc = open(os.path.join(ROOT, "docs", "matching.md"), encoding="utf-8").read()
    assert "c は「必要像 × 意志」" in doc and "will_passage" in doc and "対称" in doc
    assert "閾値 0.70 は暫定" in doc and "段2〜3" in doc
