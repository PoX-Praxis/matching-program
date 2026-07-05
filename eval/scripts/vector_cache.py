"""
候補ベクトルのモデル別キャッシュ層（実行間キャッシュ）

目的:
  solo_eval.py / pool_eval.py は候補プールを実行のたびに build_vectors() で
  全件ベクトル化していた（1,090件×毎回）。この層を手前に挟むことで、
  入力テキストが変わっていない候補は前回のベクトルを読み出す（数秒）。

設計原則（実装タスク §フェーズ2 準拠）:
  - build_vectors() の変換ロジックは変えない。手前に保存/読出しを足すだけ。
  - 保存先はモデル別ファイル eval/cache/{MODEL_TAG}_vectors.npz。
    異なる model_tag のベクトルを同一ファイルに混ぜない（禁則4の防御）。
  - キャッシュのキーは「候補の一意識別子(cand_id, 想定 login)」。
    値に「入力テキストの sha256 ハッシュ」を持ち、テキストが変われば
    その候補だけ再計算する（ファイル更新日時には頼らない）。
  - スコア/ランキングはキャッシュしない（キャッシュ対象はベクトルのみ）。

キャッシュ構造（メモリ上）:
  dict[cand_id] = (vectors_dict, text_hash)
    vectors_dict : build_vectors() 戻りのうち 8 ベクトル（VECTOR_KEYS）を list で保持
    text_hash    : 入力テキスト＋model_tag の sha256（16進文字列）

注意（署名の補足）:
  build_vectors(profile, necessity_text) は profile と必要像テキストを取るため、
  get_or_build も cand_id / profile / necessity_text を受け取る。
  プール候補は necessity_text="" が既定（照合は seeker 側の necessity_query のみ使用）。
"""
import os
import sys
import json
import hashlib
import pathlib

import numpy as np

# src/ を import パスに追加（solo_eval.py 等と同じ流儀）
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))

from embedding_service import build_vectors  # noqa: E402

# build_vectors 戻りのうちベクトル本体のキー（model_tag は別扱い）。
VECTOR_KEYS = (
    "will_symmetric", "will_sym_256",
    "will_passage",   "will_pas_256",
    "state_passage",  "state_pas_256",
    "necessity_query", "necessity_q_256",
)

# text_hash に含める build_vectors 入力テキストのキー（profile 側）。
_TEXT_KEYS = ("will_text", "state_have", "state_can_type", "state_bound", "state_unsorted")

CACHE_DIR = pathlib.Path(__file__).parent.parent / "cache"


def cache_path(model_tag):
    """モデル別キャッシュファイルのパス。"""
    return CACHE_DIR / f"{model_tag}_vectors.npz"


def _text_hash(profile, necessity_text, model_tag):
    """
    build_vectors が参照する全入力テキスト＋model_tag から決定論的ハッシュを作る。
    テキストが1文字でも変われば別ハッシュ → その候補だけ再計算される。
    """
    payload = {k: (profile.get(k) if isinstance(profile.get(k), str) else "") for k in _TEXT_KEYS}
    payload["necessity_text"] = necessity_text if isinstance(necessity_text, str) else ""
    payload["model_tag"] = model_tag
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_cache(model_tag):
    """
    eval/cache/{model_tag}_vectors.npz を読み込み dict[cand_id] = (vectors, text_hash) を返す。
    ファイルが無ければ空 dict。保存されている model_tag が引数と不一致なら例外（禁則4の防御）。
    """
    path = cache_path(model_tag)
    if not path.exists():
        return {}
    data = np.load(path, allow_pickle=False)
    stored_tag = str(data["model_tag"])
    if stored_tag != model_tag:
        raise ValueError(
            f"キャッシュの model_tag 不一致: file={stored_tag!r} 要求={model_tag!r}。"
            f"異なるモデルのベクトルを混ぜてはならない（禁則4）。{path} を確認してください。"
        )
    ids = [str(x) for x in data["ids"]]
    hashes = [str(x) for x in data["hashes"]]
    cache = {}
    for i, cid in enumerate(ids):
        vecs = {k: data[k][i].tolist() for k in VECTOR_KEYS}
        cache[cid] = (vecs, hashes[i])
    return cache


def save_cache(model_tag, cache):
    """
    dict[cand_id] = (vectors, text_hash) を eval/cache/{model_tag}_vectors.npz に保存。
    ベクトルはチャネルごとに (N, dim) の float64 配列へ積む。model_tag も記録する。
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = cache_path(model_tag)

    ids = list(cache.keys())
    arrays = {"model_tag": np.array(model_tag)}
    arrays["hashes"] = np.array([cache[c][1] for c in ids]) if ids else np.array([])
    for k in VECTOR_KEYS:
        if ids:
            arrays[k] = np.array([cache[c][0][k] for c in ids], dtype=np.float64)
        else:
            arrays[k] = np.zeros((0, 0), dtype=np.float64)
    # ids は文字列配列で保存（object 配列は allow_pickle 必須になるため）
    arrays["ids"] = np.array([str(c) for c in ids])

    # 原子的書き込み: 一時ファイルへ書いてから rename。
    # 途中でクラッシュしても本体 .npz は壊れない（部分書き込みは .tmp 側だけ）。
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "wb") as fh:
        np.savez_compressed(fh, **arrays)
    os.replace(tmp, path)  # 同一FS上で原子的
    return path


def get_or_build(cand_id, profile, model_tag, cache, necessity_text=""):
    """
    候補のベクトルを返す。cache に cand_id があり text_hash が一致すればキャッシュを返し、
    無い or テキスト変更（ハッシュ不一致）なら build_vectors して cache を更新する。

    戻り値: VECTOR_KEYS の 8 ベクトルを持つ dict（build_vectors 戻りのベクトル部分）。
    副作用: cache[cand_id] を更新（呼び出し側で最後に save_cache すること）。
    """
    h = _text_hash(profile, necessity_text, model_tag)
    hit = cache.get(cand_id)
    if hit is not None and hit[1] == h:
        return hit[0]

    vecs_full = build_vectors(profile, necessity_text)
    # 禁則4の防御: build_vectors が付けた model_tag（＝環境の MODEL_TAG）と要求が一致するか。
    if vecs_full.get("model_tag") != model_tag:
        raise ValueError(
            f"build_vectors の model_tag={vecs_full.get('model_tag')!r} が "
            f"要求 model_tag={model_tag!r} と不一致。環境変数 POX_EMBED_MODEL_TAG を確認してください。"
        )
    stored = {k: list(vecs_full[k]) for k in VECTOR_KEYS}
    cache[cand_id] = (stored, h)
    return stored


def is_cached(cand_id, profile, model_tag, cache, necessity_text=""):
    """
    この候補が現在のキャッシュでヒットするか（get_or_build が build を呼ばずに返せるか）。
    ヒット率の計測用。get_or_build と同じ判定基準（cand_id 一致かつ text_hash 一致）。
    """
    hit = cache.get(cand_id)
    return hit is not None and hit[1] == _text_hash(profile, necessity_text, model_tag)


def cache_stats(cache):
    """キャッシュ件数（デバッグ・進捗表示用）。"""
    return {"entries": len(cache)}
