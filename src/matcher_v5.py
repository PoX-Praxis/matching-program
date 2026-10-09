#!/usr/bin/env python3
"""
照合の段3（指示書57・61）: 目的ごと・文単位の照合。目的の内側では「共鳴が門、補完が価値」。

- 比べるのは「その目的の**必要像の文**」×「相手の**与え像の文**」の**全対**（型をまたぐ。型は表示の
  並べる順にだけ使う）。与え像が無い人（v4・0 文）は「現状」を与え像の代わりに使う（v4 互換）。
- 判定は **judge(need_vec, offer_vec) -> bool** の 1 か所に閉じ込める（後で含意判定に差し替えられる）。
  いまは文単位の cos（g(cos) が暫定の閾値以上）。判定の主軸は手作業検証で決める。
- **共鳴の門（指示書61 §2）**: その方向で求めている側の目的の「門の強さ」gate_s × (1 − gate_u) が
  RES_GATE_MIN 以上なら門を立てる。門は、その目的の**向かう先**と相手の**向かう先**（相手の全目的のうち
  最もよく当たるもの）の g(cos) が RES_THRESHOLD 以上のときだけ通す。共鳴は**点数に混ぜない**（通すか
  通さないかだけ）。門で落ちた相手の理由は返さない・記録しない（監査の値だけは audit_pair が返す）。
- 順序: 1. 共鳴の門 → 2. 必須の文の充足（判定を通る対が 1 つ以上・必須の文はすべて対を持つ）→
  3. 補完A（自分の目的 × 相手の与え像）・補完B（相手の目的 × 自分の与え像）のどちらかが成立すれば出す。
  **互いに埋める**のは両方が成立したときだけ（56 の決定を守る）。
- 数値は外に出さない。返すのは引用の対（文の本文）と軸の名前だけ。
"""
from embedding_service import cosine, guard
from match_config import RES_GATE_MIN, RES_THRESHOLD

SENTENCE_JUDGE_THRESHOLD = 0.70   # 【暫定】g(cos) の尺度。手作業検証（偽陽性・偽陰性の 2 条件）で調整する
MAX_PAIRS = 2                     # 根拠に出す引用の対は 1〜2 組


def judge(need_vec, offer_vec) -> bool:
    """1 対の判定（差し替え点）。いまは文単位の cos。"""
    return guard(cosine(need_vec, offer_vec)) >= SENTENCE_JUDGE_THRESHOLD


def _strength(need_vec, offer_vec) -> float:
    """内部の並べ替え用（どの対を引用するか）。外には出さない。"""
    return cosine(need_vec, offer_vec)


def direction(needs, offers):
    """一方向の補完の判定。

    needs : [{"text", "vec", "required"}]（ある目的の必要像の文）
    offers: [{"text", "vec"}]（相手の与え像の文。v4・0 文なら現状）
    戻り値: (成立したか, 引用の対 [{"need", "offer"}]（**必須の文の対を先に**、次に歓迎の文。各々強い順。最大 2 組）)
    """
    if not needs or not offers:
        return False, []
    matched = []
    for n in needs:
        hits = [(o, _strength(n["vec"], o["vec"])) for o in offers if judge(n["vec"], o["vec"])]
        if hits:
            o, st = max(hits, key=lambda x: x[1])
            matched.append((n, o, st))
        elif n.get("required"):
            return False, []                       # 必須の文が満たされない
    if not matched:
        return False, []
    matched.sort(key=lambda x: (not x[0].get("required"), -x[2]))
    return True, [{"need": n["text"], "offer": o["text"]} for n, o, _ in matched[:MAX_PAIRS]]


# ── 共鳴の門 ─────────────────────────────────────────────────────────────────
def gate_strength(purpose) -> float:
    """門の強さ = gate_s × (1 − gate_u)。値が無ければ 0（門を立てない）。"""
    try:
        s = float(purpose.get("gate_s") or 0.0)
        u = float(purpose.get("gate_u") or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, s) * max(0.0, 1.0 - u)


def resonance(dest_vec, other_dests):
    """向かう先どうしの共鳴: g(cos) の最大（相手の全目的のうち最もよく当たるもの）。測れなければ None。"""
    vals = [guard(cosine(dest_vec, d)) for d in (other_dests or []) if d]
    return max(vals) if (dest_vec and vals) else None


def judge_direction(purpose, other):
    """ある方向（purpose が other を求める）の判定。戻り値: (成立したか, 引用の対, 監査用の値)。

    purpose: {"needs", "dest_vec", "gate_s", "gate_u"}（求めている側の目的）
    other  : {"offers", "dests"}（求められている側）
    門を立てたのに共鳴を測れない（向かう先のベクトルが無い）ときは通さない。
    """
    strength = gate_strength(purpose)
    gate = strength >= RES_GATE_MIN
    res = resonance(purpose.get("dest_vec"), other.get("dests"))
    passed = (not gate) or (res is not None and res >= RES_THRESHOLD)
    info = {"resonance": res, "gate_strength": strength, "gate": gate, "gate_passed": passed}
    if not passed:
        info["complement"] = None
        return False, [], info
    ok, pairs = direction(purpose.get("needs") or [], other.get("offers") or [])
    info["complement"] = ok
    return ok, pairs, info


def match_pair(mine, theirs):
    """自分（mine）と相手（theirs）の照合。各 side は
       {"purposes": [{"purpose_id", "label", "needs", "dest_vec", "gate_s", "gate_u"}], "offers", "dests"}。

    戻り値: {"by_purpose": {自分の purpose_id: 結果}, "theirs_need_me": 結果 or None}
      結果 = {"axis": "fill" | "mutual", "pairs": [{"kind": "fill_mine" | "fill_theirs", "mine", "theirs"}]}
    """
    # 補完B: 相手のどれかの目的が、自分の与え像で満たされるか（門は相手の目的の値で立てる。最初に成立した目的）
    b_ok, b_pairs = False, []
    for q in theirs["purposes"]:
        ok, pairs, _ = judge_direction(q, mine)
        if ok:
            b_ok, b_pairs = True, pairs
            break
    b_cards = [{"kind": "fill_theirs", "mine": p["offer"], "theirs": p["need"]} for p in b_pairs]
    out = {"by_purpose": {}, "theirs_need_me": None}
    for p in mine["purposes"]:
        ok, pairs, _ = judge_direction(p, theirs)
        if not ok:
            continue
        a_cards = [{"kind": "fill_mine", "mine": x["need"], "theirs": x["offer"]} for x in pairs]
        out["by_purpose"][p["purpose_id"]] = {
            "axis": "mutual" if b_ok else "fill",
            "pairs": (a_cards[:1] + b_cards[:1]) if b_ok else a_cards,
        }
    if b_ok and not out["by_purpose"]:
        out["theirs_need_me"] = {"axis": "fill", "pairs": b_cards}
    return out


def audit_pair(mine, theirs):
    """監査ルート用（指示書61 §2-5）: 方向ごとの共鳴・門の強さ・門を立てたか・通ったか・補完の成否。
    本文は返さない。画面・公開 API には出さない。"""
    return {
        "a_to_b": [{"purpose_id": p.get("purpose_id"), **judge_direction(p, theirs)[2]} for p in mine["purposes"]],
        "b_to_a": [{"purpose_id": q.get("purpose_id"), **judge_direction(q, mine)[2]} for q in theirs["purposes"]],
    }
