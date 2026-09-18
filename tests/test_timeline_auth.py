"""指示書36: /api/timeline の閲覧者判定をセッションに（?viewer= の自己申告を廃止）。

- 本人/相手はセッションの subject_id だけで判定。?viewer= を送っても本人扱いにならない。
- 未ログインは 401 でなく第三者として応答（第三者にも見せる情報があるため）。
- 第三者向け necessity_text は公開閾値（指示書11）を timeline にも適用。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import snapshots as snap
import ledger as led


def _client():
    os.environ.pop("POX_DEBUG", None)
    appmod.DB = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.app.config["TESTING"] = True
    return appmod.app.test_client()


def _login(c, sid):
    with c.session_transaction() as sess:
        sess["subject_id"] = sid


def _seed_victim_snapshot(db):
    snap.save_snapshot(
        "u_victim", will_text="被害者の意志",
        state={"state_have": "H", "state_can_type": "C", "state_bound": "", "state_unsorted": ""},
        supporting={"背景": "BG"},
        necessity={"necessity_text": "必要像本文", "evidence_span": "生の語りの引用",
                   "gate_s": 0.6, "gate_u": 0.3, "gamma": 0.36},
        content_hash="C1", db_path=db)


def _get(c, uid, viewer=None):
    q = f"?viewer={viewer}" if viewer else ""
    return c.get(f"/api/timeline/{uid}{q}")


def _snapshot_item(data):
    return next(it for it in data["items"] if it.get("kind") == "snapshot")


# ── 完了条件 §4: ?viewer=<本人id> を送っても本人扱いにならない ────────────────────
def test_viewer_query_does_not_grant_owner():
    c = _client(); _seed_victim_snapshot(appmod.DB)
    # 未ログインで ?viewer=u_victim を偽装 → 第三者扱い（evidence_span・数値は出ない）
    r = _get(c, "u_victim", viewer="u_victim")
    assert r.status_code == 200
    data = r.get_json()
    assert data["viewer_role"] == "third"
    it = _snapshot_item(data)
    assert "evidence_span" not in it
    assert "numbers" not in it
    assert "will_text" not in it        # 現状・意志も第三者には出ない


def test_logged_in_other_with_viewer_spoof_is_not_owner():
    c = _client(); _seed_victim_snapshot(appmod.DB)
    _login(c, "u_attacker")             # 別人でログイン
    r = _get(c, "u_victim", viewer="u_victim")   # ?viewer で本人を偽装
    data = r.get_json()
    assert data["viewer_role"] == "third"        # セッション(u_attacker)基準＝第三者
    it = _snapshot_item(data)
    assert "evidence_span" not in it and "numbers" not in it


# ── §5: 未ログインは 401 でなく第三者として 200 ──────────────────────────────────
def test_unauthenticated_is_third_not_401():
    c = _client(); _seed_victim_snapshot(appmod.DB)
    r = _get(c, "u_victim")
    assert r.status_code == 200
    assert r.get_json()["viewer_role"] == "third"


# ── 本人はセッションで owner（evidence_span・数値が出る）──────────────────────────
def test_session_owner_sees_evidence_and_numbers():
    c = _client(); _seed_victim_snapshot(appmod.DB)
    _login(c, "u_victim")
    it = _snapshot_item(_get(c, "u_victim").get_json())
    assert it["evidence_span"] == "生の語りの引用"
    assert it["numbers"]["gate_s"] == 0.6
    assert it["will_text"] == "被害者の意志"


# ── §6: 接続の相手判定がセッション基準 ───────────────────────────────────────────
def test_partner_by_session_sees_will_but_not_evidence():
    c = _client(); db = appmod.DB
    _seed_victim_snapshot(db)
    # 双方向承認で成立させる（セッションの相手＝u_partner）
    led.approve("u_partner", "u_victim", db_path=db)
    led.approve("u_victim", "u_partner", db_path=db)
    _login(c, "u_partner")
    data = _get(c, "u_victim").get_json()
    assert data["viewer_role"] == "partner"
    it = _snapshot_item(data)
    assert it["will_text"] == "被害者の意志"        # 相手は will/state/necessity 可
    assert it["necessity_text"] == "必要像本文"
    assert "evidence_span" not in it               # 根拠・数値は本人のみ
    assert "numbers" not in it


# ── §2-3/§7: 第三者向け necessity_text に公開閾値を適用 ───────────────────────────
def test_third_party_necessity_hidden_before_threshold():
    c = _client(); _seed_victim_snapshot(appmod.DB)
    os.environ["POX_NECESSITY_PUBLIC_SINCE"] = "2099-01-01T00:00:00+00:00"  # 未来＝全て閾値前
    try:
        it = _snapshot_item(_get(c, "u_victim").get_json())   # 第三者
        assert "necessity_text" not in it        # 閾値前なので必要像も出さない
    finally:
        os.environ.pop("POX_NECESSITY_PUBLIC_SINCE", None)


def test_third_party_necessity_shown_after_threshold():
    c = _client(); _seed_victim_snapshot(appmod.DB)
    os.environ["POX_NECESSITY_PUBLIC_SINCE"] = "2020-01-01T00:00:00+00:00"  # 過去＝全て閾値後
    try:
        it = _snapshot_item(_get(c, "u_victim").get_json())
        assert it["necessity_text"] == "必要像本文"
        assert "evidence_span" not in it         # ただし evidence/数値は第三者には出ない
    finally:
        os.environ.pop("POX_NECESSITY_PUBLIC_SINCE", None)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n指示書36 タイムライン認証: {len(tests)} 件 全 PASS")
