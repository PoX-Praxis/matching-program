#!/usr/bin/env python3
"""指示書41 §5 — 合意判定（純粋関数）。

台帳・現在時刻・現在のメンバー集合／参加者集合に依存しない。判定に必要な入力を
すべて引数で受け取り、同じ入力からは常に同じ結論を返す（決定性・§10 禁則）。
段階2 で、台帳から「基準点(basis_seq)時点の分母」「賛成アカウント」「跨いだアンカー数」を
組み立ててこの関数に渡す。ここには DB アクセスも import も持ち込まない。

規則（§5-1）:
  期間内に誰も反対せず、かつ分母の過半数が賛成したとき成立する。
    - 意思表示 : 賛成・反対を明示する。沈黙は棄権（賛成として扱わない・§10 禁則）
    - 定足数   : 賛成した者が分母の過半数（半分より多い）
    - 可決     : 反対が1人もいない
    - 期間     : 基準点からアンカーを N 回跨ぐ（初期値 N=2）
  即時成立（§5-3）: 分母の全員が明示的に賛成した場合、期間を待たずに成立する。
  ブートストラップ（§5-4）: 分母が作成者1人なら作成者の賛成で成立（規則から自然に導かれる）。
"""

DEFAULT_PERIOD_ANCHORS = 2   # §5-1 期間 N の初期値（アンカー跨ぎ回数）


def evaluate_agreement(*, denominator, approvals, dissents, anchors_crossed,
                       period_anchors=DEFAULT_PERIOD_ANCHORS):
    """合意判定。

    引数:
      denominator     : 基準点時点の分母メンバー集合（頭数）。iterable
      approvals       : 明示的に賛成したアカウント。iterable
      dissents        : 明示的に反対したアカウント（公開トーク上の表明）。iterable
      anchors_crossed : 基準点以降に跨いだアンカー数（int >= 0）
      period_anchors  : 期間の閾値 N（既定 2）

    戻り値 dict:
      agreed    : bool  成立したか
      immediate : bool  §5-3 全員賛成による即時成立か
      reason    : str   判定理由（immediate_unanimous / majority / vetoed /
                        no_quorum / pending_period / no_denominator）
      approvers : list  分母内で賛成したアカウント（sorted）＝台帳に刻む approvals
      dissenters: list  分母内で反対したアカウント（sorted）
      quorum_of : int   分母の頭数
    """
    denom = {m for m in denominator if m}
    # 分母外の賛成・反対は数えない（判定は基準点の分母に対してのみ行う）。
    approvers = {a for a in approvals if a in denom}
    dissenters = {d for d in dissents if d in denom}

    n = len(denom)
    out = {"agreed": False, "immediate": False, "reason": "",
           "approvers": sorted(approvers), "dissenters": sorted(dissenters),
           "quorum_of": n}

    if n == 0:
        out["reason"] = "no_denominator"          # 分母なし＝判定不能・不成立
        return out

    # 反対が1人でもあれば成立しない（§5-1 可決条件・§5-6）。期間の途中でも止まる。
    if dissenters:
        out["reason"] = "vetoed"
        return out

    # §5-3 即時成立: 分母の全員が明示的に賛成（沈黙者がいない）。期間を待たない。
    if approvers == denom:
        out["agreed"] = True
        out["immediate"] = True
        out["reason"] = "immediate_unanimous"
        return out

    # 期間: 基準点から N 回アンカーを跨ぐまでは、まだ成立させない。
    if anchors_crossed < period_anchors:
        out["reason"] = "pending_period"
        return out

    # 期間満了・反対なし → 分母の過半数（半分より多い）が賛成していれば成立。
    if len(approvers) * 2 > n:
        out["agreed"] = True
        out["reason"] = "majority"
    else:
        out["reason"] = "no_quorum"
    return out
