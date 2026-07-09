"""
フェーズ2 検証テスト — 候補ベクトルキャッシュ層（eval/scripts/vector_cache.py）

stub バックエンドで実行可能（実モデルサーバー不要）。検証項目:
  - 初回は build が呼ばれ、2回目はキャッシュから返る（build を呼ばない）
  - 入力テキスト変更でその候補だけ再計算される
  - model_tag 不一致（保存ファイル vs 要求）で例外（禁則4の防御）
  - get_or_build に環境と違う model_tag を渡すと例外
  - save→load ラウンドトリップでベクトルが完全一致
"""
import sys, os, pathlib, shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pytest

import vector_cache as vc
from embedding_config import MODEL_TAG, FULL_DIM, SHORT_DIM
from embedding_service import build_vectors as real_build_vectors


PROFILE_A = {
    "will_text": "実用的なツールを作り、人々の課題を解決したい。",
    "state_have": "Python, ネットワーク技術, 複数の公開リポジトリ",
    "state_can_type": "技術で作る人",
    "state_bound": "リモート",
    "state_unsorted": "",
}
PROFILE_B = {
    "will_text": "研究を通じて新しい知能システムを実現したい。",
    "state_have": "機械学習, ベンチマーク開発",
    "state_can_type": "研究する人",
    "state_bound": "",
    "state_unsorted": "",
}


@pytest.fixture
def tmp_cache_dir(tmp_path, monkeypatch):
    """CACHE_DIR を一時ディレクトリに差し替え、eval/cache を汚さない。"""
    d = tmp_path / "cache"
    d.mkdir()
    monkeypatch.setattr(vc, "CACHE_DIR", d)
    return d


@pytest.fixture
def build_counter(monkeypatch):
    """vector_cache.build_vectors を呼び出し回数カウント付きラッパに差し替える。"""
    calls = {"n": 0}

    def counting(profile, necessity_text):
        calls["n"] += 1
        return real_build_vectors(profile, necessity_text)

    monkeypatch.setattr(vc, "build_vectors", counting)
    return calls


def test_first_build_then_cache_hit(tmp_cache_dir, build_counter):
    cache = {}
    v1 = vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    assert build_counter["n"] == 1                      # 初回は build
    v2 = vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    assert build_counter["n"] == 1                      # 2回目は build されない
    assert v1 == v2                                     # 同一ベクトル
    assert set(v1.keys()) == set(vc.VECTOR_KEYS)
    assert len(v1["will_symmetric"]) == FULL_DIM
    assert len(v1["will_sym_256"]) == SHORT_DIM


def test_text_change_recomputes_only_that_candidate(tmp_cache_dir, build_counter):
    cache = {}
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    vc.get_or_build("bob",   PROFILE_B, MODEL_TAG, cache)
    assert build_counter["n"] == 2

    # alice のテキストを変更 → alice だけ再計算
    changed = dict(PROFILE_A, will_text=PROFILE_A["will_text"] + "（追記）")
    vc.get_or_build("alice", changed,  MODEL_TAG, cache)
    assert build_counter["n"] == 3                      # +1（alice のみ）

    # bob は不変 → 再計算されない
    vc.get_or_build("bob",   PROFILE_B, MODEL_TAG, cache)
    assert build_counter["n"] == 3                      # 増えない


def test_save_load_roundtrip(tmp_cache_dir):
    cache = {}
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    vc.get_or_build("bob",   PROFILE_B, MODEL_TAG, cache)
    path = vc.save_cache(MODEL_TAG, cache)
    assert path.exists()

    loaded = vc.load_cache(MODEL_TAG)
    assert set(loaded.keys()) == {"alice", "bob"}
    # ベクトルが float64 ラウンドトリップで完全一致
    for cid in ("alice", "bob"):
        orig_vecs, orig_hash = cache[cid]
        load_vecs, load_hash = loaded[cid]
        assert load_hash == orig_hash
        for k in vc.VECTOR_KEYS:
            assert load_vecs[k] == orig_vecs[k]


def test_load_after_save_is_cache_hit(tmp_cache_dir, build_counter):
    # 事前に build して保存
    cache = {}
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    vc.save_cache(MODEL_TAG, cache)
    assert build_counter["n"] == 1

    # ロードし直して get_or_build → build されない（ハッシュ一致）
    reloaded = vc.load_cache(MODEL_TAG)
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, reloaded)
    assert build_counter["n"] == 1


def test_empty_cache_save_load(tmp_cache_dir):
    path = vc.save_cache(MODEL_TAG, {})
    assert path.exists()
    assert vc.load_cache(MODEL_TAG) == {}


def test_load_missing_file_returns_empty(tmp_cache_dir):
    assert vc.load_cache(MODEL_TAG) == {}


def test_model_tag_mismatch_on_load_raises(tmp_cache_dir):
    # tagA として保存 → ファイルを tagB の名前へコピー → tagB でロードすると内部 tagA と不一致
    cache = {}
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    path_a = vc.save_cache(MODEL_TAG, cache)          # 内部 model_tag = MODEL_TAG
    path_b = tmp_cache_dir / "other-model-tag_vectors.npz"
    shutil.copy(path_a, path_b)
    with pytest.raises(ValueError, match="model_tag 不一致"):
        vc.load_cache("other-model-tag")


def test_get_or_build_model_tag_mismatch_raises(tmp_cache_dir):
    # 環境の MODEL_TAG と違う model_tag を渡すと build_vectors の tag と不一致 → 例外
    cache = {}
    with pytest.raises(ValueError, match="model_tag"):
        vc.get_or_build("alice", PROFILE_A, "deliberately-wrong-tag", cache)


def test_save_is_atomic_no_tmp_left(tmp_cache_dir):
    # 保存後に一時ファイル(.tmp)が残らない（原子的 rename 済み）
    cache = {}
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    vc.save_cache(MODEL_TAG, cache)
    leftovers = list(tmp_cache_dir.glob("*.tmp"))
    assert leftovers == [], f"一時ファイルが残っている: {leftovers}"


def test_checkpoint_resume(tmp_cache_dir, build_counter):
    # チェックポイント保存→再開のシミュレーション:
    # 途中まで build して save（クラッシュ想定）→ 再ロードすると済み分は hit になる。
    cache = {}
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, cache)
    vc.save_cache(MODEL_TAG, cache)         # 1件だけ保存された状態でクラッシュしたと想定
    assert build_counter["n"] == 1

    resumed = vc.load_cache(MODEL_TAG)       # 再起動後のロード
    assert set(resumed.keys()) == {"alice"}
    # alice は再計算されない、bob だけ新規ビルド
    vc.get_or_build("alice", PROFILE_A, MODEL_TAG, resumed)
    assert build_counter["n"] == 1           # alice は hit（増えない）
    vc.get_or_build("bob", PROFILE_B, MODEL_TAG, resumed)
    assert build_counter["n"] == 2           # bob だけ +1
    vc.save_cache(MODEL_TAG, resumed)
    assert set(vc.load_cache(MODEL_TAG).keys()) == {"alice", "bob"}


def test_text_hash_stable_and_sensitive():
    h1 = vc._text_hash(PROFILE_A, "", MODEL_TAG)
    h2 = vc._text_hash(dict(PROFILE_A), "", MODEL_TAG)
    assert h1 == h2                                     # 同一入力 → 同一ハッシュ
    h3 = vc._text_hash(PROFILE_A, "necessity added", MODEL_TAG)
    assert h1 != h3                                     # necessity 変化 → 別ハッシュ
    h4 = vc._text_hash(PROFILE_A, "", "another-tag")
    assert h1 != h4                                     # model_tag 変化 → 別ハッシュ
