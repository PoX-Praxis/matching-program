"""ラベルの機械の確かめ（Sakana の C8。KAORU×PA の試しの「画面へ」と同じ）。
- 関門（表 §1）は力の側にかける（候補を作るときに除いた。ここでも力・関心の部品に当たれば unclear）
- 条件（表 §7）は両側にかける
- 決め手の語は same_kind の組にだけ、辞書形で当てる（照合の表の当て方）。relational は語を見ない"""
import json
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))
import match_table as MT  # noqa: E402

COND = json.load(open(os.path.join(_ROOT, "data", "match_tables", "v0.3.2-dev", "conditions.json"), encoding="utf-8"))["rows"][-1]["match"]


def parse(raw):
    """モデルの出力から JSON を取り出す（最初の「{」から最後の「}」まで）。読めなければ {}。"""
    s = str(raw or "")
    i, j = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[i:j + 1])
    except ValueError:
        return {}


def words(v):
    """key の値を語に分ける（・、，／空白で並べた語は 1 語ずつ）。"""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [w for x in v for w in words(x)]
    return [w for w in (re.sub(r"[「」『』]", "", x).strip() for x in re.split(r"[・、，,／/\s]+", str(v))) if w]


def has_lemma(text, w):
    """照合の表の当て方（辞書形でも当てる・形態素の切れ目に合う位置だけ）。"""
    t1, t2 = MT._VER.set("v0.3.2-dev"), MT._WORDS.set("lemma")
    try:
        return MT.Span(MT.tokenize(text)).has(w)
    finally:
        MT._VER.reset(t1)
        MT._WORDS.reset(t2)


def cond_clash(a, b):
    """表 §7 の必要の文の条件：片方に条件の語、もう片方にはっきり違う条件。両側にかける。"""
    ha = [w for w in COND["need_words"] if w in a]
    hb = [w for w in COND["need_words"] if w in b]
    ca = [w for w in COND["conflict_words"] if w in a]
    cb = [w for w in COND["conflict_words"] if w in b]
    return bool((ha and cb) or (hb and ca))


def check_labels(label_texts, cands, atoms):
    """label_texts: {"N_by_O": 出力の文字列, "O_by_N": …}。cands: 番号 → 候補。atoms: id → 部品。
    返り値: 番号 → {cand, link, final, why, lab}（同じ番号の 2 つ目からは捨てる）。"""
    labels = {}
    for pid in ("N_by_O", "O_by_N"):
        for lab in parse(label_texts.get(pid)).get("labels") or []:
            if not isinstance(lab, dict) or str(lab.get("pair")) not in cands:
                continue
            c = cands[lab["pair"]]
            if c["pair"] in labels:
                continue
            link, why = str(lab.get("link") or ""), []
            L, R = atoms[c["left"]], atoms[c["right"]]
            if R["kind"] in ("力", "関心") and R["exclude"]:
                why.append("関門（§1）が力の側の引用に当たる")
            if cond_clash(L["text"], R["text"]):
                why.append("条件（§7）が違う")
            if link == "same_kind":
                key = lab.get("key") if isinstance(lab.get("key"), dict) else {}
                sw, pw = words(key.get("side")), words(key.get("power"))
                if not sw or not pw or not all(has_lemma(L["text"], w) for w in sw) or not all(has_lemma(R["text"], w) for w in pw):
                    why.append("same_kind の決め手の語が部品の文に無い（辞書形で当てた）")
            final = "unclear" if why and link not in ("irrelevant", "unclear") else link
            labels[c["pair"]] = {"cand": c, "link": link, "final": final, "why": why, "lab": lab}
    return labels
