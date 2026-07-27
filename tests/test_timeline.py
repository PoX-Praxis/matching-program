"""
指示書12改訂: 接続成立時のスナップショット結合＋軌跡API の3層公開範囲。

- approve の establish_hook は成立の瞬間だけ vessel_json に snapshots を書く（理由フィールドなし）。
- /api/timeline: 本人=全部 / 当事者の相手=中身は見えるが根拠・数値なし / 第三者=necessity_text のみ・
  vulnerable_hidden の時点は中身を出さない。接続の事実（成立・終了）は全層に・理由なし。
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as appmod
import snapshots
from ledger import approve, load_all_vessels


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── establish_hook: 成立時のみ・両者の snapshot・理由なし ─────────────────────
def test_establish_hook_records_snapshots_at_mutual():
    db = _db()
    hook = lambda f, j: {f: "sF", j: "sJ"}
    approve("alice", "bob", db_path=db, establish_hook=hook)
    v0 = load_all_vessels(db_path=db)[0]
    assert v0["is_connected"] is False and "snapshots" not in v0
    approve("bob", "alice", db_path=db, establish_hook=hook)
    v1 = load_all_vessels(db_path=db)[0]
    assert v1["is_connected"] is True
    assert v1["snapshots"] == {"alice": "sF", "bob": "sJ"}
    # 理由フィールドは持たない（原則3）
    assert "reason" not in v1 and "reason" not in v1["joins"][0]


def test_establish_judgment_unchanged_without_hook():
    db = _db()
    approve("a", "b", db_path=db)
    approve("b", "a", db_path=db)
    v = load_all_vessels(db_path=db)[0]
    assert v["is_connected"] is True and "snapshots" not in v


# ── timeline 3層 ────────────────────────────────────────────────────────────
_SNAP = [
    {"snapshot_id": "s1", "created_at": "2026-07-20T00:00:00+00:00", "schema_version": "v4.3",
     "will_text": "つなぎたい", "state": {"state_have": "知識"},
     "supporting": {"生テキスト": ["原文の秘密RAW"]},
     "necessity": {"necessity_text": "翻訳できる開発者", "evidence_span": "根拠SECRET",
                   "gate_s": 0.6, "gate_u": 0.3, "gamma": 0.36, "p_sharpness": -0.4,
                   "alpha": 1.0, "beta": 2.0},
     "src_input_hash": "H", "vulnerable_hidden": False},
    {"snapshot_id": "s2", "created_at": "2026-07-22T00:00:00+00:00", "schema_version": "v4.3",
     "will_text": "脆弱な時点の意志VULN", "state": {"state_have": "弱っている"},
     "supporting": {},
     "necessity": {"necessity_text": "支えてくれる人", "evidence_span": "脆弱EV",
                   "gate_s": 0.5, "gamma": 0.2},
     "src_input_hash": "H2", "vulnerable_hidden": True},
]
_VES = [{"vessel_id": "v1", "founder": "u1", "is_connected": True,
         "joins": [{"joiner": "u2", "established_at": "2026-07-21T00:00:00+00:00",
                    "terminal_state": "active", "closed_at": "2026-07-21T00:00:00+00:00",
                    "approvals": []}]}]


def _patch(snaps, vessels):
    orig = (snapshots.get_snapshots, appmod.load_all_vessels)
    snapshots.get_snapshots = lambda uid, db_path=None: snaps
    appmod.load_all_vessels = lambda db_path=None: vessels
    return orig


def _restore(orig):
    snapshots.get_snapshots, appmod.load_all_vessels = orig


def _get(viewer):
    return appmod.app.test_client().get(f"/api/timeline/u1?viewer={viewer}").get_json()


def test_owner_sees_everything():
    orig = _patch(_SNAP, _VES)
    try:
        d = _get("u1")
        assert d["viewer_role"] == "owner"
        snaps = [i for i in d["items"] if i["kind"] == "snapshot"]
        s1 = next(i for i in snaps if i["snapshot_id"] == "s1")
        assert s1["evidence_span"] == "根拠SECRET" and s1["numbers"]["gate_s"] == 0.6
        assert s1["schema_version"] == "v4.3"
        s2 = next(i for i in snaps if i["snapshot_id"] == "s2")
        assert s2["will_text"] == "脆弱な時点の意志VULN"   # 本人は自分の伏せ時点も見える
        assert s2["vulnerable_hidden"] is True              # UIトグル用フラグ
    finally:
        _restore(orig)


def test_partner_sees_content_but_not_evidence_or_numbers():
    orig = _patch(_SNAP, _VES)   # u2 は u1 と成立済み＝当事者の相手
    try:
        d = _get("u2")
        assert d["viewer_role"] == "partner"
        s2 = next(i for i in d["items"] if i.get("snapshot_id") == "s2")
        assert s2["will_text"] == "脆弱な時点の意志VULN"   # 相手は伏せ時点も中身が見える
        assert s2["necessity_text"] == "支えてくれる人"
        assert "evidence_span" not in s2 and "numbers" not in s2   # 根拠・数値は無し
    finally:
        _restore(orig)


def test_thirdparty_necessity_only_and_hidden_time_is_masked():
    orig = _patch(_SNAP, _VES)
    try:
        d = _get("u3")   # u3 は無関係＝第三者
        assert d["viewer_role"] == "third"
        s1 = next(i for i in d["items"] if i.get("snapshot_id") == "s1")
        assert s1["necessity_text"] == "翻訳できる開発者"   # 非伏せは necessity_text のみ
        assert "will_text" not in s1 and "evidence_span" not in s1 and "numbers" not in s1
        s2 = next(i for i in d["items"] if i.get("snapshot_id") == "s2")
        assert s2.get("hidden") is True                     # 伏せ時点は中身なし（事実は残る）
        assert "necessity_text" not in s2 and "will_text" not in s2
        blob = str(d)
        assert "SECRET" not in blob and "原文の秘密RAW" not in blob and "脆弱EV" not in blob
        assert "脆弱な時点の意志VULN" not in blob            # 伏せ時点の中身も漏れない
    finally:
        _restore(orig)


def test_connection_facts_visible_to_all_without_reason():
    orig = _patch(_SNAP, _VES)
    try:
        for viewer in ("u1", "u2", "u3"):
            d = _get(viewer)
            conns = [i for i in d["items"] if i["kind"] == "connection"]
            assert conns and conns[0]["event"] == "established" and conns[0]["other"] == "u2"
            assert "reason" not in conns[0]                 # 理由は無い
    finally:
        _restore(orig)


def test_old_vessel_without_snapshots_key_ok():
    old = [{"vessel_id": "v", "founder": "u1",
            "joins": [{"joiner": "u2", "established_at": "2026-07-01T00:00:00+00:00",
                       "terminal_state": "active", "closed_at": None, "approvals": []}]}]
    orig = _patch([], old)
    try:
        d = _get("z")
        assert any(i["kind"] == "connection" for i in d["items"])
    finally:
        _restore(orig)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\ntimeline テスト: {len(tests)} 件 全 PASS")
