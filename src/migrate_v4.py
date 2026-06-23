"""
PoX v3.1 → v4 遅延移行（仕様書 F-5 / Step 7）

既存 v3.1 seeker（4軸: 意志/求めている/能力/フェーズ + supporting_material）を
v4 の二軸（意志=will / 現状=state）+ supporting_raw に写像し、必要時に一度だけ
取り込む（lazy）。全件を前もって移行しない。フェーズ軸は v4 で廃止（破棄）。

写像（F-5）:
  意志            → will_text
  能力            → state_have（持っている力）
  求めている      → supporting_raw["求めている"]（②が検証・補強に使う申告）
  生テキスト      → supporting_raw["生テキスト"]（list 化）
  要約文          → supporting_raw["要約文"]
  系列素材        → supporting_raw["系列素材"]
  フェーズ        → 破棄（v4 に位相軸は無い）
  v3.1 に無い素材（意志要求/連言選言/チャネル重み, 動き方の型/制約）→ "未取得"

冪等: 既に profile_vectors にある（同 model_tag）なら再取り込みしない。
非破壊: 元の seekers/profiles テーブルは読むだけ。書き込みは v4 テーブルのみ。
"""
from embedding_config import MODEL_TAG
from db_v4 import ingest_profile_v4

MIGRATED_FROM = "v3.1"


def map_v31_to_v4(seeker_v31):
    """
    v3.1 seeker dict を v4 profile_input dict に写像する（純粋関数・副作用なし）。
    入力のどのキーが欠けても "未取得"/"" で安全に埋める（捏造はしない）。
    """
    if not isinstance(seeker_v31, dict):
        raise ValueError("seeker_v31 は dict である必要があります")

    sup_v31 = seeker_v31.get("supporting_material") or {}

    # 生テキストは v3.1 では文字列。v4 supporting は list を期待するため list 化。
    raw_text = seeker_v31.get("生テキスト")
    if isinstance(raw_text, str) and raw_text.strip():
        nama = [raw_text]
    elif isinstance(raw_text, list):
        nama = raw_text
    else:
        nama = "未取得"

    supporting_raw = {
        "生テキスト":       nama,
        "要約文":           sup_v31.get("要約文", "未取得"),
        "求めている":       seeker_v31.get("求めている", "未取得"),
        "系列素材":         sup_v31.get("系列素材", "未取得"),
        # v3.1 には存在しない素材は明示的に未取得（②が不確実性 u に反映する）
        "意志要求の素材":   "未取得",
        "連言選言の素材":   "未取得",
        "チャネル重み素材": "未取得",
    }
    # v3.1 固有素材は破棄せず原本として温存（非破壊・将来の再生成余地）
    if "attention候補" in sup_v31:
        supporting_raw["attention候補"] = sup_v31["attention候補"]

    return {
        "will_text":      seeker_v31.get("意志", ""),
        "state_have":     seeker_v31.get("能力", ""),
        "state_can_type": "",   # v3.1 に「動き方の型」は無い（未取得相当）
        "state_bound":    "",   # v3.1 に「縛られているもの」は無い
        "state_unsorted": "",
        "supporting_raw": supporting_raw,
    }


def migrate_seeker(store, user_id, seeker_v31, *, generator_fn=None,
                   model_tag=MODEL_TAG):
    """
    v3.1 seeker を v4 へ写像して取り込む（migrated_from='v3.1' を記録）。
    既存の ingest_profile_v4 を再利用するため redact/②生成/ベクトル化は共通経路。
    """
    profile_input = map_v31_to_v4(seeker_v31)
    return ingest_profile_v4(
        store, user_id, profile_input,
        generator_fn=generator_fn, model_tag=model_tag,
        migrated_from=MIGRATED_FROM,
    )


def ensure_migrated(store, user_id, seeker_loader, *, generator_fn=None,
                    model_tag=MODEL_TAG):
    """
    遅延移行のエントリポイント（F-5）。

    store に当該 (user_id, model_tag) の v4 ベクトルが既にあれば何もしない（冪等）。
    無ければ seeker_loader(user_id) で v3.1 seeker を取得し、移行する。
    seeker が見つからなければ移行しない（None を返す）。

    seeker_loader : user_id -> v3.1 seeker dict | None（例: db.get_seeker）。
    戻り値        : 移行した場合は ingest 結果 dict、既存/不在なら None。
    """
    if store.has_bundle(user_id, model_tag):
        return None
    seeker_v31 = seeker_loader(user_id)
    if not seeker_v31:
        return None
    return migrate_seeker(store, user_id, seeker_v31,
                          generator_fn=generator_fn, model_tag=model_tag)
