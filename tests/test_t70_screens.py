"""指示書70 §4（画面の整理）— テスト 434〜437（§6 の 4〜7）。

434 マイページに、必須・種類・縛られているものの切り替えと、説明の同意の欄が無い（必須は表示だけ）
435 既存の利用者に、ポリシー更新の同意が一度だけ出る。同意すると terms.accepted に版が記録され、もう出ない
436 つながるに「照合の結果」の節が無く、「つながる意味が見つかった相手」の見出しと新しい冒頭の説明が出る
437 登録者一覧が、表示名（とハンドル）と目的の見出しだけになる
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import auth  # noqa: E402
import ledger_events as le  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401


def TPL(name):
    return open(os.path.join(ROOT, "templates", name), encoding="utf-8").read()


# ── 434（§6-4）マイページ: 切り替えと説明の同意の欄が無い。必須は表示だけ。構造化し直す入口は残る ──────────────────
def test_t434_mypage_has_no_switches(world2):
    html = TPL("mypage.html")
    for gone in ("saveMusts", "askMusts", 'type="radio"', "MUST_TOO_MANY",                  # 欠かせない要素の切り替え（65B-7）
                 "saveKind", "目的の種類", "仮（選び直せます）",                              # 目的の種類（69 §4-4）
                 "saveBound", "接続の説明に使ってよい",                                      # 縛られているもの（69 §4-3）
                 "agreeExplanation", "_explanation_consent", "explanation_settings", "explainSettings"):   # 説明の同意（69 §4-5）
        assert gone not in html, gone
    assert "変えたいときは、構造化し直してください。" in html                                # 必須は表示だけ
    assert 'href="/register">再構造化する →</a>' in html                                    # 構造化し直す入口は今のまま
    # サーバーの道も無い（表示の GET は残す）
    me = _cli("me")
    assert me.post("/api/my/musts", json={"purposes": []}).status_code == 405
    assert me.get("/api/my/explanation_settings").status_code == 404
    assert me.post("/api/my/explanation_settings", json={}).status_code == 404
    assert me.post("/api/my/explanation_consent", json={"agree": True}).status_code == 404
    d = me.get("/api/my/musts").get_json()
    assert [[n["must"] for n in p["needs"]] for p in d["purposes"]] == [[True, False], [False]]
    assert not os.path.exists(os.path.join(ROOT, "templates", "_explanation_consent.html"))


# ── 435（§6-5）既存の利用者に、ポリシー更新の同意が一度だけ出る。同意すると版が記録され、もう出ない ───────────────────
def _login(c, email):
    return c.get(f"/auth/verify?token={auth.issue_token(email, db_path=appmod.DB)}", follow_redirects=False)


def _terms(sid):
    return [e["payload"] for e in le.get_events(type_="terms.accepted", actor=sid, db_path=appmod.DB)]


def test_t435_policy_update_once(db, monkeypatch):
    # 新しく来た人: 初回のログインでいまの版を記録する。更新の同意は出ない
    c_new = appmod.app.test_client()
    r = _login(c_new, "new@example.com")
    assert r.status_code == 302 and r.headers["Location"].startswith("/mypage")
    # すでに登録している人（前の版 2026-09 に同意して登録した）
    monkeypatch.setattr(appmod, "TERMS_VERSION", "2026-09")
    c = appmod.app.test_client()
    _login(c, "kaoru@example.com")
    with c.session_transaction() as s:
        sid = s["subject_id"]
    assert [t["terms_version"] for t in _terms(sid)] == ["2026-09"]
    monkeypatch.setattr(appmod, "TERMS_VERSION", "2026-11")
    # 次のログインで、一度だけ更新の同意を出す（戻り先を保つ）
    r = _login(c, "kaoru@example.com")
    assert r.status_code == 302 and r.headers["Location"].startswith("/policy-update?next=")
    page = c.get(r.headers["Location"])
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "プライバシーポリシーが更新されました（接続の説明について）。" in body and ">同意する</button>" in body
    assert 'href="/mypage?id=' in body                                            # 「あとで」でも先へ進める
    assert c.get(f"/mypage?id={sid}").status_code == 200                           # 同じセッションでは、もう出ない
    assert appmod._explanation_consented(sid) is False                             # 同意するまで説明は作らない
    # セッションが続いていて、ログインが来ない人にも、マイページで一度だけ出す
    c2 = _cli(sid)
    r = c2.get(f"/mypage?id={sid}")
    assert r.status_code == 302 and r.headers["Location"].startswith("/policy-update?next=%2Fmypage")
    c2.get(r.headers["Location"])
    assert c2.get(f"/mypage?id={sid}").status_code == 200
    # 同意する → terms.accepted にポリシーの版（と本文のハッシュ）。もう出ない
    assert c.post("/api/my/policy_consent", json={"agree": True}).get_json()["recorded"] is True
    t = _terms(sid)
    assert [x["terms_version"] for x in t] == ["2026-09", "2026-11"] and "document" not in t[-1]
    assert t[-1]["terms_hash"] == appmod._terms_hash()
    assert appmod._explanation_consented(sid) is True
    r = _login(c, "kaoru@example.com")
    assert r.headers["Location"].startswith("/mypage")
    assert _cli(sid).get(f"/mypage?id={sid}").status_code == 200
    assert c.get("/policy-update?next=/mypage").status_code == 302                # 同意済みなら先へ
    # 外への戻り先は受け付けない
    assert appmod._safe_next("//evil.example") is None
    # 未ログインは出さない（先へ）
    assert appmod.app.test_client().get("/policy-update").status_code == 302


def test_t435b_register_single_consent():
    html = appmod.app.test_client().get("/register").get_data(as_text=True)
    assert html.count('type="checkbox" id="policyAgree"') == 1
    assert "利用規約・プライバシーポリシー（接続の説明を含む）</a>に同意する" in html
    assert "explainAgree" not in html and "explanation_consent_agreed" not in html
    assert f'const PRIVACY_POLICY_VERSION = "{appmod.TERMS_VERSION}";' in html     # 版は app.TERMS_VERSION の 1 か所
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    confirm = src[src.index("# 登録時の同意は 1 つ"):src.index("if new_handle:", src.index("# 登録時の同意は 1 つ"))]
    assert "_record_policy_consent(pid)" in confirm                                # 登録の確定で版を記録する


# ── 436（§6-6）つながる: 照合の結果の節が無く、新しい見出しと冒頭の説明 ─────────────────────────────────────
def test_t436_connect_page(db):
    html = appmod.app.test_client().get("/connect").get_data(as_text=True)
    assert "あなたの目的と、相手の公開の文から、つながる意味が見つかった相手を示します。詳細と申請は、各プロフィールから。" in html
    assert "<h2>つながる意味が見つかった相手</h2>" in html
    for gone in ("照合の結果", "照合中", "/v4/match", "loadRecommendations", "RELATED_ZERO_TEXT", "説明のある相手",
                 "あなたの目的ごとの必要像から、いま噛み合いそうな相手を照合します。"):
        assert gone not in html, gone
    assert "申し出が届いている相手は、ここではなく" in html and '<a href="/inbox">受信箱</a>' in html   # 受信箱への案内は残す
    assert "/api/explanations/list" in html


# ── 437（§6-7）登録者一覧: 表示名（@ハンドル）と目的の見出しだけ。v4 の人は一行紹介だけ ─────────────────────────────
def test_t437_directory_rows(world2, monkeypatch):
    monkeypatch.setattr(appmod, "list_directory_ids", lambda db_path=None: ["c_full", "v4p"])
    rows = {r["id"]: r for r in appmod.app.test_client().get("/seekers").get_json()}
    assert rows["c_full"]["dests"] and rows["c_full"]["dests"] == [p["dest"] for p in rows["c_full"]["purposes"]]
    assert rows["v4p"]["dests"] == []                                              # 目的が無い人（v4）
    html = TPL("connect.html")
    body = html[html.index("function dirBody(r)"):html.index("/* つながる意味が見つかった相手")]
    assert "r.dests" in body and "r.one_liner" in body
    for gone in ("r.will", "r.necessity", "r.offers", "needsHtml", "offersHtml", "profileRows", "目指していること",
                 "必要としている相手", "力になれること"):
        assert gone not in body, gone
    assert 'href="${profileHref(r.id, r.handle)}">${escHtml(r.name)}</a>' in body   # 表示名（@ハンドル）。生 id は出さない
    assert 'id="nameFilter"' in html and "String(r.name || \"\").toLowerCase().includes(q)" in body   # 絞り込みは残す
    assert "sort(" not in body                                                     # 並びはサーバーの順のまま
