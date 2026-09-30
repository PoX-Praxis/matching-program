"""指示書55-2 PR-D — ハンドル（一意・不変・表示名（@ハンドル）・/u/<ハンドル>）。

178 表示は「表示名（@ハンドル）」／179 一意・不変（重複と再設定を拒否）
188 プロフィールの URL に生 id が出ない（/u/<ハンドル>。旧 URL は移す）
189 例外の変更は一意性を保ち 1 回だけ・旧ハンドルは再利用されない
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import app as appmod
import display_names
import handles


def _cli(sid=None):
    c = appmod.app.test_client()
    if sid:
        with c.session_transaction() as s:
            s["subject_id"] = sid
    return c


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    return appmod.DB


# ── 179 一意・不変 ────────────────────────────────────────────────────────────
def test_t179_unique_and_immutable(db):
    assert handles.set_handle("u_a", "@Kaoru", db_path=db) == "kaoru"          # @ と大文字をそろえる
    with pytest.raises(handles.HandleTaken):
        handles.set_handle("u_b", "kaoru", db_path=db)                        # 重複
    with pytest.raises(handles.HandleImmutable):
        handles.set_handle("u_a", "kaoru2", db_path=db)                       # 再設定（不変）
    for bad in ("ab", "かおる", "a-b", "x" * 31, "admin"):
        with pytest.raises(handles.HandleError):
            handles.normalize(bad)


def test_t179_api_first_set_then_409(db):
    assert _cli().post("/api/my/handle", json={"handle": "alice"}).status_code == 401
    r = _cli("u_a").post("/api/my/handle", json={"handle": "alice"})
    assert r.status_code == 201 and r.get_json()["handle"] == "alice"
    assert _cli("u_a").post("/api/my/handle", json={"handle": "alice2"}).status_code == 409
    assert _cli("u_b").post("/api/my/handle", json={"handle": "alice"}).status_code == 409
    assert _cli("u_b").post("/api/my/handle", json={"handle": "a!"}).status_code == 400
    assert _cli("u_b").post("/api/my/handle", json={"id": "u_a", "handle": "bob"}).status_code == 403


def test_t179_handle_cannot_be_another_subject_id(db):
    import auth
    other, _ = auth.get_or_create_identity("x@example.com", db_path=db)
    with pytest.raises(handles.HandleTaken):
        handles.set_handle("u_me", other, db_path=db)                        # 他人の生 id と同じ文字列


def test_t179_confirm_requires_handle_for_new_users():
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    body = src[src.index("def confirm_draft("):src.index("drafts.set_status(draft_id, \"confirmed\"")]
    assert 'handles.check(cbody.get("handle")' in body and '"field": "handle"' in body
    reg = open(os.path.join(ROOT, "templates", "register.html"), encoding="utf-8").read()
    assert "後から変えられません" in reg and 'id="handleInput"' in reg      # 同意（確定）の前に見せる


def test_t179_draft_preview_suggests_handle_from_seeker_id(db):
    import drafts
    d = drafts.save_draft("u_new", {"id": "Alice_2025", "seeker": {"意志": "x"}}, owner_kind="subject", db_path=db)
    assert appmod._handle_hint(d) == {"needs_handle": True, "handle_suggestion": "alice_2025"}
    handles.set_handle("u_new", "alice_2025", db_path=db)
    assert appmod._handle_hint(d) == {"needs_handle": False}


# ── 178 表示は「表示名（@ハンドル）」────────────────────────────────────────────
def test_t178_label_is_name_with_handle(db):
    display_names.set_display_name("u_a", "カオル", db_path=db)
    handles.set_handle("u_a", "kaoru", db_path=db)
    handles.set_handle("u_b", "bob", db_path=db)
    names = appmod._resolve_names(["u_a", "u_b", "u_c"], fallback=appmod.UNNAMED_LABEL)
    assert names == {"u_a": "カオル（@kaoru）", "u_b": "@bob", "u_c": appmod.UNNAMED_LABEL}


def test_t178_api_profile_carries_handle(db, monkeypatch):
    monkeypatch.setattr(appmod, "get_profile_view", lambda i, db_path=None: {"headline": "h"})
    display_names.set_display_name("u_a", "カオル", db_path=db)
    handles.set_handle("u_a", "kaoru", db_path=db)
    d = _cli().get("/api/profile/u_a").get_json()
    assert d["display_name"] == "カオル" and d["handle"] == "kaoru"
    assert d["display_label"] == "カオル（@kaoru）"
    pvh = open(os.path.join(ROOT, "templates", "_profile_view.html"), encoding="utf-8").read()
    assert "handleEditor" in pvh and "後から変えられません" in pvh


# ── 188 URL に生 id を出さない ─────────────────────────────────────────────────
def test_t188_profile_url_uses_handle(db):
    handles.set_handle("u_9e8cd69c", "kaoru", db_path=db)
    r = _cli().get("/profile/u_9e8cd69c")
    assert r.status_code == 301 and r.headers["Location"].endswith("/u/kaoru")
    page = _cli().get("/u/kaoru")
    assert page.status_code == 200
    assert _cli().get("/u/nobody").status_code == 404
    tpl = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert "location.pathname" not in tpl                                       # 表示中の人はサーバーが渡す
    conn = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert "/u/${encodeURIComponent(handle)}" in conn


def test_t188_without_handle_stays_on_old_url(db):
    assert _cli().get("/profile/u_nohandle").status_code == 200                # 設定するまで表示名のみ


# ── 189 例外の変更は 1 回・旧ハンドルは再利用しない ────────────────────────────
def test_t189_exception_change_once_and_old_retired(db):
    handles.set_handle("u_a", "first", db_path=db)
    assert handles.change_handle_by_exception("u_a", "second", note="本人の申立て", db_path=db) == "second"
    assert handles.get_handle("u_a", db_path=db) == "second"
    with pytest.raises(handles.HandleImmutable):
        handles.change_handle_by_exception("u_a", "third", db_path=db)        # 2 回目は不可
    with pytest.raises(handles.HandleTaken):
        handles.set_handle("u_b", "first", db_path=db)                        # 旧ハンドルは再利用しない
    assert handles.subject_for("first", db_path=db) is None                    # 旧 URL は誰にも解決しない
    handles.set_handle("u_b", "bob", db_path=db)
    with pytest.raises(handles.HandleTaken):
        handles.change_handle_by_exception("u_b", "second", db_path=db)       # 一意性を保つ


def test_t189_change_is_recorded_not_shown(db):
    handles.set_handle("u_a", "first", db_path=db)
    handles.change_handle_by_exception("u_a", "second", db_path=db)
    from db_connect import get_connection
    with get_connection(db) as con:
        rows = con.execute("SELECT old_handle, new_handle FROM handle_changes WHERE subject_id=%s",
                           ("u_a",)).fetchall()
    assert [tuple(r) for r in rows] == [("first", "second")]
    rules = {r.rule for r in appmod.app.url_map.iter_rules()}
    assert not any("handle" in r and "change" in r for r in rules)             # 公開の変更 API は無い
