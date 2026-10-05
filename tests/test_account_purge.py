"""指示書55-3 §2 — アカウント整理（対象 4 件固定・dry-run 既定・台帳参照の再確認・1 トランザクション）。"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import auth
from db_connect import get_connection
from ledger_events import append_event
from scripts.purge_accounts import PURGE_IDS, run_purge

TOKEN = "test-anchor-token"
URL = "/ledger/admin/purge-accounts"   # 指示書58 §2-4 で閉じた。処理は CLI（run_purge）で検証する


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.setenv("POX_ANCHOR_TOKEN", TOKEN)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    with get_connection(appmod.DB) as con:
        con.execute("CREATE TABLE IF NOT EXISTS seekers (id TEXT PRIMARY KEY, seeker_json TEXT NOT NULL)")
        for i in ("kaoru", "smoke_test", "u_keep"):
            con.execute("INSERT INTO seekers (id, seeker_json) VALUES (%s, %s)", (i, "{}"))
    append_event("u_keep", "member.joined", {"x": 1}, db_path=appmod.DB)       # 台帳を用意
    return appmod.DB


def _seekers(db):
    with get_connection(db) as con:
        return sorted(r[0] for r in con.execute("SELECT id FROM seekers").fetchall())


class _R:
    def __init__(self, d):
        self._d = d

    def get_json(self):
        return self._d


def _post(body=None):
    """閉じる前のルートと同じ解釈（明示の true だけが実行）で run_purge を呼ぶ。"""
    return _R(run_purge(apply=(body or {}).get("apply") is True, db_path=appmod.DB))


def test_targets_are_fixed_to_four():
    assert PURGE_IDS == ("kaoru", "smoke_test", "u_5672a380", "u_9fa00efa")


def test_http_route_is_closed(db):
    for m in ("get", "post"):
        assert getattr(appmod.app.test_client(), m)(URL, headers={"X-Anchor-Token": TOKEN}).status_code == 404


def test_dry_run_by_default_deletes_nothing(db):
    d = _post({"ids": ["u_keep"]}).get_json()                      # 任意の id は受け付けない（無視）
    assert d["apply"] is False and d["deleted_ids"] == []
    rows = {a["id"]: a["rows"] for a in d["accounts"]}
    assert rows["kaoru"] == {"seekers.id": 1}
    assert _seekers(db) == ["kaoru", "smoke_test", "u_keep"]
    assert _post({"apply": "yes"}).get_json()["apply"] is False    # 明示の true だけが実行


def test_apply_deletes_only_unreferenced_targets(db):
    append_event("u_keep", "connection.established", {"a": "smoke_test", "b": "u_keep"}, db_path=db)
    d = _post({"apply": True}).get_json()
    assert d["deleted_ids"] == ["kaoru"]                           # smoke_test は台帳参照があるので消さない
    blocked = {a["id"]: a["blocked"] for a in d["accounts"]}
    assert "台帳の参照" in blocked["smoke_test"]
    assert _seekers(db) == ["smoke_test", "u_keep"]                # 対象外の id には触らない
    assert d["seekers_v3_remaining"] == 2


def test_linked_account_is_never_deleted(db, monkeypatch):
    import scripts.purge_accounts as P
    monkeypatch.setattr(P, "PURGE_IDS", ("u_real",))
    uid, _ = auth.get_or_create_identity("real@example.com", db_path=db)
    with get_connection(db) as con:
        con.execute("INSERT INTO seekers (id, seeker_json) VALUES (%s, %s)", (uid, "{}"))
    monkeypatch.setattr(P, "PURGE_IDS", (uid,))
    d = run_purge(apply=True, db_path=db)
    assert d["deleted_ids"] == [] and "紐づいている" in d["accounts"][0]["blocked"]


def test_ledger_is_untouched(db):
    import ledger_events as le
    before = le.get_events(db_path=db)
    _post({"apply": True})
    assert le.get_events(db_path=db) == before and le.verify_chain(db_path=db)["ok"] is True
