"""
PoX v4 照合・ゲート設定（仕様書 D章/E章/H章の未確定値を外出し）

ここがゲート・結合・チャネル重み・shortlist の既定値の単一ソース。
母数到着までは保守的固定（H-2/H-5）。実績学習は範囲外。
"""
import os

# ── ゲート γ（D-2 / 2-3 / H-2）────────────────────────────────────────────
# gamma_max: c 経路（意志相補）ゲートの上限。母数到着まで保守的固定（H-2 未確定）。
#   値そのものは未確証。0.5 を保守的初期値として置く（要・実機較正）。
GAMMA_MAX = float(os.environ.get("POX_GAMMA_MAX", "0.5"))

# gate on/off の実装閾値（E-1 shortlist / E-2 score で seeker.gamma > GAMMA_EPS を判定）。
GAMMA_EPS = float(os.environ.get("POX_GAMMA_EPS", "1e-3"))

# ── 結合鋭さ p（2-2/2-4）──────────────────────────────────────────────────
# 既定 p≈0 = 幾何平均（soft-AND）。② が seeker ごとに連続発行し、これは初期値/フォールバック。
P_SHARPNESS_DEFAULT = float(os.environ.get("POX_P_SHARPNESS", "0.0"))

# ── チャネル重み α,β（1-8）初期値 ─────────────────────────────────────────
ALPHA_DEFAULT = float(os.environ.get("POX_ALPHA", "1.0"))   # 共鳴（類似 a）
BETA_DEFAULT  = float(os.environ.get("POX_BETA",  "1.0"))   # 補完（相補 complement）

# ── 一次絞り込み（E-1 / H-5）──────────────────────────────────────────────
SHORTLIST_K = int(os.environ.get("POX_SHORTLIST_K", "50"))  # 各経路の近傍数（H-5 未確定）
