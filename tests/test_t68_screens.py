"""指示書68 §9-3 — 接続の説明の画面1〜3・補足・保存・フラグ・許可リスト・鍵付きのハッシュ（テスト 399〜408）。

固定のデータは tests/fixtures/explanation_v0_4.json の架空の人のカードを、world2 の人（me・c_full・c_z）に割り当てて使う。
KAORU・PA の文は使わない。数値は出さない。
"""
import copy
import json
import os
import re
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import explanation as X  # noqa: E402
import explanation_store as S  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401

KEY = "k" * 40
FIX = os.path.join(ROOT, "tests", "fixtures", "explanation_v0_4.json")


def _cases():
    with open(FIX, encoding="utf-8") as f:
        return {c["case"]: c for c in json.load(f)["cases"]}


def _write(doc):
    path = os.path.join(tempfile.mkdtemp(), "explanation_cards.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False)
    S.clear_cache()
    return path


def _pair_doc(viewer="me", target="c_full", case="1_complement"):
    """架空の 1 組のカード（見る人 × 相手）を、world2 の人に割り当てる（カードの形）。"""
    c = _cases()[case]
    return {"people": {c["viewer"]: {"subject": viewer}, c["target"]: {"subject": target}},
            "cards": [{"viewer": c["viewer"], "target": c["target"], "card": c["card"]}]}


@pytest.fixture
def xs(world2, monkeypatch):
    """フラグ allowlist・許可リスト me+c_full・鍵あり・固定のデータ（架空の 1 組）。"""
    import display_names
    for sid, name in (("me", "アオイ"), ("c_full", "ハル"), ("c_z", "ソラ")):
        display_names.set_display_name(sid, name, db_path=appmod.DB)
    monkeypatch.setenv("POX_EXPLANATION", "allowlist")
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "me+c_full")
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", KEY)
    monkeypatch.delenv("POX_EXPLANATION_DIGEST_KEY_PREV", raising=False)
    path = _write(_pair_doc())
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", path)
    yield path
    S.clear_cache()


def _card(sid, other):
    return _cli(sid).get(f"/api/explanations/card?with={other}").get_json()


def _offer(digest=True, sid="me", other="c_full"):
    d = _card(sid, other)
    body = {"to_id": other, "channel": "profile"}
    if digest:
        body["explanation_digest"] = d.get("card_digest") if digest is True else digest
    return _cli(sid).post("/approve", json=body)


def _state(a, b):
    return _cli(a).get(f"/api/connections/state?with={b}").get_json()["state"]


# ── 399 §7-6・§7-5: 画面2 で読んだ表示を保存し、画面3 はそのまま（名前だけ）。確認のチェックが無いと承認できない ─────
def test_t399_screen2_saves_and_screen3_needs_confirmation(xs):
    d = _card("me", "c_full")
    assert d["status"] == "ok" and "あなた" in d["html"] and "ハル さん" in d["html"] and d["target_name"] == "ハル"
    assert re.search(r"⟦[VT]⟧", d["html"]) is None
    assert _offer(digest="0" * 64).status_code == 409                     # 読んだ版と違えば申請しない
    assert _state("me", "c_full") == "none"
    assert _offer().status_code == 200 and _state("me", "c_full") == "pending_out"
    row = S.pending_render("me", "c_full", db_path=appmod.DB)
    assert row["viewer_html"] == d["html"] and row["card_digest"] == d["card_digest"]
    assert S.verify_digest(row["viewer_html"], row["viewer_digest"], row["viewer_key_id"])
    inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()
    assert inc["status"] == "ok" and inc["applicant_name"] == "アオイ"
    assert inc["html"] == X.personalize(row["symbolic_html"], "recipient", {"viewer": "アオイ", "target": "ハル"})
    assert "あなた" not in inc["html"] and "アオイ さん" in inc["html"] and "ハル さん" in inc["html"]
    # データが後から変わっても、画面3 は保存した表示のまま（再生成しない）
    doc = _pair_doc()
    doc["cards"][0]["card"]["purposes"][0]["meaning"]["text"] = "{O}は、別の説明で{N}の店に関われる可能性があります。"
    os.environ["POX_EXPLANATION_CARDS_PATH"] = _write(doc)
    assert _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"] == inc["html"]
    # 確認のチェックが無いと承認できない
    r = _cli("c_full").post("/approve", json={"to_id": "me"})
    assert r.status_code == 400 and r.get_json()["error"] == "explanation_unconfirmed"
    assert _state("c_full", "me") == "pending_in"
    r = _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True})
    assert r.status_code == 200 and _state("me", "c_full") == "connected"
    done = S.approved_render("me", "c_full", db_path=appmod.DB)
    assert done["status"] == "approved" and done["recipient_digest"] and done["recipient_key_id"] == done["viewer_key_id"]
    assert S.verify_digest(inc["html"], done["recipient_digest"], done["recipient_key_id"])


# ── 400 §7-6: 見送りは確認なしでできる ──────────────────────────────────────────────
def test_t400_decline_without_confirmation(xs):
    assert _offer().status_code == 200
    r = _cli("c_full").post("/api/connections/decline", json={"to_id": "me"})
    assert r.status_code == 200 and _state("me", "c_full") == "declined_out"


# ── 401 §7-7: 補足つきの承認は保存され、接続の画面に出る。読む人ごとの呼び方 ─────────────────────
def test_t401_approve_supplement_shown_after_connected(xs):
    assert _offer().status_code == 200
    long = "あ" * 401
    r = _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True, "supplement": long})
    assert r.status_code == 400 and _state("c_full", "me") == "pending_in"
    r = _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True,
                                              "supplement": "在庫の記録は飲食店の分だけです"})
    assert r.status_code == 200
    a, b = _card("me", "c_full"), _card("c_full", "me")
    assert a["status"] == b["status"] == "approved"
    assert "在庫の記録は飲食店の分だけです" in a["html"] and "在庫の記録は飲食店の分だけです" in b["html"]
    assert "ハル さんの補足" in a["html"] and "あなた" in a["html"]           # 申請した人には、読んだときの呼び方
    assert "ハル さんの補足" in b["html"] and "あなた" not in b["html"]       # 承認した人には、名前だけ（画面3 と同じ）
    assert "ex-supp-btn" not in a["html"] and X.FOOTER_TEXT in a["html"]   # 承認の後は「補足する」を置かない


# ── 402 §7-7・§7-8: 補足つきの見送りは申請した人に届き、届けた後に残らない。表示も消える ──────────────────
def test_t402_decline_supplement_delivered_once_and_render_deleted(xs):
    assert _offer().status_code == 200
    assert S.count_rows(db_path=appmod.DB)["explanation_renders"] == 1
    r = _cli("c_full").post("/api/connections/decline", json={"to_id": "me", "supplement": "いまは店の仕事に手が回りません"})
    assert r.status_code == 200
    assert S.count_rows(db_path=appmod.DB)["explanation_renders"] == 0
    notes = _cli("me").get("/api/explanations/declined_notes").get_json()
    assert notes == [{"from_name": "ハル", "text": "いまは店の仕事に手が回りません", "at": notes[0]["at"]}]
    assert _cli("me").get("/api/explanations/declined_notes").get_json() == []
    assert _cli("c_full").get("/api/explanations/declined_notes").get_json() == []
    assert S.count_rows(db_path=appmod.DB) == {"explanation_renders": 0, "explanation_supplements": 0}


# ── 403 §7-8: 取り下げで、保存した表示が消える。画面1 の補足は運営だけ（相手に届かない）────────────────────
def test_t403_withdraw_deletes_render_and_self_supplement_stays_private(xs):
    assert _offer().status_code == 200
    assert _cli("me").post("/api/connections/withdraw", json={"to_id": "c_full"}).get_json()["withdrawn"] is True
    assert S.count_rows(db_path=appmod.DB)["explanation_renders"] == 0
    assert _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["status"] == "none"
    r = _cli("me").post("/api/explanations/supplement", json={"to_id": "c_full", "text": "店は二つあります"})
    assert r.status_code == 200 and S.count_rows(db_path=appmod.DB)["explanation_supplements"] == 1
    assert "店は二つあります" not in json.dumps(_card("c_full", "me"), ensure_ascii=False)
    assert _cli("c_full").get("/api/explanations/declined_notes").get_json() == []
    assert _cli("me").post("/api/explanations/supplement", json={"to_id": "c_full", "text": "あ" * 401}).status_code == 400
    assert _cli("me").post("/api/explanations/supplement", json={"to_id": "c_z", "text": "x"}).status_code == 404


# ── 404 §7-9: render_digest は鍵付き。allowlist・on で鍵が無いと起動しない。鍵の識別子と、ひとつ前の鍵 ──────────
def test_t404_digest_key_required_and_rotation(monkeypatch):
    monkeypatch.delenv("POX_DEBUG", raising=False)
    monkeypatch.delenv("POX_EXPLANATION_DIGEST_KEY", raising=False)
    for m in ("allowlist", "on"):
        monkeypatch.setenv("POX_EXPLANATION", m)
        with pytest.raises(SystemExit):
            S.startup_check()
        monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", "short")
        with pytest.raises(SystemExit):
            S.startup_check()
        monkeypatch.delenv("POX_EXPLANATION_DIGEST_KEY")
    monkeypatch.setenv("POX_EXPLANATION", "off")
    assert S.startup_check()["key"] == "なし"                              # off なら鍵なしで起動する
    with pytest.raises(S.KeyMissing):
        S.render_digest("<p>x</p>")
    monkeypatch.setenv("POX_EXPLANATION", "allowlist")
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", KEY)
    st = S.startup_check()
    assert st == {"mode": "allowlist", "key": "設定あり", "key_prev": "なし"} and KEY not in json.dumps(st)
    d1, k1 = S.render_digest("<p>x</p>")
    assert re.fullmatch(r"[0-9a-f]{64}", d1) and re.fullmatch(r"[0-9a-f]{8}", k1) and KEY not in k1
    assert d1 != __import__("hashlib").sha256(b"<p>x</p>").hexdigest()     # 鍵の無いハッシュではない
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", "n" * 40)              # 鍵を替える
    d2, k2 = S.render_digest("<p>x</p>")
    assert d2 != d1 and k2 != k1
    assert not S.verify_digest("<p>x</p>", d1, k1)                          # 古い記録は、ひとつ前の鍵が無いと確かめられない
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY_PREV", KEY)
    assert S.verify_digest("<p>x</p>", d1, k1) and S.verify_digest("<p>x</p>", d2, k2)
    assert not S.verify_digest("<p>y</p>", d1, k1)
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    assert "_xs.startup_check()" in src                                     # 起動のときに確かめる


# ── 405 §7-10: off（既定）では今と同じ ──────────────────────────────────────────────
def test_t405_off_is_unchanged(xs, monkeypatch):
    monkeypatch.delenv("POX_EXPLANATION")
    assert S.mode() == "off"
    assert _card("me", "c_full") == {"status": "off"}
    assert _cli("me").get("/api/explanations/list").get_json()["status"] == "off"
    assert _cli("me").post("/approve", json={"to_id": "c_full"}).status_code == 200       # 版を添えなくても申請できる
    assert _cli("c_full").post("/approve", json={"to_id": "me"}).status_code == 200       # チェックなしで承認できる
    assert _state("me", "c_full") == "connected"
    assert S.count_rows(db_path=appmod.DB) == {"explanation_renders": 0, "explanation_supplements": 0}
    monkeypatch.setenv("POX_EXPLANATION", "unknown-value")                  # 知らない値は off
    assert S.mode() == "off"
    html = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert "あなたの必要から見た、この人" in html and "loadPreview();" in html     # off のときは、いまの欄を出す


# ── 406 §7-11: allowlist で、許可リストの組以外には出ない（@ハンドルでも書ける・形の違う組は閉じる）────────────────
def test_t406_allowlist_only(xs, monkeypatch):
    doc = _pair_doc()
    other = _pair_doc(viewer="me", target="c_z", case="2_resonance")
    doc["people"].update({"minato": {"subject": "c_z"}})
    doc["cards"] += other["cards"]
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", _write(doc))
    assert _card("me", "c_full")["status"] == "ok" and _card("c_full", "me")["status"] == "ok"   # どちら向きでも
    assert _card("me", "c_z") == {"status": "off"} and _card("c_z", "me") == {"status": "off"}
    assert [x["other"] for x in _cli("me").get("/api/explanations/list").get_json()["items"]] == ["c_full"]
    import handles
    handles.set_handle("c_z", "sora_z", db_path=appmod.DB)
    monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", "me+@sora_z")
    assert _card("me", "c_z")["status"] == "ok" and _card("me", "c_full") == {"status": "off"}
    for bad in ("", "me", "me+", "me+@nobody_here", "me+me", "me c_full", "@@+me"):
        monkeypatch.setenv("POX_EXPLANATION_ALLOWLIST", bad)
        assert not S.enabled_for("me", "c_full", db_path=appmod.DB) and not S.enabled_for("me", "c_z", db_path=appmod.DB), bad
    monkeypatch.setenv("POX_EXPLANATION", "on")
    assert _card("me", "c_z")["status"] == "ok"
    assert _card("c_z", "c_full") == {"status": "none"}                    # on でもデータの無い組は出さない（準備中と出さない）
    # 形の違うファイル・読めないファイルは誰にも出さない
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", _write({"cards": [{"viewer": "aoi", "target": "haru", "card": {"x": 1}}]}))
    assert _card("me", "c_full") == {"status": "none"}
    bad = os.path.join(tempfile.mkdtemp(), "broken.json")
    open(bad, "w").write("{")
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", bad)
    S.clear_cache()
    assert _card("me", "c_full") == {"status": "none"}


# ── 407 §7-12・§2-2: 一覧は 0 件・1 件・多件（10 件ずつ・並びは中立）。説明の無い相手は出さない ─────────────────
def test_t407_list_zero_one_many(xs, monkeypatch):
    monkeypatch.setenv("POX_EXPLANATION", "on")
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", _write({"cards": []}))
    d = _cli("me").get("/api/explanations/list").get_json()
    assert d == {"status": "ok", "items": [], "page": 0, "has_prev": False, "has_next": False}
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", _write(_pair_doc()))
    d = _cli("me").get("/api/explanations/list").get_json()
    assert len(d["items"]) == 1 and d["items"][0]["name"] == "ハル" and not d["has_next"]
    it = d["items"][0]["items"][0]
    assert it["head"].startswith("あなたの目的1") and it["meaning"].endswith("可能性があります。") and "ハル さん" in it["meaning"]
    c = _cases()["1_complement"]["card"]
    sparse = _cases()["3_sparse"]["card"]
    people = {"aoi": {"subject": "me"}}
    cards = []
    for i in range(12):
        people[f"p{i:02d}"] = {"subject": f"p{i:02d}"}
        cards.append({"viewer": "aoi", "target": f"p{i:02d}", "card": c})
    people["pz"] = {"subject": "pz"}
    pend = copy.deepcopy(c)
    for p in pend["purposes"] + pend["reverse_purposes"]:
        p["status"] = "pending"
        p["meaning"] = None
    cards.insert(3, {"viewer": "aoi", "target": "pz", "card": pend})          # 説明の無い相手（pending）
    people["ps"] = {"subject": "ps"}
    cards.append({"viewer": "aoi", "target": "ps", "card": sparse})           # 逆の向きだけの説明（疎）
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", _write({"people": people, "cards": cards}))
    p0 = _cli("me").get("/api/explanations/list?page=0").get_json()
    p1 = _cli("me").get("/api/explanations/list?page=1").get_json()
    ids = [x["other"] for x in p0["items"] + p1["items"]]
    assert len(p0["items"]) == 10 and p0["has_next"] and not p0["has_prev"]
    assert len(p1["items"]) == 3 and p1["has_prev"] and not p1["has_next"]
    assert ids == [f"p{i:02d}" for i in range(12)] + ["ps"]                  # データの順（中立）・pending は出さない
    assert p1["items"][-1]["items"][0]["head"].startswith("表示名未設定のアカウント さんの目的")   # 逆の向きの説明
    blob = json.dumps(p0, ensure_ascii=False)
    for k in ("準備中", '"g"', "score", "count", "total"):
        assert k not in blob, k
    html = open(os.path.join(ROOT, "templates", "connect.html"), encoding="utf-8").read()
    assert 'id="explainList"' in html and "/api/explanations/list?page=" in html and "PoXExplain.listItemHtml" in html


# ── 408 §7-12・§1-2・§4-6: 状態（stale・補足・deleted）で崩れない・向きのレコードから組み立てる・v4 の相手の見出し ─────────
def test_t408_states_directions_and_v4_heading(xs, monkeypatch):
    cases = _cases()
    for name, cs in cases.items():
        fwd, rev = X.split_card(cs["card"])
        card = X.assemble(fwd, rev, cs["card"]["target_has_purposes"])
        assert X.validate(card)[0], name
        assert card["purposes"] == cs["card"]["purposes"] and card["reverse_purposes"] == cs["card"]["reverse_purposes"]
        assert sorted(map(json.dumps, card["citations"])) == sorted(map(json.dumps, cs["card"]["citations"]))
        assert card["output_digest"] == X.output_digest(card)
    # 向きのレコードの形のファイルでも、同じカードが出る（A の目的 × B の文 は、A の本体と B の「A さんから見た B さん」の両方に使う）
    c = cases["1_complement"]["card"]
    fwd, rev = X.split_card(c)
    doc = {"format": S.FILE_FORMAT, "people": {"aoi": {"subject": "me"}, "haru": {"subject": "c_full"}},
           "directions": [{"need": "aoi", "offer": "haru", **fwd}, {"need": "haru", "offer": "aoi", **rev}]}
    monkeypatch.setenv("POX_EXPLANATION_CARDS_PATH", _write(doc))
    a = S.card_for("me", "c_full", db_path=appmod.DB)
    b = S.card_for("c_full", "me", db_path=appmod.DB)
    assert a["purposes"] == c["purposes"] and b["reverse_purposes"] == _swap(c["purposes"])
    # v4 の相手（目的が無い）: 逆の向きの見出しは「〇〇さんが求めている相手『…』について」
    v4 = copy.deepcopy(c)
    v4["target_has_purposes"] = False
    h = X.render(v4, "viewer", {"viewer": "アオイ", "target": "ハル"})
    assert "■ ハル さんが求めている相手『作ったものを使い続けてもらいたい』について" in h
    assert "ハル さんの目的1「作ったもの" not in h and "── ハル さんが求めている相手 ──" in h
    # 状態: stale・補足あり・deleted は崩れない（承認済みの表示に補足を足しても、末尾の注記の前に入る）
    st = X.render(cases["4_stale_supplemented"]["card"], "viewer", {"viewer": "アオイ", "target": "ハル"})
    assert "承認時の説明" in st and "の補足" in st and st.endswith("</section>")
    dl = copy.deepcopy(c)
    dl["status"] = "deleted"
    assert X.render(dl, "recipient", {"viewer": "アオイ", "target": "ハル"}).count(X.DELETED_TEXT) == 1
    sup = X.supplements_html([{"by": "target", "text": "<b>x</b>", "at": "2026-11-10"}])
    assert "&lt;b&gt;x&lt;/b&gt;" in sup and "⟦T⟧の補足" in sup
    # 画面の部品: 画面3 の確認のチェックと注記・「内容を確認して申請する」・補足の欄
    inbox = open(os.path.join(ROOT, "templates", "inbox.html"), encoding="utf-8").read()
    prof = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert "内容を確認しました（違う点があれば補足してください）" in inbox and "申請の時に読んだ内容です（申請の時点のプロフィールにもとづきます）" in inbox
    assert "explanation_confirmed" in inbox and "/api/explanations/declined_notes" in inbox
    assert "内容を確認して申請する" in prof and "explanation_digest" in prof and "/api/explanations/supplement" in prof


def _swap(purposes):
    out = json.loads(json.dumps(purposes))
    for p in out:
        p["ref"] = p["ref"].replace("viewer.", "target.", 1)
        for e in p["elements"]:
            e["need_ref"] = e["need_ref"].replace("viewer.", "target.", 1)
            e["offer_ref"] = e["offer_ref"].replace("target.", "viewer.", 1)
        for u in p["unknown"]:
            u["need_ref"] = u["need_ref"].replace("viewer.", "target.", 1)
        p["missing_must"] = [m.replace("viewer.", "target.", 1) for m in p["missing_must"]]
    return out
