"""GET /ledger/audit/inventory — 埋め込みとアカウントの棚卸し（指示書55 段階0-2 E-1〜E-3・B-2・B-5）。

判定書 §5-1 の条件: 読み取りのみ・スコアを返さない・個人の履歴を横断して並べない。
認証は /ledger/audit/legacy-boundary と同じ（X-Anchor-Token。無し・不一致は 404）。
"""
import json, os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import auth
import display_names
from db_connect import get_connection
from ledger_events import append_event

TOKEN = "test-anchor-token"
URL = "/ledger/audit/inventory"


def _setup(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setenv("POX_ANCHOR_TOKEN", TOKEN)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    me, _ = auth.get_or_create_identity("kaoru@example.com", db_path=appmod.DB)
    display_names.set_display_name(me, "kaoru", db_path=appmod.DB)
    with get_connection(appmod.DB) as con:                   # 旧 v3 だけに残る id（auth 無し）
        con.execute("CREATE TABLE IF NOT EXISTS seekers (id TEXT PRIMARY KEY, seeker_json TEXT NOT NULL)")
        con.execute("INSERT INTO seekers (id, seeker_json) VALUES (%s, %s)", ("u_orphan", '{"意志":"秘密の本文"}'))
    append_event(me, "connection.established", {"a": me, "b": "u_other"}, db_path=appmod.DB)
    return me


def _get(token=TOKEN):
    headers = {"X-Anchor-Token": token} if token is not None else {}
    return appmod.app.test_client().get(URL, headers=headers)


def test_inventory_requires_anchor_token(monkeypatch):
    _setup(monkeypatch)
    assert _get(token=None).status_code == 404
    assert _get(token="wrong").status_code == 404
    assert _get(token=TOKEN).status_code == 200
    assert appmod.app.test_client().post(URL, headers={"X-Anchor-Token": TOKEN}).status_code == 405  # 読み取りのみ


def test_inventory_reports_embedding_resolution(monkeypatch):
    _setup(monkeypatch)
    e = _get().get_json()["embedding"]
    import embedding_config as EC
    assert e["backend"] == EC.BACKEND and e["model_tag"] == EC.MODEL_TAG and e["full_dim"] == EC.FULL_DIM
    assert set(e) >= {"backend_env_set", "model_tag_env_set", "column_dim", "endpoint_set"}
    assert all(isinstance(v, bool) for v in e["endpoint_set"].values())     # URL そのものは返さない


def test_inventory_accounts_and_ledger(monkeypatch):
    me = _setup(monkeypatch)
    d = _get().get_json()
    acc = {a["id"]: a for a in d["accounts"]}
    assert acc[me]["display_name"] == "kaoru" and acc[me]["in_auth"] and acc[me]["ledger_refs"] == 1
    assert acc["u_orphan"]["in_seekers_v3"] and not acc["u_orphan"]["in_auth"]
    assert acc["u_orphan"]["ledger_refs"] == 0
    assert d["accounts_without_auth"] == ["u_orphan"]
    assert d["ledger"]["connection_closed_count"] == 0
    assert d["ledger"]["connection_established_count"] == 1
    assert d["vectors"] is None and d["profiles_v4"] is None                # SQLite には v4 表が無い


def test_inventory_returns_no_body_or_score(monkeypatch):
    _setup(monkeypatch)
    blob = json.dumps(_get().get_json(), ensure_ascii=False)
    for k in ("秘密の本文", "score", "a_sim", "limiting_axis", "email", "seeker_json", "payload"):
        assert k not in blob, k


def test_inventory_writes_nothing(monkeypatch):
    _setup(monkeypatch)
    def snapshot():
        with get_connection(appmod.DB) as con:
            return [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                    for t in ("ledger_events", "auth_identities", "display_names", "seekers")]
    before = snapshot()
    _get(); _get()
    assert snapshot() == before
