"""
PoX v4 embedding 設定（仕様書 C章 / H章の未確定値を一箇所に集約）

ここが embedding 次元・モデルタグ・prefix の単一ソース。
schema_v4 の vector 列サイズもここを参照する（列サイズと embedding 次元の不一致を防ぐ）。
値は実機検証（H-1/H-3）後に差し替える前提。ハードコードを散らさない。
"""
import os

# ── モデル・次元（A-3 / H-3）──────────────────────────────────────────────
MODEL_TAG = os.environ.get("POX_EMBED_MODEL_TAG", "qwen3-embedding-0.6b-d1024")
FULL_DIM  = 1024   # Qwen3-0.6B 固定（A-3）
SHORT_DIM = 256    # MRL 短次元（H-3: 損失 <1% を実機確認後に変更可）

# ── 定義域ガード（C-3 / Sakana #4）──────────────────────────────────────
EPS = 1e-6

# ── prefix 打ち分け（C-1 / H-1）─────────────────────────────────────────
# Qwen3 公式例に倣い query に英語 instruction / passage は無指示 を初期値に。
# 文言は実機で最適化（H-1）。三経路:
#   symmetric : 対称（意志⇔意志, スコア a）。無指示で対称近似。
#   query     : 必要像（相補チャネルの query 側）。
#   passage   : 現状・意志passage（相補チャネルの候補側, スコア b/c）。無指示。
PREFIX = {
    "symmetric": "",
    "query": (
        "Instruct: Given a person's need description, "
        "retrieve people whose profile can satisfy that need.\nQuery: "
    ),
    "passage": "",
}

# ── バックエンド選択（stub | qwen3）──────────────────────────────────────
# Qwen3 セルフホストが用意できるまでは stub（決定論的・非意味的）で前進する。
# 実 Qwen3 ホスト確定後に POX_EMBED_BACKEND=qwen3 で差し替え（後から差し替え可能な形）。
BACKEND = os.environ.get("POX_EMBED_BACKEND", "stub")

# qwen3 バックエンドの推論サービス URL（常駐 FastAPI 等）。確定後に設定。
QWEN3_ENDPOINT = os.environ.get("POX_QWEN3_ENDPOINT", "")
