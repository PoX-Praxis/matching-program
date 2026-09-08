"""指示書18: 接続の根拠是正（§1）・宣言ハッシュ範囲（§2）・チャネル来歴（§3）・float ガード（§6-1）。"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import pytest
import ledger
import ledger_events as le
from db_connect import get_connection
from subject_ledger import profile_content_hash


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── §1 接続の根拠 ────────────────────────────────────────────────────────────
def test_grounded_establish_fills_refs():
    db = _db()
    refs = {"a": {"profile_snapshot_hash": "PA", "necessity_hash": "NA"},
            "b": {"profile_snapshot_hash": "PB", "necessity_hash": None}}
    rr = lambda s: refs.get(s)
    ledger.approve("a", "b", db_path=db, ref_resolver=rr, require_grounding=True)
    r = ledger.approve("b", "a", db_path=db, ref_resolver=rr, require_grounding=True)
    assert r["established"] is True
    ev = le.get_events(type_="connection.established", db_path=db)[0]["payload"]
    assert ev["a_ref"] == {"profile_snapshot_hash": "PA", "necessity_hash": "NA"}
    assert ev["b_ref"] == {"profile_snapshot_hash": "PB", "necessity_hash": None}
    v = ledger.load_all_vessels(db_path=db)[0]
    assert v["is_connected"] is True and v["grounded"] is True


def test_gate_blocks_when_profile_structured_missing():
    db = _db()
    refs = {"a": {"profile_snapshot_hash": "PA", "necessity_hash": None},
            "b": {"profile_snapshot_hash": None, "necessity_hash": None}}   # b に根拠なし
    rr = lambda s: refs.get(s)
    ledger.approve("a", "b", db_path=db, ref_resolver=rr, require_grounding=True)
    r = ledger.approve("b", "a", db_path=db, ref_resolver=rr, require_grounding=True)
    assert r["established"] is False and r["reason"] == "missing_profile_structured"
    assert le.get_events(type_="connection.established", db_path=db) == []   # 成立させない
    # b が後で根拠を得れば、再承認で成立（自己修復）
    refs["b"]["profile_snapshot_hash"] = "PB"
    r2 = ledger.approve("b", "a", db_path=db, ref_resolver=rr, require_grounding=True)
    assert r2["established"] is True
    assert ledger.load_all_vessels(db_path=db)[0]["grounded"] is True


def test_old_shape_is_ungrounded_and_reported():
    db = _db()
    # 是正前の形（necessity_hash="" sentinel・profile_snapshot_hash に snapshot_id）
    le.append_event("x", "connection.established", {
        "a": "x", "b": "y", "initiator": "x",
        "a_ref": {"profile_snapshot_hash": "snap_1", "necessity_hash": ""},
        "b_ref": {"profile_snapshot_hash": "snap_2", "necessity_hash": ""},
        "as_of_seq": 0, "approved_at": {"x": "t", "y": "t"}, "contributions": [],
    }, db_path=db)
    assert ledger.grounding_report(db_path=db) == {"total": 1, "grounded": 0, "ungrounded": 1}
    v = ledger.load_all_vessels(db_path=db)[0]
    assert v["grounded"] is False
    # 旧形でも timeline 用スナップショット結合は a_ref から拾える（後方互換）
    assert v["snapshots"] == {"x": "snap_1", "y": "snap_2"}


def test_default_no_resolver_is_ungrounded_null_refs():
    db = _db()
    ledger.approve("a", "b", db_path=db)
    ledger.approve("b", "a", db_path=db)          # resolver 無し（レガシー/テスト）
    ev = le.get_events(type_="connection.established", db_path=db)[0]["payload"]
    assert ev["a_ref"]["profile_snapshot_hash"] is None    # null（"" ではない）
    assert ledger.load_all_vessels(db_path=db)[0]["grounded"] is False


# ── §2 宣言ハッシュ範囲 ──────────────────────────────────────────────────────
def test_content_hash_covers_declaration_fields():
    base = {"will_text": "w", "state_have": "h",
            "supporting_raw": {"背景": "bg", "一行紹介": "one", "意志_どこへ": "where",
                               "意志_なぜ": "why", "経験": "exp", "要約文": "sum"}}
    h0 = profile_content_hash(base)
    for k in ("背景", "一行紹介", "意志_どこへ", "意志_なぜ", "経験", "要約文"):
        changed = {**base, "supporting_raw": {**base["supporting_raw"], k: "別"}}
        assert profile_content_hash(changed) != h0, f"{k} がハッシュに効いていない"
    # raw / 生成素材はハッシュに影響しない（宣言でない）
    same = {**base, "supporting_raw": {**base["supporting_raw"],
            "生テキスト": ["秘密"], "系列素材": ["x"], "意志要求の素材": "y"}}
    assert profile_content_hash(same) == h0
    # 欠損は空文字扱い（安定）
    assert profile_content_hash({"will_text": "w", "state_have": "h", "supporting_raw": {}}) \
        == profile_content_hash({"will_text": "w", "state_have": "h",
                                 "supporting_raw": {"背景": ""}})


# ── §3 チャネル来歴 ──────────────────────────────────────────────────────────
def test_channel_provenance_stored_in_requests_not_ledger():
    db = _db()
    ledger.approve("a", "b", predicted_role="実装力", channel="complementary",
                   match_run_id="run1", db_path=db)
    with get_connection(db) as con:
        row = con.execute(
            "SELECT predicted_role, channel, match_run_id FROM connection_requests "
            "WHERE from_subject='a' AND to_subject='b'").fetchone()
    assert tuple(row) == ("実装力", "complementary", "run1")
    # 台帳イベントには match_run_id 等を載せない（成立前なのでそもそもイベント無し）
    assert le.get_events(type_="connection.established", db_path=db) == []


# ── §6-1 float ガード ────────────────────────────────────────────────────────
def test_append_event_rejects_float():
    db = _db()
    with pytest.raises(ValueError):
        le.append_event("a", "t.x", {"n": 0.5}, db_path=db)
    with pytest.raises(ValueError):
        le.append_event("a", "t.x", {"nested": {"deep": [1, 2.0]}}, db_path=db)
    # int / bool / str は可
    assert le.append_event("a", "t.ok", {"i": 1, "b": True, "s": "x"}, db_path=db)["seq"] == 1


if __name__ == "__main__":
    import pytest as _p
    sys.exit(_p.main([__file__, "-q"]))
