"""指示書17 §7 配線: _publish_necessity_best_effort が必要像を 1:N 台帳へ載せ、
ベクトルを実体化する。churn（同一内容）はスキップ、変化は n を進める。

v4 の profile ベクトル化（profiles_v4/profile_vectors）は Postgres 専用のため、
ここでは配線の実体である best-effort ヘルパを直接検証する（_v4_async_job は成功時に
このヘルパを呼ぶ・1行）。necessity のベクトル化は embed()=stub で実サービス不要。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import necessities as N
import ledger_events as le


def _setup():
    db = os.path.join(tempfile.mkdtemp(), "t.db")
    appmod.DB = db
    return db


_PROFILE = {"will_text": "つなぐ", "state_have": "知識", "state_can_type": "",
            "state_bound": "", "state_unsorted": "", "supporting_raw": {}}
_NEC = {"necessity_text": "翻訳できる開発者", "gate_s": 0.6, "gate_u": 0.3,
        "p_sharpness": -0.4, "alpha": 1.0, "beta": 2.0, "evidence_span": "原文の一部",
        "src_input_hash": "H1", "generator_name": "Claude Opus 4.8"}


def test_records_and_vectorizes_necessity():
    db = _setup()
    appmod._publish_necessity_best_effort("u1", dict(_PROFILE), dict(_NEC))
    rows = N.get_necessities("u1", db_path=db)
    assert len(rows) == 1 and rows[0]["owner_kind"] == "subject"
    assert rows[0]["generator"] == "claude-opus"                 # 正規化
    assert rows[0]["will_text"] == "つなぐ"                       # profile から補完
    evs = le.get_events(type_="necessity.published", db_path=db)
    assert len(evs) == 1 and evs[0]["payload"]["content_hash"] == rows[0]["content_hash"]
    qv = N.query_vectors(rows[0]["necessity_id"], db_path=db)
    assert qv and len(qv["will_symmetric"]) == len(qv["necessity_query"]) > 0   # 実体化済み


def test_unchanged_is_churn_skipped():
    db = _setup()
    appmod._publish_necessity_best_effort("u2", dict(_PROFILE), dict(_NEC))
    appmod._publish_necessity_best_effort("u2", dict(_PROFILE), dict(_NEC))   # 同一内容
    assert len(N.get_necessities("u2", db_path=db)) == 1
    assert len(le.get_events(type_="necessity.published", db_path=db)) == 1
    assert le.verify_chain(db_path=db)["ok"] is True


def test_changed_adds_second_with_prev_chain():
    db = _setup()
    appmod._publish_necessity_best_effort("u3", dict(_PROFILE), dict(_NEC))
    appmod._publish_necessity_best_effort("u3", dict(_PROFILE),
                                          dict(_NEC, necessity_text="支える人"))
    rows = N.get_necessities("u3", db_path=db)
    assert [r["n"] for r in rows] == [1, 2]
    assert rows[1]["prev_necessity"] == rows[0]["necessity_id"]


def test_missing_necessity_is_noop():
    db = _setup()
    appmod._publish_necessity_best_effort("u4", dict(_PROFILE), None)   # 例外を出さず何もしない
    assert N.get_necessities("u4", db_path=db) == []


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\nnecessity 配線テスト: {len(tests)} 件 全 PASS")
