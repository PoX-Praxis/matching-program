"""接続の説明 — どの相手について作るか（指示書69 §2・仕様 §12-5）。

候補の線: 必要像の要素ごとに、nomic の C2+b-1 で b1 = min(g − q99_n, g − q99_s) が δ（−0.020）を超える相手の文を
持つ人を、その要素の候補とする（66C の要素の層と同じ式。g = (1 + cos) / 2）。
  q99_n: その必要像の文と、背景の与え像の形の文（bg-v2 の推定用）との g の 99% 点
  q99_s: 背景の必要像の形の文（bg-v2 の推定用）と、その相手の文との g の 99% 点
向き（N の目的 × O の文）ごとに、N の必要像のどれかの要素に候補があれば、その向きを作る。

上層の結果（目的を満たすか）・件数・順位は使わない（§2・仕様 §12-8）。数値は画面と API に出さない。
"""
import numpy as np

LINE = "C2+b1"
DELTA = -0.020
BACKGROUND = "bg-v2"
CANDIDATE_VERSION = "cand-v1"


def judgment_version(model_tag, schema_version="v5", delta=DELTA, line=LINE, background=BACKGROUND):
    """候補の線の記録（judgment_version）: 線・δ・背景・必要像の書き方の版・埋め込みのモデル。"""
    return f"{CANDIDATE_VERSION};line={line};delta={delta:+.3f};bg={background};schema={schema_version};model_tag={model_tag}"


def _unit(m):
    m = np.asarray(m, dtype=float)
    if m.ndim == 1:
        m = m[None, :]
    n = np.linalg.norm(m, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return m / n


def g_matrix(Q, P):
    return (1.0 + _unit(Q) @ _unit(P).T) / 2.0


class Background:
    """背景の文のベクトル（与え像の形＝passage・必要像の形＝query）。q99 を出す。"""

    def __init__(self, offer_form, need_form):
        self.P = _unit(offer_form)
        self.Q = _unit(need_form)

    def q99_need(self, need_vecs):
        return np.quantile(g_matrix(need_vecs, self.P), 0.99, axis=1)

    def q99_sentence(self, sent_vecs):
        return np.quantile(g_matrix(self.Q, sent_vecs), 0.99, axis=0)


def element_hits(need_vecs, sent_vecs, bg, delta=DELTA):
    """必要像の要素 × 相手の文の通過（bool の行列）。どちらかが空なら空。"""
    if not len(need_vecs) or not len(sent_vecs):
        return np.zeros((len(need_vecs), len(sent_vecs)), dtype=bool)
    g = g_matrix(need_vecs, sent_vecs)
    b1 = np.minimum(g - bg.q99_need(need_vecs)[:, None], g - bg.q99_sentence(sent_vecs)[None, :])
    return b1 > delta


def is_candidate(need_vecs, sent_vecs, bg, delta=DELTA):
    """向き（N の必要像の要素 × O の文）に候補があるか。"""
    return bool(element_hits(need_vecs, sent_vecs, bg, delta).any())
