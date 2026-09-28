"""GET /ledger/audit/legacy-boundary — 監査のエンドポイント（Render の Shell が使えないため）。

scripts/audit_legacy_boundary.py と同じ処理を HTTP から実行する。読み取りのみ。
認証は /ledger/anchor と同じ（POX_ANCHOR_TOKEN ／ X-Anchor-Token。無し・不一致は 404）。
"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import ledger_events as le
import talks

TOKEN = "test-anchor-token"
URL = "/ledger/audit/legacy-boundary"


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


def _setup(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setenv("POX_ANCHOR_TOKEN", TOKEN)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    cid = _cli("u_alice").post("/api/communities", json={"name": "n", "founder_id": "u_alice"}).get_json()["id"]
    tk = _cli("u_alice").post(f"/api/community/{cid}/talks",
                              json={"kind": "proposal", "title": "P", "target": {}}).get_json()["talk_id"]
    pid = _cli("u_alice").post(f"/api/talks/{tk}/posts", json={"body": "秘密の本文"}).get_json()["post_id"]
    _cli("u_alice").post(f"/api/talks/{tk}/vote", json={"stance": "approve"})
    ref = _cli().get(f"/api/talks/{tk}").get_json()["result"]["purpose_event_hash"]
    return tk, pid, ref


def _get(query="", token=TOKEN):
    headers = {"X-Anchor-Token": token} if token is not None else {}
    return _cli().get(URL + query, headers=headers)


def test_audit_requires_anchor_token(monkeypatch):
    _setup(monkeypatch)
    assert _get(token=None).status_code == 404                 # トークンなし
    assert _get(token="wrong").status_code == 404              # 不一致
    monkeypatch.delenv("POX_ANCHOR_TOKEN")
    assert _get(token="").status_code == 404                   # サーバー側が未設定
    assert _get(token=TOKEN).status_code == 404


def test_audit_clean_recommends_seq(monkeypatch):
    _setup(monkeypatch)
    r = _get("?boundary_at=2000-01-01T00:00:00.000Z")
    assert r.status_code == 200
    d = r.get_json()
    assert d["boundary_at"] == "2000-01-01T00:00:00.000Z"
    assert d["mismatch_count"] == 0 and d["mismatches"] == []
    assert d["recommended_seq"] == d["boundary_seq"] == 0
    assert d["checked"] >= 1
    # 既定の境界時刻は #101 の main 反映時刻
    assert _get().get_json()["boundary_at"] == "2026-09-23T08:49:23.000Z"


def test_audit_lists_mismatch_ids_without_text(monkeypatch):
    tk, pid, ref = _setup(monkeypatch)
    with talks._connect(appmod.DB) as con:
        con.execute("UPDATE talk_posts SET body=%s WHERE post_id=%s", ("改変された本文", pid))
    d = _get("?boundary_at=2000-01-01T00:00:00.000Z").get_json()
    assert d["mismatch_count"] == 1 and d["recommended_seq"] is None
    agreed = le.get_events(type_="purpose.agreed", db_path=appmod.DB)[-1]
    assert d["mismatches"] == [{"event_hash": ref, "seq": agreed["seq"]}]      # 合意の id と seq まで
    body = json.dumps(d, ensure_ascii=False)
    for leaked in ("秘密の本文", "改変された本文", tk, agreed["payload"]["discussion_hash"]):
        assert leaked not in body                              # 本文・トーク・ハッシュは返さない


def test_audit_is_read_only(monkeypatch):
    tk, pid, ref = _setup(monkeypatch)
    n = len(le.get_events(db_path=appmod.DB))
    posts = talks.get_posts(tk, db_path=appmod.DB)
    _get("?boundary_at=2000-01-01T00:00:00.000Z")
    assert len(le.get_events(db_path=appmod.DB)) == n
    assert talks.get_posts(tk, db_path=appmod.DB) == posts


def test_audit_matches_cli_script(monkeypatch):
    _setup(monkeypatch)
    from scripts.audit_legacy_boundary import run_audit
    r = run_audit("2000-01-01T00:00:00.000Z", db_path=appmod.DB)
    d = _get("?boundary_at=2000-01-01T00:00:00.000Z").get_json()
    assert (d["boundary_seq"], d["checked"], d["mismatch_count"]) == \
        (r["boundary_seq"], r["checked"], len(r["mismatches"]))
