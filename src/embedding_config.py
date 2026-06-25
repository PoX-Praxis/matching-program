"""
PoX v4 embedding 設定（仕様書 C章 / H章の未確定値を一箇所に集約）

ここが embedding 次元・モデルタグ・prefix の単一ソース。
schema_v4 の vector 列サイズもここを参照する（列サイズと embedding 次元の不一致を防ぐ）。
値は実機検証（H-1/H-3）後に差し替える前提。ハードコードを散らさない。

実験ブランチ（exp/embedding-model-eval）では MODEL_TAG / BACKEND / ENDPOINT を
環境変数で切り替えて3モデルを同一コードで回す。FULL_DIM は MODEL_DIMS から自動決定。
"""
import os

# ── モデル・次元（A-3 / H-3）──────────────────────────────────────────────
MODEL_TAG = os.environ.get("POX_EMBED_MODEL_TAG", "qwen3-embedding-0.6b-d1024")

# モデルごとの出力次元（FULL_DIM）。3モデルすべて実機確認済み。
MODEL_DIMS = {
    "qwen3-embedding-0.6b-d1024": 1024,  # Qwen3-Embedding-0.6B（実機確認済み: dim=1024）
    "embgemma-300m":               768,  # EmbeddingGemma-300M（実機確認済み: dim=768）
    "nomic-emb-v2":                768,  # nomic-embed-text-v2-moe（実機確認済み: dim=768）
}

# POX_EMBED_FULL_DIM が明示設定されていれば最優先。なければ MODEL_DIMS から取得。
_dim_override = os.environ.get("POX_EMBED_FULL_DIM")
if _dim_override:
    FULL_DIM = int(_dim_override)
else:
    FULL_DIM = MODEL_DIMS.get(MODEL_TAG)
    if FULL_DIM is None:
        raise ValueError(
            f"MODEL_TAG={MODEL_TAG!r} の FULL_DIM が未確定です。"
            "実機で次元を確認し MODEL_DIMS に追記するか、"
            "POX_EMBED_FULL_DIM 環境変数で指定してください。"
        )

SHORT_DIM = 256    # MRL 切り詰め先。3モデル共通固定（設計書 §5）

# ── 定義域ガード（C-3 / Sakana #4）──────────────────────────────────────
EPS = 1e-6

# ── prefix 打ち分け（C-1 / H-1）─────────────────────────────────────────
# モデルごとに書式が異なる。実機で確認後に MODEL_TAG ごとの値を埋める。
# TODO: embgemma / nomic の prefix 書式を実機確認後に追記する。
#   symmetric : 対称（意志⇔意志, スコア a）
#   query     : 必要像（相補チャネルの query 側）
#   passage   : 現状・意志passage（相補チャネルの候補側, スコア b/c）

_PREFIX_BY_MODEL = {
    "qwen3-emb-0.6b": {
        "symmetric": "",
        "query": (
            "Instruct: Given a person's need description, "
            "retrieve people whose profile can satisfy that need.\nQuery: "
        ),
        "passage": "",
    },
    # TODO: EmbeddingGemma の prefix 書式を実機確認後に埋める
    # （"task: search result | query: " 等、Gemma 指定の書式に従う）
    "embgemma-300m": {
        "symmetric": None,  # TODO
        "query":     None,  # TODO
        "passage":   None,  # TODO
    },
    # TODO: Nomic Embed v2 の prefix 書式を実機確認後に埋める
    # （"search_query:" / "search_document:" の指定 prefix）
    "nomic-emb-v2": {
        "symmetric": None,  # TODO
        "query":     None,  # TODO
        "passage":   None,  # TODO
    },
}

PREFIX = _PREFIX_BY_MODEL.get(MODEL_TAG, _PREFIX_BY_MODEL["qwen3-emb-0.6b"])

# ── バックエンド選択（stub | qwen3 | embgemma | nomic）────────────────────
BACKEND = os.environ.get("POX_EMBED_BACKEND", "stub")

# 各バックエンドの推論サービス URL（常駐 FastAPI 等）。確定後に設定。
QWEN3_ENDPOINT   = os.environ.get("POX_QWEN3_ENDPOINT",   "")
EMBGEMMA_ENDPOINT = os.environ.get("POX_EMBGEMMA_ENDPOINT", "")  # TODO: ホスト確定後に設定
NOMIC_ENDPOINT   = os.environ.get("POX_NOMIC_ENDPOINT",   "")    # TODO: ホスト確定後に設定
