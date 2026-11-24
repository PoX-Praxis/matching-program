"""欄 → 節ごとの部品と、組の候補（KAORU×PA の試し 5 回目の役目 roles="v05" と同じ）。

- 節は照合の表 v0.3.1-dev §8-0 で切る。例を挙げる節（EX_MARK・EX_HEAD の形）は、本文と別の部品に切る
- 役目：側＝necessity・purpose／縛り＝bound（中身の語で lack・condition・prohibition。決められなければ lack）／
  力＝experience・offer・state.have・state.can_type／関心＝interest／文脈＝why・background・means（候補にしない）
- 表 v0.3.2-dev §1（関門）と例示の形は「外すもの」の検出だけに使う（表 §2 は使わない）
- 候補：A＝x の側 × y の力・関心（関心は x の側の目的の種類が共鳴のときだけ）／B＝x の縛り × y の力／C＝x の側 × y の縛り。
  外すものに当たる力・関心の部品は除く"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))
import match_table as MT  # noqa: E402

SPLIT_V, GATE_V = "v0.3.1-dev", "v0.3.2-dev"
EX_MARK = ("に見られるように", "などのように", "のように", "といったように")
EX_HEAD = ("例えば", "たとえば")
BOUND_PROHIBITION = re.compile(r"てはいけない|てはならない|禁止|NG|避け|控え")
BOUND_CONDITION = re.compile(r"だけ|のみ|しか|に限|限り|以外|曜|平日|週末|休日|午前|午後|夜間|時間帯")
BASIS = {"experience": "経験", "offer": "申告", "state.have": "資源", "state.can_type": "能力"}
POWER = ("申告", "資源", "能力")


class ver:
    """照合の表の版と、語の当て方（辞書形）を、この中だけ切り替える。"""

    def __init__(self, v):
        self.v = v

    def __enter__(self):
        self.t = (MT._VER.set(self.v), MT._WORDS.set("lemma"))

    def __exit__(self, *a):
        MT._VER.reset(self.t[0])
        MT._WORDS.reset(self.t[1])


def bound_sub(text):
    """縛りを中身で分ける（機械の語の規則。決められなければ lack）。"""
    if BOUND_PROHIBITION.search(text):
        return "prohibition"
    if BOUND_CONDITION.search(text):
        return "condition"
    return "lack"


def kind_of(fk):
    f = fk.split(".", 1)[1]
    if f.startswith(("necessity", "purpose")):
        return "側"
    if f == "state.bound":
        return "縛り"
    if f == "experience" or f.startswith("offer.") or f in ("state.have", "state.can_type"):
        return "力"
    if f.startswith("interest."):
        return "関心"
    if f == "state.unsorted":
        return "未分類"
    return "文脈"


def basis_of(fk):
    f = fk.split(".", 1)[1].split("#")[0]
    return BASIS.get("offer" if f.startswith("offer.") else f)


def purpose_of(fk, META):
    m = re.search(r"\.(?:necessity|purpose|means)\.p(\d+)", fk)
    if not m:
        return None, None
    pk = f"{fk[0]}.purpose.p{m.group(1)}"
    return pk, META.get(pk, {}).get("目的の種類")


def _split_example(c):
    """例を挙げる節を、本文と別の部品に切る。返り値: [(Clause, 例か)]。"""
    toks = c.toks
    for i, t in enumerate(toks):
        if t.surface == "、":
            head = c.sentence[toks[0].start:t.start]
            if head.endswith(EX_MARK) or head.startswith(EX_HEAD):
                a, b = toks[:i + 1], toks[i + 1:]
                if b:
                    return [(MT.Clause(c.sentence, a, c.index), True)] + _split_example(MT.Clause(c.sentence, b, c.index))
    txt = c.text
    return [(c, txt.endswith(EX_MARK) or txt.startswith(EX_HEAD))]


def build_atoms(F, META):
    """F（欄の名前 → 文。一人目が先）から部品を作る。部品の id は「欄の名前#その欄の中の番号」。"""
    atoms = []
    for fk, text in F.items():
        pos, k = 0, 0
        for sent in re.findall(r"[^。]+。?", text):
            s0 = text.find(sent, pos)
            pos = s0 + len(sent)
            with ver(SPLIT_V):
                cls = [c for c in MT.split_clauses(sent) if c.toks]
            for c in cls:
                for part, is_ex in _split_example(c):
                    if not part.toks:
                        continue
                    k += 1
                    with ver(GATE_V):
                        gates = MT.gate_hits(part)
                    out = (["例示"] if is_ex else []) + gates
                    pk, pkind = purpose_of(fk, META)
                    kd = kind_of(fk)
                    atoms.append({"id": f"{fk}#{k}", "field": fk, "who": fk[0], "kind": kd, "text": part.text,
                                  "start": s0 + part.start, "end": s0 + part.end, "exclude": out, "purpose": pk, "purpose_kind": pkind,
                                  "bound": bound_sub(part.text) if kd == "縛り" else None, "basis": basis_of(fk) if kd == "力" else None})
    return atoms


def candidates(atoms, x, y):
    """x_need_met_by_y の候補。返り値: ({"A": [(左, 右)], "B": …, "C": …}, 数の要約)。"""
    side = [a for a in atoms if a["who"] == x and a["kind"] == "側"]
    bnd = [a for a in atoms if a["who"] == x and a["kind"] == "縛り"]
    pw = [a for a in atoms if a["who"] == y and a["kind"] == "力"]
    it = [a for a in atoms if a["who"] == y and a["kind"] == "関心"]
    ybnd = [a for a in atoms if a["who"] == y and a["kind"] == "縛り"]
    pwk, itk = [a for a in pw if not a["exclude"]], [a for a in it if not a["exclude"]]
    out = {"A": [], "B": [], "C": []}
    for s in side:
        out["A"] += [(s, p) for p in pwk]
        if s["purpose_kind"] == "共鳴":
            out["A"] += [(s, p) for p in itk]
        out["C"] += [(s, b) for b in ybnd]
    for b in bnd:
        out["B"] += [(b, p) for p in pwk]
    return out, {"side": len(side), "bound": len(bnd), "power": len(pw), "power_kept": len(pwk), "interest": len(it),
                 "interest_kept": len(itk), "y_bound": len(ybnd)}


def purposes_of(F, META, who):
    """who（"A"・"B"）の目的の並び。目的の欄が無い人（v4）は、必要像ぜんぶを 1 つの目的（補完）として扱う。"""
    ps = sorted({k.split("#")[0] for k in F if k.startswith(f"{who}.purpose.")})
    if ps:
        return [{"n": p.rsplit(".p", 1)[1], "field": p, "title": F[p].rstrip("。"), "kind": META[p].get("目的の種類"),
                 "needs": sorted(k for k in F if k.startswith(f"{who}.necessity.p{p.rsplit('.p', 1)[1]}."))} for p in ps]
    return [{"n": "1", "field": None, "title": "", "kind": "補完", "needs": sorted(k for k in F if k.startswith(f"{who}.necessity."))}]


def side_ids_of(atoms, who, p):
    """目的 p に属する、who の側の部品の id（必要像と目的の文）。"""
    return [a["id"] for a in atoms if a["who"] == who and a["kind"] == "側" and
            (a["field"].split("#")[0] in p["needs"] or a["field"].split("#")[0] == p["field"])]
