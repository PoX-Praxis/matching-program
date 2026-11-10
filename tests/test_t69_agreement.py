"""指示書69 §4 — 合意の記録・削除・「縛られているもの」の許可・目的の種類・同意・公開の切り替え（テスト 423〜430）。

KAORU の確認（2026-11-10）: 接続のイベントは変えない（案 B）・generation_digest・redaction.recorded に説明の種類・
日次のアンカーに含める・目的の種類は仮の規則と別の表・説明の同意は別の版。合成データは架空の人だけ。
"""
import hashlib
import hmac
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import explanation as X  # noqa: E402
import explanation_agreement as XA  # noqa: E402
import explanation_store as S  # noqa: E402
import ledger_events as le  # noqa: E402
from canon import canonicalize  # noqa: E402
from test_t61_resonance_gate import _cli, db  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401
from test_t68_screens import KEY, _card, _offer, _state, xs  # noqa: E402,F401


def _agree(supplement=None, applicant_private=False, recipient_private=False):
    d = _card("me", "c_full")
    r = _cli("me").post("/approve", json={"to_id": "c_full", "explanation_digest": d["card_digest"],
                                          "explanation_private": applicant_private})
    assert r.status_code == 200
    body = {"to_id": "me", "explanation_confirmed": True, "explanation_private": recipient_private}
    if supplement:
        body["supplement"] = supplement
    assert _cli("c_full").post("/approve", json=body).status_code == 200
    assert _state("me", "c_full") == "connected"


def _agreed():
    return le.get_events(type_=XA.EVENT_TYPE, db_path=appmod.DB)


# ── 423 §7-13: 合意のイベントは、両方の確認がそろったときだけ書く。① と句は入らない。接続のイベントは変えない（案 B）───
def test_t423_agreed_only_when_both_confirmed(xs):
    assert _offer().status_code == 200
    assert _agreed() == []                                                      # 申請だけでは書かない
    assert _cli("c_full").post("/api/connections/decline", json={"to_id": "me"}).status_code == 200
    assert _agreed() == []                                                      # 見送りは書かない
    with S._connect(appmod.DB) as con:
        con.execute("DELETE FROM connection_requests")
    _agree(supplement="週末だけ手伝えます")
    ev = _agreed()
    assert len(ev) == 1
    p = ev[0]["payload"]
    conn = le.get_events(type_="connection.established", db_path=appmod.DB)[-1]
    assert p["connection_ref"] == conn["event_hash"] and ev[0]["actor"] == "c_full"
    assert "explanation" not in json.dumps(conn["payload"], ensure_ascii=False)  # 接続のイベントは変えない
    assert [a["by"] for a in p["assents"]] == ["me", "c_full"] and all(a["render_digest"] for a in p["assents"])
    assert p["supplemented"] is True and p["canon_version"] == "a1" and p["direction"] == {"need": "me", "offer": "c_full"}
    assert len(p["pair_digest"]) == 64 and len(p["digest_key_id"]) == 8 and len(p["generation_digest"]) == 64
    assert "generator" not in p and "model" not in json.dumps(p)
    card = json.loads(S.render_by_id(S.approved_render("me", "c_full", db_path=appmod.DB)["id"], db_path=appmod.DB)["card_json"])
    blob = json.dumps(p, ensure_ascii=False)
    for pur in card["purposes"]:
        if pur["meaning"]:
            assert pur["meaning"]["text"] not in blob                           # ① は入れない
        for e in pur["elements"]:
            assert e["need_quote"] not in blob and e["offer_quote"] not in blob  # 句はそのまま入れない
    assert "週末だけ手伝えます" not in blob                                      # 補足の本文も入れない


# ── 424 §4-2 (1)(2): pair_digest は a1 の HMAC。句のハッシュも HMAC。generation_digest はモデル・revision・sha ─────
def test_t424_pair_digest_is_keyed(xs, monkeypatch):
    _agree()
    p = _agreed()[0]["payload"]
    row = S.approved_render("me", "c_full", db_path=appmod.DB)
    card = json.loads(S.render_by_id(row["id"], db_path=appmod.DB)["card_json"])
    a1 = XA.a1_object(card, "me", "c_full")
    assert a1["purposes"] and all(pair["relation"] in X.RELATIONS for pur in a1["purposes"] for pair in pur["pairs"])
    want = hmac.new(KEY.encode(), canonicalize(a1), hashlib.sha256).hexdigest()
    assert p["pair_digest"] == want and row["pair_digest"] == want and row["agreed_event_hash"] == _agreed()[0]["event_hash"]
    q = card["purposes"][0]["elements"][0]["need_quote"]
    assert a1["purposes"][0]["pairs"][0]["need_quote_h"] != hashlib.sha256(q.encode()).hexdigest()   # 素のハッシュではない
    monkeypatch.setenv("POX_EXPLANATION_DIGEST_KEY", "z" * 40)
    assert XA.pair_digest(a1)[0] != want                                        # 鍵が違えば違う
    g = card["generation"]
    assert p["generation_digest"] == hashlib.sha256(canonicalize(
        {"model": g["model"], "revision": g["revision"], "prompt1_sha": g["prompt1_sha"], "prompt2_sha": g["prompt2_sha"]})).hexdigest()


# ── 425 §4-2 (5): 削除で本文・①・表示・補足が消え、合意のイベントは残る。redaction.recorded に説明の種類 ─────────
def test_t425_delete_keeps_hash_and_records_redaction(xs):
    _agree(supplement="週末だけ手伝えます")
    agreed = _agreed()[0]
    dry = XA.delete_for_subject("c_full", db_path=appmod.DB)
    assert dry["apply"] is False and dry["renders"] == 1 and dry["supplements"] >= 1
    assert S.count_rows(db_path=appmod.DB)["explanation_supplements"] >= 1        # 数えるだけでは消さない
    res = XA.delete_for_subject("c_full", reason_class="subject_request", decided_by="ops", apply=True, db_path=appmod.DB)
    assert res["redactions"] == 1
    row = S.render_by_id(S.approved_render("me", "c_full", db_path=appmod.DB)["id"], db_path=appmod.DB)
    assert row["deleted_at"] and row["symbolic_html"] == "" and row["viewer_html"] == "" and row["card_json"] is None
    assert row["pair_digest"] == agreed["payload"]["pair_digest"]                # ハッシュは残る
    assert S.count_rows(db_path=appmod.DB)["explanation_supplements"] == 0
    with S._connect(appmod.DB) as con:
        assert con.execute("SELECT COUNT(*) FROM explanation_directions WHERE need_subject='c_full' OR offer_subject='c_full'"
                           ).fetchone()[0] == 0
    assert _agreed() == [agreed]                                                 # 台帳のイベントは残る
    red = le.get_events(type_="redaction.recorded", db_path=appmod.DB)[-1]["payload"]
    assert red["hash_kind"] == "explanation" and red["target_ref"] == agreed["event_hash"]
    assert red["prev_hash"] == agreed["payload"]["pair_digest"] and red["reason_class"] == "subject_request"
    d = _card("me", "c_full")
    assert d["status"] == "approved" and X.DELETED_TEXT in d["html"] and "ハル" not in d["html"]
    assert le.verify_chain(db_path=appmod.DB)["ok"]
    with pytest.raises(ValueError):
        XA.record_redaction(agreed["event_hash"], reason_class="whim", decided_by="ops", db_path=appmod.DB)


# ── 426 §4-2 (7): 日次のアンカーに、合意のイベントも含まれる ─────────────────────────────────────
def test_t426_anchor_includes_agreed(xs):
    import anchor
    _agree()
    ev = _agreed()[0]
    day = ev["at"][:10]
    r = anchor.compute_root_for_date(day, db_path=appmod.DB)
    hashes = [e["event_hash"] for e in le.get_events(db_path=appmod.DB) if (e["at"] or "").startswith(day)
              and e["type"] != "anchor.published"]
    assert ev["event_hash"] in hashes and r["event_count"] == len(hashes)
    assert r["root"] == anchor.merkle_root(sorted(hashes))


# ── 427 §4-3: 「縛られているもの」の許可（既定は使わない・版を記録・変えたら作り直しのきっかけ）─────────────────────
def test_t427_bound_permission(xs, monkeypatch):
    calls = []
    monkeypatch.setattr(appmod, "_explanation_trigger", lambda sid: calls.append(sid))
    d = _cli("me").get("/api/my/explanation_settings").get_json()
    assert d["bound_allowed"] is False and d["bound_version"] == 0
    assert appmod._explanation_bound_allowed("me") is False
    assert _cli("me").post("/api/my/explanation_settings", json={"bound_allowed": True}).get_json() == {"changed": True}
    assert appmod._explanation_bound_allowed("me") is True and S.bound_setting("me", db_path=appmod.DB) == (True, 1)
    assert _cli("me").post("/api/my/explanation_settings", json={"bound_allowed": True}).get_json() == {"changed": False}
    assert _cli("me").post("/api/my/explanation_settings", json={"bound_allowed": False}).get_json() == {"changed": True}
    assert S.bound_setting("me", db_path=appmod.DB) == (False, 2) and calls == ["me", "me"]
    assert _cli("me").post("/api/my/explanation_settings", json={"bound_allowed": "yes"}).status_code == 400
    html = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "現状の「縛られているもの」を、接続の説明に使ってよい（初期設定は使いません）" in html


# ── 428 §4-4: 目的の種類（仮の規則・本人が選び直す・「仮」はマイページだけ・生成の入力に使う）────────────────────
def test_t428_purpose_kinds(xs, monkeypatch):
    calls = []
    monkeypatch.setattr(appmod, "_explanation_trigger", lambda sid: calls.append(sid))
    d = _cli("me").get("/api/my/explanation_settings").get_json()
    ps = d["purposes"]
    assert [p["kind"] for p in ps] == ["補完", "補完"] and all(p["provisional"] for p in ps)   # 仮の規則（関心の型の要素が無い）
    pid = ps[1]["purpose_id"]
    assert _cli("me").post("/api/my/explanation_settings", json={"purpose_kinds": {pid: "共鳴"}}).get_json() == {"changed": True}
    ps2 = _cli("me").get("/api/my/explanation_settings").get_json()["purposes"]
    assert ps2[1]["kind"] == "共鳴" and ps2[1]["provisional"] is False and ps2[0]["provisional"] is True
    assert calls == ["me"]
    for bad in ({pid: "その他"}, {"other_purpose": "補完"}, ["補完"]):
        assert _cli("me").post("/api/my/explanation_settings", json={"purpose_kinds": bad}).status_code == 400
    person = appmod._explanation_person("me")
    assert {p["purpose_id"]: p["kind"] for p in person["purposes"]}[pid] == "共鳴"
    import explanation_gen as G
    assert G.need_side(person)["block"]["purposes"][1]["種類"] == "共鳴"
    assert "仮（選び直せます）" in open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    for f in ("profile.html", "inbox.html", "connect.html"):                    # 相手の画面には「仮」を出さない
        assert "仮（" not in open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()


# ── 429 §4-5: 説明の同意は別の版（terms.accepted に文書の種類と版）・取り込み分は同意の前でも出る ──────────────────
def test_t429_explanation_consent(xs):
    assert _card("me", "c_full")["status"] == "ok"                              # 取り込み分は同意の前でも出す
    assert appmod._explanation_consented("me") is False
    assert _cli("me").post("/api/my/explanation_consent", json={}).status_code == 400
    r = _cli("me").post("/api/my/explanation_consent", json={"agree": True}).get_json()
    assert r == {"consented": True, "recorded": True, "consent_version": "2026-11-expl-v1"}
    assert _cli("me").post("/api/my/explanation_consent", json={"agree": True}).get_json()["recorded"] is False
    ev = [e for e in le.get_events(type_="terms.accepted", actor="me", db_path=appmod.DB)]
    assert len(ev) == 1
    p = ev[0]["payload"]
    assert p["document"] == "explanation" and p["terms_version"] == "2026-11-expl-v1"
    consent = open(os.path.join(ROOT, "templates", "_explanation_consent.html"), encoding="utf-8").read()
    assert p["terms_hash"] == hashlib.sha256(consent.encode("utf-8")).hexdigest()
    assert appmod._explanation_consented("me") is True and appmod._explanation_consented("c_full") is False
    assert _cli("me").get("/api/my/explanation_settings").get_json()["consented"] is True
    # 文言（案のまま）・公開の機能は準備中・ポリシーの節・登録とマイページに入っている
    for t in ("引用は、あなたの文章から一字も変えずに抜き出します。", "（公開の機能は準備中）", "初期設定は使いません",
              "AI の要約の文は記録に入れません。", "台帳には、内容を復元できない記録だけが残ります。"):
        assert t in consent, t
    priv = open(os.path.join(ROOT, "templates", "privacy.html"), encoding="utf-8").read()
    for t in ("6-2. 接続の説明（AI による説明）について", "第三者への提供には当たりません", "外部の AI のサービスは使いません",
              "公開の機能は準備中", "モデルとプロンプトの版を記録します"):
        assert t in priv, t
    for f in ("register.html", "mypage.html"):
        assert '{% include "_explanation_consent.html" %}' in open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()


# ── 430 §4-6: 「非公開にする」の切り替えの値を記録する（台帳には入れない）───────────────────────────────
def test_t430_private_switch_recorded(xs):
    _agree(applicant_private=False, recipient_private=True)
    row = S.render_by_id(S.approved_render("me", "c_full", db_path=appmod.DB)["id"], db_path=appmod.DB)
    assert row["applicant_private"] == 0 and row["recipient_private"] == 1
    assert "private" not in json.dumps(_agreed()[0]["payload"])
    for f, key in (("profile.html", "explanation_private"), ("inbox.html", "explanation_private")):
        html = open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()
        assert key in html and "この接続の内容を非公開にする" in html and "公開の機能は準備中" in html
