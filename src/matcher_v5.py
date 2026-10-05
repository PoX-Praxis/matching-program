#!/usr/bin/env python3
"""
照合の段3（指示書57）: 目的ごと・文単位の照合。

- 比べるのは「その目的の**必要像の文**」×「相手の**与え像の文**」の**全対**（型をまたぐ。型は表示の
  並べる順にだけ使う）。与え像が無い人（v4）は「現状」を与え像の代わりに使う（v4 互換）。
- 判定は **judge(need_vec, offer_vec) -> bool** の 1 か所に閉じ込める（後で含意判定に差し替えられる）。
  いまは文単位の cos（g(cos) が暫定の閾値以上）。判定の主軸は手作業検証（57 §3）で決める。
- ある向きが「成立」するのは: 判定を通る対が 1 つ以上あり、かつ **必須の文はすべて** 通る対を持つとき。
- 補完A（自分の目的の必要像 × 相手の与え像）と補完B（相手の目的の必要像 × 自分の与え像）は別々に判定する。
  入口はどちらかが成立。**互いに埋める**のは両方が成立したときだけ（56 の決定を守る）。
- 数値は外に出さない。返すのは引用の対（文の本文）と軸の名前だけ。
"""
from embedding_service import cosine, guard

SENTENCE_JUDGE_THRESHOLD = 0.70   # 【暫定】g(cos) の尺度。手作業検証（偽陽性・偽陰性の 2 条件）で調整する
MAX_PAIRS = 2                     # 根拠に出す引用の対は 1〜2 組


def judge(need_vec, offer_vec) -> bool:
    """1 対の判定（差し替え点）。いまは文単位の cos。"""
    return guard(cosine(need_vec, offer_vec)) >= SENTENCE_JUDGE_THRESHOLD


def _strength(need_vec, offer_vec) -> float:
    """内部の並べ替え用（どの対を引用するか）。外には出さない。"""
    return cosine(need_vec, offer_vec)


def direction(needs, offers):
    """一方向の判定。

    needs : [{"text", "vec", "required"}]（ある目的の必要像の文）
    offers: [{"text", "vec"}]（相手の与え像の文。v4 なら現状）
    戻り値: (成立したか, 引用の対 [{"need", "offer"}]（強い順に最大 2 組）)
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
    matched.sort(key=lambda x: x[2], reverse=True)
    return True, [{"need": n["text"], "offer": o["text"]} for n, o, _ in matched[:MAX_PAIRS]]


def match_pair(mine, theirs):
    """自分（mine）と相手（theirs）の照合。各 side は
       {"purposes": [{"purpose_id", "label", "needs": [...]}], "offers": [...]}。

    戻り値: {"by_purpose": {自分の purpose_id: 結果}, "theirs_need_me": 結果 or None}
      結果 = {"axis": "fill" | "mutual", "pairs": [{"kind": "fill_mine" | "fill_theirs", "mine", "theirs"}]}
    """
    # 補完B: 相手のどれかの目的の必要像が、自分の与え像で満たされるか（最初に成立した目的）
    b_ok, b_pairs = False, []
    for q in theirs["purposes"]:
        ok, pairs = direction(q["needs"], mine["offers"])
        if ok:
            b_ok, b_pairs = True, pairs
            break
    b_cards = [{"kind": "fill_theirs", "mine": p["offer"], "theirs": p["need"]} for p in b_pairs]
    out = {"by_purpose": {}, "theirs_need_me": None}
    for p in mine["purposes"]:
        ok, pairs = direction(p["needs"], theirs["offers"])
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
