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
    # 指示書70 §4-2: マイページの切り替えは外した（構造化の答え「説明への使用」を使う。§5 は rev5 の実装の後）。
    # 既定は使わない。切り替えの道（/api/my/explanation_settings）は無い。
    assert appmod._explanation_bound_allowed("me") is False
    assert _cli("me").get("/api/my/explanation_settings").status_code == 404
    assert _cli("me").post("/api/my/explanation_settings", json={"bound_allowed": True}).status_code == 404
    assert appmod._explanation_bound_allowed("me") is False
    html = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "接続の説明に使ってよい" not in html and "saveBound" not in html


# ── 428 §4-4: 目的の種類（仮の規則で生成の入力に使う。指示書70 §4-2: 選ぶ道と表示は外した）────────────────────
def test_t428_purpose_kinds(xs, monkeypatch):
    assert _cli("me").post("/api/my/explanation_settings", json={"purpose_kinds": {}}).status_code == 404
    person = appmod._explanation_person("me")
    assert [p["kind"] for p in person["purposes"]] == [None, None]              # 本人が選んだ値は無い
    import explanation_gen as G
    assert [p["種類"] for p in G.need_side(person)["block"]["purposes"]] == ["補完", "補完"]   # 仮の規則（関心の型の要素が無い）
    for f in ("mypage.html", "profile.html", "inbox.html", "connect.html"):    # 本人にも相手にも出さない（指示書70 §4-2）
        html = open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()
        assert "仮（" not in html and "saveKind" not in html and "目的の種類" not in html, f


# ── 429 §4-5 → 指示書70 §4-3: 説明の同意はポリシーの版に一本化（terms.accepted）・取り込み分は同意の前でも出る ──────────
def test_t429_explanation_consent(xs):
    assert _card("me", "c_full")["status"] == "ok"                              # 取り込み分は同意の前でも出す
    assert appmod._explanation_consented("me") is False
    # 指示書69 の説明だけの同意（2026-11-expl-v1）は、ポリシーの版の同意に数えない（記録は消さない）
    appmod.append_event("me", "terms.accepted", {"subject_id": "me", "document": "explanation",
                                                 "terms_version": "2026-11-expl-v1", "terms_hash": "x"}, db_path=appmod.DB)
    assert appmod._explanation_consented("me") is False
    assert _cli("me").post("/api/my/explanation_consent", json={"agree": True}).status_code == 404
    assert _cli("me").post("/api/my/policy_consent", json={}).status_code == 400
    r = _cli("me").post("/api/my/policy_consent", json={"agree": True}).get_json()
    assert r == {"consented": True, "recorded": True, "terms_version": appmod.TERMS_VERSION}
    assert _cli("me").post("/api/my/policy_consent", json={"agree": True}).get_json()["recorded"] is False
    ev = [e["payload"] for e in le.get_events(type_="terms.accepted", actor="me", db_path=appmod.DB)]
    assert len(ev) == 2 and ev[0]["document"] == "explanation"                 # 過去の記録として残る
    p = ev[1]
    assert "document" not in p and p["terms_version"] == "2026-11"
    priv = open(os.path.join(ROOT, "templates", "privacy.html"), encoding="utf-8").read()
    assert p["terms_hash"] == hashlib.sha256(priv.encode("utf-8")).hexdigest()
    assert appmod._explanation_consented("me") is True and appmod._explanation_consented("c_full") is False
    for t in ("6-2. 接続の説明（AI による説明）について", "第三者への提供には当たりません", "外部の AI のサービスは使いません",
              "公開の機能は準備中", "モデルとプロンプトの版を記録します", "2026年11月の版のこのポリシーに同意した方に適用されます",
              "構造化のときに、あなたが使ってよいと答えた場合だけ使います"):
        assert t in priv, t
    assert not os.path.exists(os.path.join(ROOT, "templates", "_explanation_consent.html"))
    for f in ("register.html", "mypage.html"):
        assert "_explanation_consent" not in open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()


# ── 430 §4-6: 「非公開にする」の切り替えの値を記録する（台帳には入れない）───────────────────────────────
def test_t430_private_switch_recorded(xs):
    _agree(applicant_private=False, recipient_private=True)
    row = S.render_by_id(S.approved_render("me", "c_full", db_path=appmod.DB)["id"], db_path=appmod.DB)
    assert row["applicant_private"] == 0 and row["recipient_private"] == 1
    assert "private" not in json.dumps(_agreed()[0]["payload"])
    for f, key in (("profile.html", "explanation_private"), ("inbox.html", "explanation_private")):
        html = open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()
        assert key in html and "この接続の内容を非公開にする" in html and "公開の機能は準備中" in html
