"""指示書35: スナップショット保存をベクトル化から切り離す。

確定処理（_ingest_v4_from_flat）は、profiles_v4 保存の直後に **同期・必須**で時点
スナップショット（本文の記録）を保存する。ベクトル化（外部依存＝埋め込みモデル）は
その後の非同期ジョブで、失敗してもスナップショットには影響しない。

- §7-4 完了条件: ベクトル化が走らない/失敗しても user_snapshots に行が残る。
- §7-5: スナップショット保存が失敗したら確定（_ingest）自体が失敗する（例外送出）。
- §7-6: view_overrides（本人より）がスナップショットに含まれる。churn 判定は content_hash
  のままなので「本人より」だけの変更は新スナップショットを作らない（非対称）。
"""
import os, sys, tempfile
ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, ROOT)

import app as appmod
import db as dbmod
import snapshots as snap
from db_v4 import MemoryStore


def _db():
    return os.path.join(tempfile.mkdtemp(), "t.db")


def _flat(will="止まった接続を動かしたい", nec="現場を実装に翻訳できる開発者"):
    return {
        "will_text": will,
        "state_have": "現場の知識", "state_can_type": "翻訳する動き",
        "state_bound": "技術がない", "state_unsorted": "",
        "supporting_raw": {"背景": "BG", "意志_どこへ": "WHERE", "意志_なぜ": "WHY",
                           "経験": "EXP", "求めている": "実装できる人", "生テキスト": ["素の言葉"]},
        "necessity_text": nec, "gate_s": 0.6, "gate_u": 0.3, "p_sharpness": -0.4,
        "alpha": 1.0, "beta": 2.0, "evidence_span": "一緒に背負える", "generator": "Claude",
    }


def _run_ingest(store, db, flat, *, spawn=None):
    """confirm/registration の同期部（_ingest_v4_from_flat）を SQLite + MemoryStore で走らせる。
    spawn=None なら _spawn_v4_job を no-op に（＝ベクトル化を起動しない＝失敗相当）。"""
    orig = (appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job, appmod.DB)
    appmod.is_postgres = lambda: True
    appmod._v4_store = lambda: store
    appmod._spawn_v4_job = spawn or (lambda *a, **k: None)
    appmod.DB = db
    try:
        return appmod._ingest_v4_from_flat(flat, profile_id="u_k")
    finally:
        appmod.is_postgres, appmod._v4_store, appmod._spawn_v4_job, appmod.DB = orig


# ── §7-4 完了条件: ベクトル化が走らなくてもスナップショットは残る ────────────────
def test_snapshot_saved_even_when_vectorization_never_runs():
    db, store = _db(), MemoryStore()
    _run_ingest(store, db, _flat())            # _spawn は no-op（ベクトル化しない）
    assert not store.has_bundle("u_k", __import__("embedding_config").MODEL_TAG)  # ベクトルは無い
    snaps = snap.get_snapshots("u_k", db_path=db)
    assert len(snaps) == 1                     # それでも本文スナップショットは残る
    s = snaps[0]
    assert s["will_text"] == "止まった接続を動かしたい"
    assert s["state"]["state_have"] == "現場の知識"
    assert s["supporting"]["背景"] == "BG"
    assert s["necessity"]["necessity_text"] == "現場を実装に翻訳できる開発者"


def test_snapshot_saved_before_vectorize_spawn():
    """スナップショット保存はベクトル化ジョブ起動より前。spawn 段で失敗しても本文は残っている。"""
    db, store = _db(), MemoryStore()
    def boom(*a, **k):
        raise RuntimeError("ベクトル化起動失敗")
    try:
        _run_ingest(store, db, _flat(), spawn=boom)
    except RuntimeError:
        pass
    assert len(snap.get_snapshots("u_k", db_path=db)) == 1   # spawn より前に保存済み


# ── §7-5: スナップショット保存が失敗したら確定が失敗する（例外を握りつぶさない）────
def test_confirm_fails_if_snapshot_save_fails():
    db, store = _db(), MemoryStore()
    orig_save = snap.save_snapshot
    snap.save_snapshot = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("snap DB 障害"))
    try:
        raised = False
        try:
            _run_ingest(store, db, _flat())
        except RuntimeError:
            raised = True
        assert raised, "スナップショット保存の失敗は確定を失敗させる（best-effort にしない）"
    finally:
        snap.save_snapshot = orig_save


# ── §7-6: view_overrides がスナップショットに含まれる ─────────────────────────
def test_view_overrides_included_in_snapshot():
    db, store = _db(), MemoryStore()
    # profiles 行を作り「本人より」を設定（view_overrides は profiles テーブルに残る設計）。
    dbmod.save_profile("u_k", {"_meta": {"schema_version": "v4"}, "意志": "x",
                               "現状": {}, "supporting_material": {}}, db_path=db)
    dbmod.save_view_overrides("u_k", {"free_text": "本人からのメッセージ"}, db_path=db)
    _run_ingest(store, db, _flat())
    s = snap.get_snapshots("u_k", db_path=db)[0]
    assert s["view_overrides"] == {"free_text": "本人からのメッセージ"}


def test_view_overrides_only_change_does_not_add_snapshot():
    """非対称（§4）: content_hash は view_overrides を含まないため、本人よりだけ変えても
    churn でスキップされ新スナップショットは増えない（本指示書では content_hash 範囲を変えない）。"""
    db, store = _db(), MemoryStore()
    dbmod.save_profile("u_k", {"_meta": {"schema_version": "v4"}, "意志": "x",
                               "現状": {}, "supporting_material": {}}, db_path=db)
    _run_ingest(store, db, _flat())                       # 1点目
    dbmod.save_view_overrides("u_k", {"free_text": "後から書いた本人より"}, db_path=db)
    _run_ingest(store, db, _flat())                       # 構造化内容は同一 → churn スキップ
    assert len(snap.get_snapshots("u_k", db_path=db)) == 1  # 増えない（本文ハッシュ不変）


# ── churn: 内容が変われば増える（回帰防止）──────────────────────────────────
def test_snapshot_added_when_content_changes():
    db, store = _db(), MemoryStore()
    _run_ingest(store, db, _flat(will="最初の意志"))
    _run_ingest(store, db, _flat(will="変わった意志"))
    assert len(snap.get_snapshots("u_k", db_path=db)) == 2


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t(); print(f"  PASS: {t.__name__}")
    print(f"\n指示書35 スナップショット切り離し: {len(tests)} 件 全 PASS")
