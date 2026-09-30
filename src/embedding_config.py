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
# REQUESTED_MODEL_TAG は「どのモデルの次元・prefix で作るか」。実際に行へ付けるタグ（MODEL_TAG）は
# バックエンドの解決後に決める（下の「タグの規則」。stub は実タグと分離する）。
REQUESTED_MODEL_TAG = os.environ.get("POX_EMBED_MODEL_TAG", "qwen3-embedding-0.6b-d1024")

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
    FULL_DIM = MODEL_DIMS.get(REQUESTED_MODEL_TAG)
    if FULL_DIM is None:
        raise ValueError(
            f"MODEL_TAG={REQUESTED_MODEL_TAG!r} の FULL_DIM が未確定です。"
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
    # キーは MODEL_TAG（MODEL_DIMS と同じ表記）に一致させる。
    # 以前は "qwen3-emb-0.6b" で MODEL_TAG と不一致 → フォールバック経由で解決していた。
    "qwen3-embedding-0.6b-d1024": {
        "symmetric": "",
        "query": (
            "Instruct: Given a person's need description, "
            "retrieve people whose profile can satisfy that need.\nQuery: "
        ),
        "passage": "",
    },
    # EmbeddingGemma（google/embeddinggemma-300m）公式プロンプト書式。
    #   symmetric : 対称類似（STS）→ "task: sentence similarity | query: "
    #   query     : 検索クエリ（必要像）→ "task: search result | query: "
    #   passage   : 検索ドキュメント（候補の現状）→ "title: none | text: "
    "embgemma-300m": {
        "symmetric": "task: sentence similarity | query: ",
        "query":     "task: search result | query: ",
        "passage":   "title: none | text: ",
    },
    # Nomic Embed v2（nomic-embed-text-v2-moe）公式 task prefix。
    #   symmetric : 両側同一 prefix で対称比較 → "search_query: "
    #   query     : クエリ側（必要像）→ "search_query: "
    #   passage   : ドキュメント側（候補の現状）→ "search_document: "
    "nomic-emb-v2": {
        "symmetric": "search_query: ",
        "query":     "search_query: ",
        "passage":   "search_document: ",
    },
}

PREFIX = _PREFIX_BY_MODEL.get(REQUESTED_MODEL_TAG, _PREFIX_BY_MODEL["qwen3-embedding-0.6b-d1024"])

# ── バックエンド選択（stub | qwen3 | embgemma | nomic）────────────────────
# 解決は「環境変数 → コードの既定」の 2 段だけ。backend と model_tag は別の環境変数。
BACKEND = os.environ.get("POX_EMBED_BACKEND", "stub")
BACKEND_ENV_SET = "POX_EMBED_BACKEND" in os.environ
MODEL_TAG_ENV_SET = "POX_EMBED_MODEL_TAG" in os.environ

# ── タグの規則（指示書55-2 B-1-4）─────────────────────────────────────────
# 照合は同じ model_tag の行どうしでしか行わない。stub（意味を持たない擬似ベクトル）が実モデルの
# タグで保存されると、実物と同じ母集団に混ざって後から区別できない。そこで:
#   - stub のタグは常に "stub-d<次元>"（実タグと必ず分離される＝混在が起きえない）
#   - 実バックエンドのタグは "<backend>-" で始まること（既存の nomic-emb-v2 等は元々この形で、
#     改名しない）。backend とタグが食い違う設定（例: backend=nomic・tag=qwen3-…）は起動時に止める。
if BACKEND == "stub":
    MODEL_TAG = f"stub-d{FULL_DIM}"
else:
    MODEL_TAG = REQUESTED_MODEL_TAG
    if not MODEL_TAG.startswith(f"{BACKEND}-"):
        raise ValueError(
            f"POX_EMBED_BACKEND={BACKEND!r} と POX_EMBED_MODEL_TAG={MODEL_TAG!r} が食い違っています"
            f"（タグは '{BACKEND}-' で始まる必要があります）。"
        )

# 各バックエンドの推論サービス URL（常駐 FastAPI 等）。確定後に設定。
QWEN3_ENDPOINT   = os.environ.get("POX_QWEN3_ENDPOINT",   "")
EMBGEMMA_ENDPOINT = os.environ.get("POX_EMBGEMMA_ENDPOINT", "")  # TODO: ホスト確定後に設定
NOMIC_ENDPOINT   = os.environ.get("POX_NOMIC_ENDPOINT",   "")    # TODO: ホスト確定後に設定
