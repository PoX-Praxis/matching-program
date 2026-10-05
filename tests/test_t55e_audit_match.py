"""指示書55-4 — 内部値の実測（/ledger/audit/match）と 55-3 の残り。

- GET /ledger/audit/match は閉じた（指示書58 §2-4。他の一時ルートと一緒に 404）
- 185: ベクトル化済みなのに現行タグを持たない人がいる間は照合しない（他タグの存在では止めない）
- necessities.model_tag: どのタグで作ったベクトルかを残し、別タグのものは照合に使わない
- POX_TEST_ALLOW_STUB は Render（本番）で立っていたら起動しない
"""
import json, os, subprocess, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import necessities as N
from db_v4 import MemoryStore
from embedding_config import MODEL_TAG
from necessity_gen import compute_gamma

TOKEN = "test-anchor-token"


def _vecs(x):
    return {k: list(x) for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query")}


@pytest.fixture
def v4(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setenv("POX_ANCHOR_TOKEN", TOKEN)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    store = MemoryStore()
    gamma = compute_gamma(0.6, 0.3)
    for pid, v, nec in (("u_k", [1.0, 0.0], "秘密の必要像K"), ("u_p", [0.8, 0.6], "秘密の必要像P")):
        store.save_profile(pid, fields={"will_text": "秘密の意志", "state_have": "秘密の現状"}, supporting_raw={},
                           supporting_redacted={}, pii_redaction_status="none", migrated_from=None,
                           generation_status="ready")
        store.save_necessity(pid, MODEL_TAG, {"necessity_text": nec, "gate_s": 0.6, "gate_u": 0.3,
                                              "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0,
                                              "gamma": gamma, "evidence_span": ""})
        store.save_vectors(pid, MODEL_TAG, _vecs(v))
    monkeypatch.setattr(appmod, "is_postgres", lambda: True)
    monkeypatch.setattr(appmod, "_v4_store", lambda: store)
    monkeypatch.setattr(appmod, "_linked_ids", lambda ids: set(ids))
    monkeypatch.setattr(appmod, "_matching_available", lambda: True)
    monkeypatch.setattr(appmod, "_seeker_live_necessity_id", lambda sid, db_path=None: None)
    import migrate_v4
    monkeypatch.setattr(migrate_v4, "ensure_migrated", lambda *a, **k: None)
    return store


# ── 一時的に開けていた監査・整理のルートは閉じた（指示書58 §2-4）────────────────────────
@pytest.mark.parametrize("method,url", [
    ("get", "/ledger/audit/match?pair=u_k,u_p"),
    ("get", "/ledger/audit/inventory"),
    ("post", "/ledger/admin/purge-accounts"),
])
def test_temporary_audit_routes_are_closed(v4, method, url):
    c = appmod.app.test_client()
    r = getattr(c, method)(url, headers={"X-Anchor-Token": TOKEN}, json={"apply": True})
    assert r.status_code == 404                                   # トークンがあっても存在しない
    assert not any(str(rule) in url.split("?")[0] for rule in appmod.app.url_map.iter_rules()
                   if str(rule).startswith(("/ledger/audit/match", "/ledger/audit/inventory",
                                            "/ledger/admin/purge")))


# ── 185 モデルの切替が済むまで照合しない ─────────────────────────────────────────
def _match(sid="u_k"):
    c = appmod.app.test_client()
    with c.session_transaction() as s:
        s["subject_id"] = sid
    return c.post("/v4/match", json={"seeker_id": sid}).get_json()


def test_t185_no_matching_while_someone_lacks_current_tag(v4):
    assert _match()["status"] == "ok"
    v4.save_vectors("u_old", "nomic-emb-v1", _vecs([1.0, 0.0]))            # 旧タグだけの人がいる
    assert v4.count_missing_tag(MODEL_TAG) == 1
    d = _match()
    assert d["status"] == "unavailable" and d["results"] == []


def test_t185_old_tag_rows_alone_do_not_stop_matching(v4):
    v4.save_vectors("u_k", "nomic-emb-v1", _vecs([1.0, 0.0]))              # 切替後も旧タグの行は残る
    v4.save_vectors("u_p", "nomic-emb-v1", _vecs([1.0, 0.0]))
    assert v4.count_missing_tag(MODEL_TAG) == 0
    assert _match()["status"] == "ok"                                         # 全員が現行タグを持てば照合する


def test_t185_postgres_query_counts_vectorized_only():
    import inspect, db_v4
    src = inspect.getsource(db_v4.PostgresStore.count_missing_tag)
    assert "NOT IN" in src and "model_tag=%s AND is_active=true" in src


# ── necessities.model_tag ────────────────────────────────────────────────────
def test_necessity_vectors_carry_model_tag():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    r = N.publish_necessity("u1", "subject", {"will_text": "w", "necessity_text": "n", "gate_s": 0.5,
                                              "gate_u": 0.3}, db_path=db)
    nid = r["necessity_id"]
    out = N.vectorize_necessity(nid, embed_fn=lambda t, role: [1.0, 0.0], db_path=db)
    assert out["model_tag"] == MODEL_TAG
    assert N.query_vectors(nid, db_path=db) is not None
    from db_connect import get_connection
    with get_connection(db) as con:
        con.execute("UPDATE necessities SET model_tag=%s WHERE necessity_id=%s", ("other-tag", nid))
    assert N.query_vectors(nid, db_path=db) is None                          # 別タグのベクトルは使わない
    with get_connection(db) as con:
        con.execute("UPDATE necessities SET model_tag=NULL WHERE necessity_id=%s", (nid,))
    assert N.query_vectors(nid, db_path=db) is not None                      # 列を足す前の行は現行扱い


# ── POX_TEST_ALLOW_STUB の本番拒否 ──────────────────────────────────────────────
def test_allow_stub_is_refused_on_render():
    env = {k: v for k, v in os.environ.items() if not k.startswith("POX_EMBED_") and k != "POX_DEBUG"}
    env.update({"RENDER": "true", "POX_TEST_ALLOW_STUB": "1", "POX_EMBED_BACKEND": "nomic",
                "POX_EMBED_MODEL_TAG": "nomic-emb-v2"})
    r = subprocess.run([sys.executable, "-c", "import app"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "POX_TEST_ALLOW_STUB はテスト専用" in (r.stderr + r.stdout)
