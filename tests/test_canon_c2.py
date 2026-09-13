"""指示書28 段階2: canon_version c1→c2 と台帳フィールド追加。

- 過去 c1 イベントの検証が壊れない（verify_chain は保存版で再計算・§9-4）。
- content_hash: c1 は seeking を無視（凍結）、c2 は seeking を含む。
- necessity.published に source_snapshot_hash / generator_tag / attempt_n が載る。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import canon
import ledger_events as le
import necessities as N
from canon import canonicalize, sha256_hex


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


# ── §9-4: c1/c2 混在でも検証が壊れない ─────────────────────────────────────────
def test_verify_chain_mixed_c1_c2(monkeypatch):
    db = _db()
    # 過去イベント（c1 で刻む）を再現
    monkeypatch.setattr(le, "CANON_VERSION", "c1")
    e1 = le.append_event("u1", "subject.created", {"subject_id": "u1"}, db_path=db)
    # 版を上げてから新イベント（c2）
    monkeypatch.setattr(le, "CANON_VERSION", "c2")
    e2 = le.append_event("u1", "necessity.published", {"n": 1, "content_hash": "abc"}, db_path=db)
    events = le.get_events(db_path=db)
    assert events[0]["canon_version"] == "c1" and events[1]["canon_version"] == "c2"
    # 混在チェーンでも整合（保存された版の body を再直列化して event_hash を再計算）
    assert le.verify_chain(db_path=db)["ok"] is True
    assert e2["prev_hash"] == e1["event_hash"]


# ── content_hash: c1 凍結 / c2 は seeking 含む ──────────────────────────────────
def test_content_hash_c1_frozen_ignores_seeking():
    nums = {"gate_s": 0.0, "gate_u": 0.3, "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0}
    h_s1 = N.compute_content_hash("必要像", nums, "commit", "求A", canon_version="c1")
    h_s2 = N.compute_content_hash("必要像", nums, "commit", "求B", canon_version="c1")
    assert h_s1 == h_s2   # c1 は seeking を見ない
    # c1 は旧来の形（seeking キー無し）とビット一致で凍結されている
    expected = sha256_hex(canonicalize({
        "necessity_text": "必要像",
        "numbers": {k: nums.get(k) for k in N._NUM_KEYS},
        "evidence_commit": "commit",
    }))
    assert h_s1 == expected


def test_content_hash_c2_includes_seeking():
    nums = {"gate_s": 0.0, "gate_u": 0.3, "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0}
    h_a = N.compute_content_hash("必要像", nums, "commit", "求A")   # 既定 = c2
    h_b = N.compute_content_hash("必要像", nums, "commit", "求B")
    assert h_a != h_b                                              # seeking で変わる
    h_c1 = N.compute_content_hash("必要像", nums, "commit", "求A", canon_version="c1")
    assert h_a != h_c1                                            # c2 と c1 は別ハッシュ


# ── necessity.published のフィールド ───────────────────────────────────────────
def test_publish_necessity_records_provenance_and_c2():
    db = _db()
    nec = {"necessity_text": "伴走者が要る", "gate_s": 0.3, "gate_u": 0.3,
           "p_sharpness": 0.0, "alpha": 1.0, "beta": 1.0, "evidence_span": "一緒に背負える人"}
    r = N.publish_necessity("u1", "subject", nec, origin="generated", generator="Claude Opus 4.8",
                            seeking="実装できる人", source_snapshot_hash="prof_hash_123",
                            generator_tag="community-overall-v1.0/claude", attempt_n=2, db_path=db)
    assert r["skipped"] is False
    ev = [e for e in le.get_events(db_path=db) if e["type"] == "necessity.published"][0]
    assert ev["payload"]["source_snapshot_hash"] == "prof_hash_123"
    assert ev["payload"]["generator_tag"] == "community-overall-v1.0/claude"
    assert ev["payload"]["attempt_n"] == 2
    assert ev["canon_version"] == "c2"


def test_publish_necessity_churn_skips_same_content():
    db = _db()
    nec = {"necessity_text": "同じ", "gate_s": 0.0, "gate_u": 0.3, "p_sharpness": 0.0,
           "alpha": 1.0, "beta": 1.0, "evidence_span": ""}
    r1 = N.publish_necessity("u1", "subject", nec, seeking="同じ求め", db_path=db)
    r2 = N.publish_necessity("u1", "subject", nec, seeking="同じ求め", db_path=db)
    assert r1["skipped"] is False and r2["skipped"] is True     # 同一内容は churn で弾く
    # seeking を変えると別内容 → 弾かれない（c2 は seeking を content に含む）
    r3 = N.publish_necessity("u1", "subject", nec, seeking="違う求め", db_path=db)
    assert r3["skipped"] is False


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-q"])
