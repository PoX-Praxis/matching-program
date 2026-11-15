"""指示書74 順3 — 承認までのやり取り（§3）と、報告74 の確認（2026-11-15）。テスト 471〜479。

471（§8-8）やり取りを、申請者と受けた人が書けて、互いに見える。当事者でない人は読めず、書けない
472（§8-9）接続が成立した後は、やり取りに足せない。申請が無い相手には書けない
473（§8-10）承認の後、やり取りが承認時の説明と一緒に読める。見送り・取り下げ・退会で本文が消える。見送りの補足は残す（確認 2）
474（§8-11）やり取りの本文が、台帳・pair_digest・render_digest・ログに入らない
475（§8-12）1 通 400 字を超えると弾く。1 人 10 通の上限を超えると弾く。制御文字・ゼロ幅文字を除く
476 supplemented: 受けた人の発言が 1 通以上あるとき true（申請者だけなら false）
477 新しいやり取りの印（受信箱の一覧・マイページ）。読むと消える。件数は出さない
478 承認の後の表示は、見る人の側を先に（確認 7）。文は変わらない
479 ポリシー 2026-11b（6-2 の差し替え・非公開の時期）・申し出の文はやり取りの先頭・非公開のチェックは画面から外した
480（追補2）承認画面の「申請の時に読んだ内容です」を消し、二つの側を別々の枠に。注記は枠の外に 1 回（新旧の申請・承認の後）
481（追補2）確認のチェックの文・更新のお知らせの文・やり取りの日付（「11月15日」）
"""
import json
import os

import pytest
import re
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "src")]

import app as appmod  # noqa: E402
import explanation as X  # noqa: E402
import explanation_agreement as XA  # noqa: E402
import explanation_store as S  # noqa: E402
import ledger_events as le  # noqa: E402
from test_t61_resonance_gate import _cli  # noqa: E402
from test_t68_screens import _card, _offer, _state, xs  # noqa: E402,F401
from test_t65b2_two_layer import world2  # noqa: E402,F401
from test_t61_resonance_gate import db  # noqa: E402,F401

NAMES = {"viewer": "アオイ", "target": "ハル"}


def _get(sid, other):
    return _cli(sid).get(f"/api/explanations/exchange?with={other}").get_json()


def _post(sid, other, text):
    return _cli(sid).post("/api/explanations/exchange", json={"to_id": other, "text": text})


def _approve():
    r = _cli("c_full").post("/approve", json={"to_id": "me", "explanation_confirmed": True})
    assert r.status_code == 200 and _state("me", "c_full") == "connected"


def _texts(h):
    return Counter(t.strip() for t in re.split(r"<[^>]+>", h) if t.strip())


# ── 471（§8-8）当事者の二人が書けて、互いに見える。当事者でない人は読めない・書けない ─────────────────────────
def test_t471_both_parties_post_and_see(xs, monkeypatch):
    assert _get("me", "c_full")["status"] == "none"                                  # 申請の前は無い
    assert _offer().status_code == 200
    a = _get("me", "c_full")
    assert a["status"] == "open" and a["items"] == [] and a["can_post"] is True and a["remaining"] == 10
    assert _post("me", "c_full", "店の帳簿は、紙で付けています。").status_code == 201        # 申請者
    assert _post("c_full", "me", "帳簿の形式を教えてもらえますか。").status_code == 201      # 受けた人
    for sid, other in (("me", "c_full"), ("c_full", "me")):
        items = _get(sid, other)["items"]
        assert [x["text"] for x in items] == ["店の帳簿は、紙で付けています。", "帳簿の形式を教えてもらえますか。"]
        assert [x["name"] for x in items] == ["アオイ", "ハル"]                         # 呼び方はユーザー名
        assert [x["mine"] for x in items] == ([True, False] if sid == "me" else [False, True])
        assert all(re.fullmatch(r"\d{1,2}月\d{1,2}日", x["at"]) for x in items)          # 日付は「11月15日」の形（追補2）
    # 当事者でない人（c_z）: 説明が出ていない組なので off。説明を全員に出しても、二人の間の申請は見えない
    assert _get("c_z", "me")["status"] == "off" and _post("c_z", "me", "x").status_code == 404
    monkeypatch.setenv("POX_EXPLANATION", "on")
    assert _get("c_z", "me")["status"] == "none" and _get("c_z", "c_full")["status"] == "none"
    assert _post("c_z", "me", "横から").status_code == 409 and _post("c_z", "c_full", "横から").status_code == 409
    assert "横から" not in json.dumps(_get("me", "c_full"), ensure_ascii=False)


# ── 472（§8-9）成立の後は足せない・申請が無い相手には書けない ─────────────────────────────────────────
def test_t472_closed_after_connected_and_no_request(xs):
    r = _post("me", "c_full", "申請の前")
    assert r.status_code == 409 and r.get_json()["message"] == appmod.EXCHANGE_CLOSED_MESSAGE
    assert _offer().status_code == 200
    assert _post("c_full", "me", "週末なら話せます").status_code == 201
    _approve()
    d = _get("me", "c_full")
    assert d["status"] == "closed" and d["can_post"] is False and [x["text"] for x in d["items"]] == ["週末なら話せます"]
    assert _post("me", "c_full", "成立の後").status_code == 409
    assert _post("c_full", "me", "成立の後").status_code == 409
    assert [x["text"] for x in _get("c_full", "me")["items"]] == ["週末なら話せます"]


# ── 473（§8-10）承認の後は残る・見送り・取り下げ・退会で消える。見送りの補足は残す ─────────────────────────
def test_t473_kept_on_approval_and_erased_otherwise(xs):
    # 見送り: やり取りは消える。見送りに添えた補足は、申請者に一度だけ届く（報告74 の確認 2）
    assert _offer().status_code == 200
    assert _post("me", "c_full", "見送りで消える文").status_code == 201
    r = _cli("c_full").post("/api/connections/decline", json={"to_id": "me", "supplement": "いまは手が回りません"})
    assert r.status_code == 200
    assert S.count_rows(db_path=appmod.DB)["explanation_exchanges"] == 0
    notes = _cli("me").get("/api/explanations/declined_notes").get_json()
    assert [n["text"] for n in notes] == ["いまは手が回りません"]
    assert _cli("me").get("/api/explanations/declined_notes").get_json() == []          # 一度だけ
    inbox = open(os.path.join(ROOT, "templates", "inbox.html"), encoding="utf-8").read()
    assert 'class="ib-decline-note"' in inbox and "申請した人に一度だけ届けて、消します" in inbox
    # 取り下げ: やり取りは消える
    with S._connect(appmod.DB) as con:
        con.execute("DELETE FROM connection_requests")
    assert _offer().status_code == 200
    assert _post("c_full", "me", "取り下げで消える文").status_code == 201
    assert _cli("me").post("/api/connections/withdraw", json={"to_id": "c_full"}).get_json()["withdrawn"] is True
    assert S.count_rows(db_path=appmod.DB)["explanation_exchanges"] == 0
    # 承認: 残り、承認時の説明が出る場所で読める
    assert _offer().status_code == 200
    assert _post("c_full", "me", "承認で残る文").status_code == 201
    _approve()
    assert _get("me", "c_full")["status"] == "closed"
    prof = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert 'id="exchangeBox"' in prof and 'PoXExplain.loadExchange($("exchangeBox"), profileId)' in prof
    # 退会・削除の求め: 本文を消す（承認の後も。いまの補足と同じ）
    res = XA.delete_for_subject("me", apply=True, db_path=appmod.DB)
    assert res["apply"] is True
    assert S.count_rows(db_path=appmod.DB)["explanation_exchanges"] == 0
    assert _get("c_full", "me")["status"] == "none"


# ── 474（§8-11）本文は台帳・pair_digest・render_digest・ログに入らない ─────────────────────────────────
def test_t474_text_not_in_ledger_digests_or_logs(xs, capsys, caplog):
    assert _offer().status_code == 200
    inc = _cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]
    secret = "やり取りだけに書いた文・七三二一"
    assert _post("c_full", "me", secret).status_code == 201
    assert _post("me", "c_full", secret + "の返事").status_code == 201
    _approve()
    out = capsys.readouterr()
    assert secret not in out.out and secret not in out.err and secret not in caplog.text   # ログに出さない
    events = le.get_events(db_path=appmod.DB)
    assert events and secret not in json.dumps(events, ensure_ascii=False)                # 台帳に入れない
    row = S.approved_render("me", "c_full", db_path=appmod.DB)
    assert secret not in json.dumps(row, ensure_ascii=False, default=str)                   # pair_digest・a1・表示の行に入らない
    assert S.verify_digest(inc, row["recipient_digest"], row["recipient_key_id"])          # render_digest は見た説明だけ


# ── 475（§8-12）400 字・10 通・入力の手当て ────────────────────────────────────────────────
def test_t475_length_limit_and_cleaning(xs):
    assert _offer().status_code == 200
    assert _post("me", "c_full", "あ" * 401).status_code == 400
    assert _post("me", "c_full", "あ" * 400).status_code == 201
    assert _post("me", "c_full", "  ​\x07 ").status_code == 400                         # 手当ての後に空
    assert _post("me", "c_full", "帳​簿の\x07話\n二行目").status_code == 201
    assert _get("me", "c_full")["items"][-1]["text"] == "帳簿の話\n二行目"                   # 制御文字・ゼロ幅文字を除く（改行は残す）
    for i in range(8):
        assert _post("me", "c_full", f"{i}").status_code == 201
    d = _get("me", "c_full")
    assert d["remaining"] == 0 and d["can_post"] is False
    r = _post("me", "c_full", "11 通目")
    assert r.status_code == 400 and "10 通まで" in r.get_json()["error"]
    assert _post("c_full", "me", "相手は別に数える").status_code == 201                       # 上限は 1 人ずつ


# ── 476 supplemented ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("writer, expect", [("me", False), ("c_full", True)])
def test_t476_supplemented_means_recipient_posted(xs, writer, expect):
    assert _offer().status_code == 200
    other = "c_full" if writer == "me" else "me"
    assert _post(writer, other, "やり取りに書いた").status_code == 201                       # 申請者だけ／受けた人
    _approve()
    ev = le.get_events(type_=XA.EVENT_TYPE, db_path=appmod.DB)
    assert ev[-1]["payload"]["supplemented"] is expect
    assert set(ev[-1]["payload"]) == {"connection_ref", "pair_digest", "digest_key_id", "canon_version",
                                      "relation_rule_version", "spec_version", "direction", "assents", "supplemented",
                                      "generation_digest"}                                     # 形は変えない


# ── 477 新しいやり取りの印 ────────────────────────────────────────────────────────────
def test_t477_new_exchange_marks(xs):
    assert _offer().status_code == 200
    offers = _cli("c_full").get("/api/my/offers").get_json()
    assert offers and offers[0]["new_exchange"] is False
    assert _post("me", "c_full", "読んでください").status_code == 201
    assert _cli("c_full").get("/api/my/offers").get_json()[0]["new_exchange"] is True      # 受信箱の一覧の印
    assert _cli("c_full").get("/api/explanations/exchange_unread").get_json() == {"from": ["me"]}
    _get("c_full", "me")                                                                    # 読む
    assert _cli("c_full").get("/api/my/offers").get_json()[0]["new_exchange"] is False
    assert _post("c_full", "me", "返事です").status_code == 201
    assert _cli("me").get("/api/explanations/exchange_unread").get_json() == {"from": ["c_full"]}   # マイページの印
    inbox = open(os.path.join(ROOT, "templates", "inbox.html"), encoding="utf-8").read()
    my = open(os.path.join(ROOT, "templates", "mypage.html"), encoding="utf-8").read()
    assert "o.new_exchange" in inbox and "新しいやり取り" in inbox
    assert "/api/explanations/exchange_unread" in my and "新しいやり取りがあります" in my


# ── 478 承認の後の表示は、見る人の側を先に（確認 7）────────────────────────────────────────────
def test_t478_approved_view_viewer_side_first(xs):
    assert _offer().status_code == 200
    sym = S.pending_render("me", "c_full", db_path=appmod.DB)["symbolic_html"]
    _approve()
    a = _card("me", "c_full")                                                              # 申請者
    b = _card("c_full", "me")                                                              # 受けた人
    assert a["status"] == b["status"] == "approved"
    assert a["html"].index('class="ex-side ex-side-main"') < a["html"].index('class="ex-side ex-side-rev"')
    assert b["html"].index('class="ex-side ex-side-rev"') < b["html"].index('class="ex-side ex-side-main"')
    assert "あなた（申請した人）の目的から見た、ハル さん" in a["html"]
    assert "ハル さん（受けた人）の目的から見た、アオイ さん" in b["html"] and "あなた" not in b["html"]
    for html, reader in ((a["html"], "viewer"), (b["html"], "recipient")):               # 文は変わらない（並べ直すだけ）
        saved = X.personalize(sym.replace(X.SUPPLEMENT_BUTTON, ""), reader, NAMES)
        added, removed = _texts(html) - _texts(saved), _texts(saved) - _texts(html)
        assert all("目的から見た" in t or "つながる意味があります" in t for t in added), added
        assert all(t.startswith("──") and "から見た" in t for t in removed), removed


# ── 479 ポリシー 2026-11b・申し出の文・非公開のチェック ─────────────────────────────────────────────
def test_t479_policy_offer_message_and_private_switch(xs):
    assert appmod.TERMS_VERSION == "2026-11b"
    pol = open(os.path.join(ROOT, "templates", "privacy.html"), encoding="utf-8").read()
    for line in ("申請の取り下げ・見送りのときは、表示した説明と、承認までのやり取りを残しません（見送りに添えた補足は、"
                 "申請した人に一度だけ届けた後に消します）。承認された説明と、承認までのやり取りは、接続が続く間、保存します。",
                 "公開の機能ができたときに、接続のページから、どちらか一人でも非公開にできます。",
                 "説明の違う点や聞きたいことは、申請から承認までの間、申請した人と受けた人の二人の「やり取り」で伝えられます。"
                 "やり取りは二人にだけ表示し、公開しません。合意の記録にも入れません。退会・削除を求めた場合、"
                 "説明の本文とやり取りは削除します。"):
        assert line in pol, line[:20]
    assert "説明の「補足する」から伝えられます" not in pol
    assert "版 2026-11b" in open(os.path.join(ROOT, "templates", "policy_update.html"), encoding="utf-8").read()
    # 申し出の文（ひとこと）は、やり取りの先頭に出る（保存の場所は変えない）
    d = _card("me", "c_full")
    r = _cli("me").post("/approve", json={"to_id": "c_full", "channel": "profile", "message": "よろしくお願いします",
                                          "explanation_digest": d["card_digest"]})
    assert r.status_code == 200
    got = _get("c_full", "me")
    assert [(o["name"], o["text"]) for o in got["offer_messages"]] == [("アオイ", "よろしくお願いします")]
    # 非公開のチェックは画面から外した（列は残す）
    for f in ("profile.html", "inbox.html"):
        assert "ex-private" not in open(os.path.join(ROOT, "templates", f), encoding="utf-8").read()
    assert S.pending_render("me", "c_full", db_path=appmod.DB)["applicant_private"] == 0


# ── 480（追補2）承認画面の 1 行を消し、二つの側を別々の枠に。注記は枠の外に 1 回 ─────────────────────────────
def test_t480_detail_sides_in_separate_boxes(xs):
    inbox = open(os.path.join(ROOT, "templates", "inbox.html"), encoding="utf-8").read()
    assert "申請の時に読んだ内容です" not in inbox and "ex-cap" not in inbox
    assert _offer().status_code == 200
    from test_t74_inbox_detail import _forms
    shown = [_cli("c_full").get("/api/explanations/incoming?with=me").get_json()["html"]]   # 新しく保存した申請
    shown += [X.incoming_html(f["mixed"], NAMES) for f in _forms().values()]               # 前に保存した申請（追補1 の形）
    _approve()
    shown += [_card("me", "c_full")["html"], _card("c_full", "me")["html"]]                # 承認の後の表示（二人とも）
    for h in shown:
        assert 'data-layout="' in h
        head_end = h.index(">") + 1
        nodes = X._top_nodes(h[head_end:-len("</section>")])
        sides = [n for n in nodes if n.startswith('<div class="ex-side ')]
        assert len(sides) == 2                                                                 # 側ごとに別々の枠
        assert all(s.count('<div class="ex-side-h">') == 1 and s.index('<div class="ex-side-h">') < 40 for s in sides)
        foots = [n for n in nodes if n.startswith('<p class="ex-foot">')]
        assert len(foots) == 1 and h.count('class="ex-foot"') == 1                             # 注記は枠の外に 1 回
        assert nodes.index(foots[0]) > max(nodes.index(s) for s in sides)                     # 枠の下
    css = open(os.path.join(ROOT, "templates", "_explanation.html"), encoding="utf-8").read()
    assert ".ex-side + .ex-side" not in css                                                    # 側の間の点線の区切りはやめた（画面1 の ⑥ の線はそのまま）
    rev = re.search(r"\.ex-side-rev > \.ex-side-h \{ background: (#[0-9a-f]+)", css).group(1)
    main = re.search(r"\.ex-side-main > \.ex-side-h \{ background: (#[0-9a-f]+)", css).group(1)
    assert rev != main                                                                         # 帯の色を変える（見出しの文も出す）
    assert ".ex-card[data-layout] { border: none" in css                                       # 画面1（data-layout が無い）は変えない
    v = _card("me", "c_z") if False else None                                                  # noqa: F841（画面1 は data-layout を持たない）
    from test_t68_screens import _cases
    assert 'data-layout' not in (X.render(_cases()["1_complement"]["card"], "viewer", NAMES) or "")


# ── 481（追補2）確認のチェック・お知らせ・日付 ───────────────────────────────────────────────
def test_t481_check_label_notice_and_dates(xs):
    inbox = open(os.path.join(ROOT, "templates", "inbox.html"), encoding="utf-8").read()
    assert "内容を確認しました（違う点は、やり取りで伝えられます）" in inbox and "違う点があれば補足してください" not in inbox
    pu = open(os.path.join(ROOT, "templates", "policy_update.html"), encoding="utf-8").read()
    for line in ("プライバシーポリシーの「6-2. 接続の説明（AI による説明）について」を改めました（版 2026-11b）。",
                 "変わったのは 2 点です。",
                 "<li>申請から承認までの「やり取り」を保存します。承認されたときは残り、見送り・取り下げのときは消えます。</li>",
                 "<li>接続の内容を非公開にできる時期を、公開の機能ができたときに、接続のページから、に改めました。</li>"):
        assert line in pu, line[:20]
    assert appmod.TERMS_VERSION == "2026-11b"                                                  # 版とポリシーの本文は変えない
    d = _card("me", "c_full")
    assert _cli("me").post("/approve", json={"to_id": "c_full", "channel": "profile", "message": "はじめまして",
                                             "explanation_digest": d["card_digest"]}).status_code == 200
    assert _post("me", "c_full", "日付の確かめ").status_code == 201
    got = _get("c_full", "me")
    for x in got["offer_messages"] + got["items"]:
        assert re.fullmatch(r"\d{1,2}月\d{1,2}日", x["at"]), x["at"]
    assert appmod._month_day("2026-11-05T09:00:00+00:00") == "11月5日" and appmod._month_day(None) == ""
