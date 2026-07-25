"""
指示書12 フェーズ2/3: 接続成立時のスナップショット結合＋軌跡API の公開範囲。

- approve の establish_hook は成立の瞬間だけ呼ばれ vessel_json に snapshots を書く。
- /api/timeline: 本人には evidence_span＋数値、第三者には necessity_text のみ。
  接続イベントは完全公開。原文(生テキスト)・evidence_span・数値は第三者に漏れない。
"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as appmod
import snapshots
from ledger import approve, load_all_vessels


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── establish_hook: 成立時のみ・両者の snapshot を記録 ────────────────────────
def test_establish_hook_records_snapshots_at_mutual():
    db = _db()
    hook = lambda f, j: {f: "sF", j: "sJ"}
    approve("alice", "bob", db_path=db, establish_hook=hook)   # 片方向 → 未成立
    v0 = load_all_vessels(db_path=db)[0]
    assert v0["is_connected"] is False and "snapshots" not in v0   # まだ書かれない
    approve("bob", "alice", db_path=db, establish_hook=hook)   # 相互 → 成立
    v1 = load_all_vessels(db_path=db)[0]
    assert v1["is_connected"] is True
    assert v1["snapshots"] == {"alice": "sF", "bob": "sJ"}     # founder=alice / joiner=bob


def test_establish_judgment_unchanged_without_hook():
    db = _db()
    approve("a", "b", db_path=db)          # hook 無しでも従来どおり
    approve("b", "a", db_path=db)
    v = load_all_vessels(db_path=db)[0]
    assert v["is_connected"] is True and "snapshots" not in v


# ── timeline API の公開範囲 ─────────────────────────────────────────────────
_SNAP = [{
    "snapshot_id": "s1", "created_at": "2026-07-20T00:00:00+00:00",
    "will_text": "つなぎたい", "state": {"state_have": "知識"},
    "supporting": {"生テキスト": ["ここは原文の秘密"]},
    "necessity": {"necessity_text": "翻訳できる開発者", "evidence_span": "根拠の生引用SECRET",
                  "gate_s": 0.6, "gate_u": 0.3, "gamma": 0.36, "p_sharpness": -0.4,
                  "alpha": 1.0, "beta": 2.0},
    "src_input_hash": "H",
}]
_VES = [{
    "vessel_id": "v1", "founder": "u1", "is_connected": True,
    "joins": [{"joiner": "u2", "established_at": "2026-07-21T00:00:00+00:00",
               "terminal_state": "active", "closed_at": "2026-07-21T00:00:00+00:00",
               "approvals": []}],
}]


def _patch(snaps, vessels):
    orig = (snapshots.get_snapshots, appmod.load_all_vessels)
    snapshots.get_snapshots = lambda uid, db_path=None: snaps
    appmod.load_all_vessels = lambda db_path=None: vessels
    return orig


def _restore(orig):
    snapshots.get_snapshots, appmod.load_all_vessels = orig


def test_timeline_owner_sees_evidence_and_numbers():
    orig = _patch(_SNAP, _VES)
    try:
        d = appmod.app.test_client().get("/api/timeline/u1?viewer=u1").get_json()
        assert d["is_owner"] is True
        snap = next(i for i in d["items"] if i["kind"] == "snapshot")
        assert snap["necessity_text"] == "翻訳できる開発者"
        assert snap["evidence_span"] == "根拠の生引用SECRET"
        assert snap["numbers"]["gate_s"] == 0.6 and snap["numbers"]["gamma"] == 0.36
        assert any(i["kind"] == "connection" and i["other"] == "u2" for i in d["items"])
    finally:
        _restore(orig)


def test_timeline_thirdparty_no_evidence_no_numbers_no_raw():
    orig = _patch(_SNAP, _VES)
    try:
        d = appmod.app.test_client().get("/api/timeline/u1?viewer=u2").get_json()
        assert d["is_owner"] is False
        snap = next(i for i in d["items"] if i["kind"] == "snapshot")
        assert snap["necessity_text"] == "翻訳できる開発者"   # necessity_text は公開
        assert "evidence_span" not in snap                    # 根拠は非公開
        assert "numbers" not in snap                          # 数値は非公開
        blob = str(d)
        assert "SECRET" not in blob and "ここは原文の秘密" not in blob  # 原文・根拠が漏れない
        assert any(i["kind"] == "connection" for i in d["items"])      # 接続は完全公開
    finally:
        _restore(orig)


def test_timeline_old_vessel_without_snapshots_key_ok():
    old = [{"vessel_id": "v", "founder": "u1",
            "joins": [{"joiner": "u2", "established_at": "2026-07-01T00:00:00+00:00",
                       "terminal_state": "active", "closed_at": None, "approvals": []}]}]
    orig = _patch([], old)
    try:
        d = appmod.app.test_client().get("/api/timeline/u1?viewer=z").get_json()
        assert any(i["kind"] == "connection" for i in d["items"])   # 壊れない
    finally:
        _restore(orig)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\ntimeline テスト: {len(tests)} 件 全 PASS")
