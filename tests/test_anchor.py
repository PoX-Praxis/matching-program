"""指示書17 段階4: 日次 Merkle root（アンカー）。

- root はその日のイベントハッシュ（anchor.published を除く）＋是認ログから計算。
- イベントゼロの日も空 root を記録（飛ばし検出）。
- prev_anchor で連結。anchor.published も鎖に乗る。
- 改ざん（イベント差し替え）は verify で不一致になる。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import anchor
import ledger_events as le
from db_connect import get_connection


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def test_merkle_root_basic_shapes():
    assert anchor.merkle_root([]) == anchor.merkle_root([])          # 空は決定的
    one = anchor.merkle_root(["aa" * 32])
    assert one == "aa" * 32                                          # 1枚はそのまま
    two = anchor.merkle_root(["aa" * 32, "bb" * 32])
    assert len(two) == 64 and two != one                            # 2枚は畳まれる


def test_publish_and_verify_matches():
    db = _db()
    le.append_event("u1", "subject.created", {"subject_id": "u1", "kind": "individual"}, db_path=db)
    le.append_event("u1", "terms.accepted", {"subject_id": "u1", "terms_version": "2026-08"}, db_path=db)
    ev = le.get_events(db_path=db)[0]
    date = ev["at"][:10]
    res = anchor.publish_anchor(date, db_path=db)
    assert res["skipped"] is False and res["event_count"] == 2
    v = anchor.verify_date(date, db_path=db)
    assert v["anchored"] is True and v["match"] is True
    # anchor.published 自身も台帳に乗る（鎖に含まれる）
    assert le.get_events(type_="anchor.published", db_path=db)


def test_empty_day_records_empty_root():
    db = _db()
    from canon import sha256_hex
    res = anchor.publish_anchor("2026-01-01", db_path=db)
    assert res["skipped"] is False and res["event_count"] == 0
    assert res["root"] == sha256_hex(b"")          # 空の日も記録される（§6-1）


def test_idempotent_same_date():
    db = _db()
    anchor.publish_anchor("2026-02-02", db_path=db)
    again = anchor.publish_anchor("2026-02-02", db_path=db)
    assert again["skipped"] is True


def test_prev_anchor_links():
    db = _db()
    a1 = anchor.publish_anchor("2026-03-01", db_path=db)
    a2 = anchor.publish_anchor("2026-03-02", db_path=db)
    assert a1["prev_anchor"] is None
    assert a2["prev_anchor"] == a1["root"]          # 連結


def test_tamper_is_detected():
    db = _db()
    le.append_event("u1", "subject.created", {"subject_id": "u1", "kind": "individual"}, db_path=db)
    ev = le.get_events(db_path=db)[0]
    date = ev["at"][:10]
    anchor.publish_anchor(date, db_path=db)

    # (a) payload だけ書き換え event_hash を放置 → 鎖の再計算（verify_chain）で検出
    with get_connection(db) as con:
        con.execute("UPDATE ledger_events SET payload_json=%s WHERE seq=%s",
                    ('{"subject_id":"HACKED","kind":"individual"}', ev["seq"]))
    assert le.verify_chain(db_path=db)["ok"] is False

    # (b) event_hash ごと差し替え（イベント置換の模擬）→ アンカー root が不一致
    with get_connection(db) as con:
        con.execute("UPDATE ledger_events SET event_hash=%s WHERE seq=%s", ("00" * 32, ev["seq"]))
    assert anchor.verify_date(date, db_path=db)["match"] is False


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nanchor テスト: {len(tests)} 件 全 PASS")
