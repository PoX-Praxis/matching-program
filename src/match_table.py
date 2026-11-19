"""照合の表（指示書77a）— 必要の句（1 つ）と相手の文（1 つ）が「応える」かを、LLM を使わずに表だけで決める。

- 表は data/match_tables/<版>/ のデータ（文書 docs/match_tables_<版>.md を写したもの）。ここには表の中身を書かない
- 版は match(..., version=) で選ぶ。v0.2（指示書77a 順1）は、そのときの結果が変わらないように残す。
  v0.3（指示書77a 追補1）は、節の単位と前の節をつなぐこと・文全体で見る関門と条件・概念の 3 列・内容語の辞書形・nomic を使わない
- 形態素解析は fugashi＋unidic-lite（版は requirements-bench.txt で固定。版と辞書は tool_versions() で記録する）
- 順は表の §8: 句に分ける → 関門 → 種類（必要の型との互換）→ 述語・枠 → 領域（概念・nomic の補助）→ 条件 → 関係と［C］
- 判定は 3 値（合う／合わない／分からない）。分からないは落とす。どこで落ちたかを句ごとに残す
- nomic は呼ばない。対象の句どうしの近さは sim(a, b) を外から渡す（渡さなければ、その段は「分からない」）
本番の画面・照合・記録には使わない（計測だけ）。
"""
import contextvars
import functools
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_ROOT = os.path.join(ROOT, "data", "match_tables")
VERSION = "v0.2"
TABLE_FILES = ("gates.json", "clause_kinds.json", "predicates.json", "objects.json", "concepts.json",
               "conditions.json", "need_types.json", "type_compat.json", "nominalize.json")
VERSIONS = ("v0.2", "v0.3", "v0.3.1-dev", "v0.3.2-dev")
WORDS = ("lemma", "surface")       # 内容語の当て方: 辞書形（lemma）・表記のまま（surface）
_VER = contextvars.ContextVar("match_table_version", default=VERSION)
_WORDS = contextvars.ContextVar("match_table_words", default="surface")
STEPS = ("句", "関門", "種類", "述語", "枠", "領域", "条件")
MODES = ("split", "whole")          # split: 77a §2-2 の 1（句に分ける）・whole: 文を 1 つの句とする（診断用）
PUNCT = ("、", "。", "，", "．")

# 形態素の並びを読むための機械の決まり（表の中身ではない）
_AUX_AFTER_TE = {"いる", "くる", "おく", "しまう", "もらう", "いただく", "いく", "みる", "ある", "くれる", "あげる"}
_AUX_AFTER_RENYO = {"始める"}
_FORMAL_NOUNS = {"ところ", "こと", "もの", "ため", "はず", "わけ"}
_NP_DELIMS = {"を", "に", "で", "が", "から", "へ", "は", "も", "より"}
_MODAL_ENDS = ("かもしれない", "だろう", "らしい", "そうだ", "と聞いている")
_E_ROW = {"う": "え", "く": "け", "ぐ": "げ", "す": "せ", "つ": "て", "ぬ": "ね", "ぶ": "べ", "む": "め", "る": "れ"}


# ── 表と道具 ────────────────────────────────────────────────────────────────────────────
def table_files(version=None):
    version = version or _VER.get()
    return TABLE_FILES + (("units.json",) if version != "v0.2" else ()) + (("readings.json",) if version == "v0.3.2-dev" else ())


def load_tables(version=None):
    return _load_tables(version or _VER.get())


@functools.lru_cache(maxsize=None)
def _load_tables(version):
    folder = os.path.join(TABLE_ROOT, version)
    out = {}
    for name in table_files(version):
        with open(os.path.join(folder, name), encoding="utf-8") as f:
            out[name.rsplit(".", 1)[0]] = json.load(f)
    return out


def table_digest(version=None):
    """表のデータのファイルの sha256（ファイル名の順に並べた一覧）。記録の一式に入れる。"""
    import hashlib
    version = version or _VER.get()
    folder = os.path.join(TABLE_ROOT, version)
    return {name: hashlib.sha256(open(os.path.join(folder, name), "rb").read()).hexdigest() for name in table_files(version)}


def _v02():
    return _VER.get() == "v0.2"


def _v031():
    """v0.3.1-dev 以降（v0.3.2-dev は v0.3.1-dev の上に、機械の読みだけを直した版）。"""
    return _VER.get() in ("v0.3.1-dev", "v0.3.2-dev")


def _v032():
    return _VER.get() == "v0.3.2-dev"


def _reading(name):
    """表 v0.3.2-dev の機械の読み（文書 §0 の v0.3.2-dev の変更・readings.json）の match。"""
    return next(r["match"] for r in load_tables()["readings"]["readings"] if r["#"] == name)


def _is_final_particle(t):
    """v0.3.2-dev M3: 文末の終助詞「よ」「ね」。"""
    return t.pos1 == "助詞" and t.pos2 == "終助詞" and t.surface in _reading("M3")["final_particles"]


def _m4_skip(t):
    """v0.3.2-dev M4: 関心の形の「に」と「関心」の間で飛ばす語（読点・副詞）。"""
    return t.surface in PUNCT or t.pos1 in _reading("M4")["skip"]


def _strip_tail(toks):
    """句の終わりの句読点と（v0.3.2-dev では）終助詞「よ」「ね」を除く。"""
    toks = list(toks)
    while toks and (toks[-1].surface in PUNCT or (_v032() and _is_final_particle(toks[-1]))):
        toks = toks[:-1]
    return toks


@functools.lru_cache(maxsize=None)
def _tagger():
    import fugashi
    return fugashi.Tagger()


def tool_versions():
    import importlib.metadata as md
    t = _tagger()
    info = t.dictionary_info[0] if t.dictionary_info else {}
    return {"fugashi": md.version("fugashi"), "unidic-lite": md.version("unidic-lite"),
            "dictionary": {"file": os.path.basename(info.get("filename", "")), "version": info.get("version"),
                           "size": info.get("size"), "charset": info.get("charset")}}


class Tok:
    __slots__ = ("surface", "lemma", "base", "pos1", "pos2", "ctype", "cform", "start", "end")

    def __init__(self, surface, lemma, base, pos1, pos2, ctype, cform, start, end):
        self.surface, self.lemma, self.base, self.pos1, self.pos2 = surface, lemma, base, pos1, pos2
        self.ctype, self.cform, self.start, self.end = ctype, cform, start, end

    def __repr__(self):
        return f"{self.surface}/{self.base}/{self.pos1}"


@functools.lru_cache(maxsize=4096)
def _tokenize_cached(text):
    out, pos = [], 0
    for w in _tagger()(text):
        f = w.feature
        s = text.find(w.surface, pos)
        s = pos if s < 0 else s
        base = f.orthBase if f.orthBase not in (None, "*") else w.surface
        lemma = f.lemma if f.lemma not in (None, "*") else w.surface
        out.append(Tok(w.surface, lemma.split("-")[0], base, f.pos1, f.pos2, f.cType, f.cForm, s, s + len(w.surface)))
        pos = s + len(w.surface)
    return tuple(out)


def tokenize(text):
    return list(_tokenize_cached(text))


# ── 語の当て方（形態素の切れ目に合う位置だけ数える）─────────────────────────────────────────────
class Span:
    """句の語の並び。語の照合は、句読点と空白を除いた表記の上で、形態素の切れ目に合う位置だけを数える。"""

    def __init__(self, toks):
        self.toks = [t for t in toks if t.surface.strip()]
        self.words = [t for t in self.toks if t.surface not in PUNCT]
        self.norm = "".join(t.surface for t in self.words)
        self.bounds, k = {0}, 0
        for t in self.words:
            k += len(t.surface)
            self.bounds.add(k)

    def find(self, term):
        term = "".join(ch for ch in term if ch not in PUNCT and not ch.isspace())
        if not term:
            return -1
        i = self.norm.find(term)
        while i >= 0:
            if i in self.bounds and i + len(term) in self.bounds:
                return i
            i = self.norm.find(term, i + 1)
        return -1

    def has(self, term):
        if self.find(term) >= 0:
            return True
        return _WORDS.get() == "lemma" and self.has_base(term)

    def has_base(self, term):
        """辞書形で当てる: 語の辞書形（書字形の基本形）の並びが、句の語の辞書形の並びに連続してあるか。"""
        tb = _term_bases(term)
        if not tb:
            return False
        bs = [t.base for t in self.words]
        n = len(tb)
        return any(tuple(bs[i:i + n]) == tb for i in range(len(bs) - n + 1))

    def ends(self, term):
        term = "".join(ch for ch in term if ch not in PUNCT)
        return self.norm.endswith(term) and (len(self.norm) - len(term)) in self.bounds


@functools.lru_cache(maxsize=4096)
def _term_bases(term):
    return tuple(t.base for t in tokenize(term) if t.surface not in PUNCT and t.surface.strip())


_CONJ_TAIL = ("が", "けれど", "けど", "ので", "のに")


def core_toks(toks):
    """句の終わりの読点と、節をつなぐ接続助詞（〜が・〜けれど・〜ので）を除いた語の並び（v0.3 の関門の形を見る）。"""
    out = _strip_tail([t for t in toks if t.surface.strip()])     # v0.3.2-dev M3: 文末の「よ」「ね」も除く
    while out and ((out[-1].pos1 == "助詞" and out[-1].pos2 == "接続助詞" and out[-1].surface in _CONJ_TAIL)
                   or (len(out) > 1 and out[-1].surface == "で" and out[-1].base == "だ" and out[-2].pos2 == "準体助詞")):
        out = out[:-2] if out[-1].surface == "で" and out[-1].base == "だ" else out[:-1]
    return out


# ── 句に分ける（77a §2-2 の 1）─────────────────────────────────────────────────────────────
def _is_compound_particle(toks, i):
    """「として」「について」の し・て（つい・て）は、述語ではなく助詞の一部。"""
    t = toks[i]
    if t.base == "する" and i > 0 and toks[i - 1].surface == "と" and i + 1 < len(toks) and toks[i + 1].surface == "て":
        return True
    if t.surface == "て" and i > 1 and toks[i - 1].base == "する" and toks[i - 2].surface == "と":
        return True
    if t.base in ("つく", "付く") and i > 0 and toks[i - 1].surface == "に" and i + 1 < len(toks) and toks[i + 1].surface == "て":
        return True
    if t.surface == "て" and i > 1 and toks[i - 1].base in ("つく", "付く") and toks[i - 2].surface == "に":
        return True
    return False


def _content_after(toks, i):
    """i の次の語が、前の述語に続く補助の語でない（新しい句が始まる）か。"""
    if i + 1 >= len(toks):
        return False
    n = toks[i + 1]
    if n.surface in PUNCT or n.pos1 in ("助動詞", "補助記号"):
        return False
    if n.pos1 == "助詞":
        return False
    if n.pos1 == "動詞" and n.pos2 == "非自立可能":
        return False
    return True


def _compound_noun_part(toks, i):
    """連用形の動詞が、すぐ後の名詞・接尾辞と一つの名詞になっているか（使い方・居場所・解き方）。「〜し」（サ変）は除く。"""
    t = toks[i]
    return (t.pos1 == "動詞" and str(t.cform).startswith("連用形") and t.base != "する" and i + 1 < len(toks)
            and toks[i + 1].pos1 in ("名詞", "接尾辞"))


def _split_points(toks):
    pts = []
    for i, t in enumerate(toks):
        if t.surface == "、":
            pts.append(("読点", i))
        elif t.pos1 == "助詞" and t.pos2 == "接続助詞" and t.surface in ("て", "で") and not _is_compound_particle(toks, i):
            if _content_after(toks, i):
                pts.append(("て", i))
        elif t.pos1 in ("動詞", "助動詞") and str(t.cform).startswith("連用形") and not _is_compound_particle(toks, i):
            if t.pos1 == "動詞" and _content_after(toks, i) and toks[i + 1].pos1 != "動詞" \
                    and not _compound_noun_part(toks, i):
                pts.append(("し", i))
        if not _v02() and i + 1 < len(toks) and toks[i + 1].surface != "、" and all(x != i for _, x in pts):
            # v0.3 §8-0: 〜が・〜けれど・〜ので・〜なら も節の切れ目（読点が続くときは読点で切る）
            if (t.pos1 == "助詞" and t.pos2 == "接続助詞" and t.surface in ("が", "けれど", "けど", "ので")) \
                    or (t.surface == "なら" and t.pos1 == "助動詞") \
                    or (t.surface == "で" and t.base == "だ" and i > 0 and toks[i - 1].pos2 == "準体助詞"):
                pts.append(("が・ので・なら", i))
    return pts


def _ends_with_predicate(seg):
    """句の終わりが述語の形（連用・終止・接続助詞・だ の連用）か。名詞・格助詞・「なら」で終わるものは述語の句にしない。"""
    toks = [t for t in seg if t.surface not in PUNCT]
    if _v032():
        toks = _strip_tail(toks)                                      # M3: 文末の「よ」「ね」
    if not toks:
        return False
    t = toks[-1]
    if _is_compound_particle(toks, len(toks) - 1):
        return False
    if t.pos1 == "助詞" and t.pos2 == "接続助詞":
        return True
    if t.pos1 in ("動詞", "形容詞") and not str(t.cform).startswith("連体形"):
        return True
    if t.pos1 == "動詞" and len(toks) == len(seg):
        return True
    if t.pos1 == "助動詞" and t.base not in ("だ",) and not str(t.cform).startswith("連体形"):
        return True
    if t.pos1 == "助動詞" and t.base == "だ" and str(t.cform).startswith("連用形"):
        return True
    if not _v02() and t.pos1 == "助動詞" and t.base in ("だ", "です") and str(t.cform).startswith("終止形"):
        return True                                                   # v0.3: 文の終わりの「〜好きだ」も節
    return False


class Clause:
    def __init__(self, sentence, toks, index):
        self.sentence, self.index = sentence, index
        self.toks = [t for t in toks if t.surface != "。"]
        self.start = self.toks[0].start if self.toks else 0
        self.end = self.toks[-1].end if self.toks else 0
        while self.toks and self.toks[-1].surface in PUNCT:
            self.toks.pop()
            self.end = self.toks[-1].end if self.toks else self.start
        self.text = sentence[self.start:self.end]
        self.span = Span(self.toks)


def split_clauses(sentence, mode="split"):
    """相手の文を述語ごとの句にする。読点・「〜て」・「〜し」（連用）の切れ目で切り、述語で終わらない切れ端は次の句につなぐ。
    mode="whole" は文を 1 つの句とする（診断用）。"""
    toks = [t for t in tokenize(sentence) if t.surface != "。"]
    if mode == "whole" or not toks:
        return [Clause(sentence, toks, 0)]
    segs, cur = [], []
    pts = {i: k for k, i in _split_points(toks)}
    for i, t in enumerate(toks):
        if pts.get(i) == "読点":
            cur.append(t)
            segs.append(cur)
            cur = []
            continue
        cur.append(t)
        if i in pts:
            segs.append(cur)
            cur = []
    if cur:
        segs.append(cur)
    merged, carry = [], []
    for seg in segs:
        seg = carry + seg
        if _ends_with_predicate(seg):
            merged.append(seg)
            carry = []
        else:
            carry = seg
    if carry:
        if merged:
            merged[-1] = merged[-1] + carry
        else:
            merged.append(carry)
    return [Clause(sentence, seg, k) for k, seg in enumerate(merged)]


# ── 中心の述語 ───────────────────────────────────────────────────────────────────────────
def is_potential(t):
    """可能動詞（関われる・貸し出せる・手伝える）か。表記の辞書形と、もとの動詞の形で見る。"""
    if t.pos1 != "動詞" or not str(t.ctype).startswith("下一段"):
        return False
    b, lem = t.base, t.lemma
    return (len(b) >= 3 and b.endswith("る") and lem and lem[-1] in _E_ROW and b[-2] == _E_ROW[lem[-1]]
            and b[:-2] == lem[:-1])


def verb_key(t):
    if is_potential(t):
        return t.base[:-2] + t.lemma[-1]
    return t.base


class Pred:
    """句の中心の述語。head は表の動詞と比べる鍵（サ変は「名詞＋する」・「N ができる」は「N をする」）。"""

    def __init__(self):
        self.head, self.head_i, self.verb_i = None, None, None
        self.potential = self.negated = self.passive = False
        self.chain, self.modality = [], None
        self.content, self.content_i = None, None      # 「〜立場で関われる」の〜・「〜ことができる」の〜
        self.inner, self.inner_i = None, None          # 「〜のが好き」の〜
        self.m1 = False                                # v0.3.2-dev M1 で可能と読んだ


def _strip_end(toks, forms):
    """句の終わりの決まった形（推量・伝聞）を切る。切った後の語の並び（読点は残す）と、切った形を返す。"""
    sp = Span(toks)
    for f in forms:
        if sp.ends(f):
            k = len(sp.norm) - len(f)
            keep, acc = [], 0
            for t in toks:
                if t.surface not in PUNCT and t.surface.strip():
                    if acc >= k:
                        break
                    acc += len(t.surface)
                keep.append(t)
            return keep, f
    return toks, None


def _cut_words(toks, n):
    """終わりから、句読点でない語を n 個切る。"""
    out = list(toks)
    while n > 0 and out:
        t = out.pop()
        if t.surface not in PUNCT:
            n -= 1
    return out


def analyze_pred(toks, src=None):
    p = Pred()
    p.src = src
    toks = _strip_tail([t for t in toks if t.surface.strip()])      # v0.3.2-dev M3: 文末の「よ」「ね」も除く
    toks, p.modality = _strip_end(toks, _MODAL_ENDS)
    core = core_toks(toks)
    if len(core) < len(toks) and Span(core).norm.endswith(("ことがない", "ことはない", "ことができる")):
        toks = core                                                   # 「記録したことはないが」の「が」を除いて見る
    sp = Span(toks)
    if sp.ends("ことがない") or sp.ends("ことはない"):
        toks = _cut_words(toks, 3)
        p.negated = True
    if sp.ends("ことができる") or sp.ends("ことができます"):
        toks = _cut_words(toks, 4 if sp.ends("ことができます") else 3)
        p.potential = True
    W = [i for i, t in enumerate(toks) if t.surface not in PUNCT]     # 句読点でない語の位置

    def tok(k):                                                       # k 番目の語（句読点を飛ばす）
        return toks[W[k]] if 0 <= k < len(W) else None
    j = len(W) - 1
    while j >= 0:
        t, prev = tok(j), tok(j - 1)
        if t.pos1 in ("助動詞", "終助詞") or (t.pos1 == "助詞" and t.pos2 in ("接続助詞", "準体助詞")):
            j -= 1
            continue
        if t.pos1 == "動詞" and t.base in _AUX_AFTER_TE and prev is not None and prev.surface in ("て", "で") \
                and prev.pos2 == "接続助詞":
            j -= 1
            continue
        if t.pos1 == "動詞" and t.base in _AUX_AFTER_RENYO and prev is not None and prev.pos1 == "動詞" \
                and str(prev.cform).startswith("連用形"):
            j -= 1
            continue
        if t.pos1 == "名詞" and t.base in _FORMAL_NOUNS and tok(j + 1) is not None and tok(j + 1).base == "だ":
            j -= 1
            continue
        break
    p.toks = toks
    if j < 0:
        return p
    p.chain = [tok(k) for k in range(j + 1, len(W))]
    t, prev, prev2 = tok(j), tok(j - 1), tok(j - 2)
    p.head_i = p.verb_i = W[j]
    if t.pos1 == "動詞" and t.base == "できる":
        p.potential = True
        if prev is not None and prev.pos1 == "名詞":
            p.head, p.head_i = prev.surface + "する", W[j - 1]
        elif prev is not None and prev.surface in ("が", "は") and prev2 is not None and prev2.pos1 == "名詞":
            p.head, p.head_i = prev2.surface + "をする", W[j - 2]
        else:
            p.head = "できる"
    elif t.pos1 == "動詞" and t.base == "する":
        if prev is not None and prev.pos1 == "名詞" and prev.pos2 != "数詞":
            p.head, p.head_i = prev.surface + "する", W[j - 1]
        elif prev is not None and prev.surface == "に" and prev2 is not None and prev2.pos1 == "名詞":
            p.head, p.head_i = prev2.surface + "にする", W[j - 2]
        else:
            p.head = "する"
    elif t.pos1 == "動詞" and t.base == "ある" and prev is not None and prev.surface in ("が", "は") \
            and prev2 is not None and prev2.surface == "関心":
        p.head, p.head_i = "関心がある", W[j - 2]
    elif t.pos1 == "形容詞" and t.base == "ない" and prev is not None and prev.surface in ("が", "は") \
            and prev2 is not None and prev2.surface == "関心":
        p.head, p.head_i, p.negated = "関心がある", W[j - 2], True
    elif _v032() and t.pos1 == "動詞" and t.base == _reading("F1")["verb"] and prev is not None \
            and prev2 is not None and [prev2.surface, prev.surface] == _reading("F1")["before"]:
        p.head, p.head_i = _reading("F1")["head_as"], W[j - 2]     # F1: 「関心を持つ」は「関心がある」と同じ述語
    elif t.pos1 == "動詞":
        p.head = verb_key(t)
        p.potential = p.potential or is_potential(t)
    else:
        p.head = t.base if t.pos1 in ("形容詞", "形状詞") else t.surface
    if p.chain and p.chain[0].pos1 == "助動詞" and p.chain[0].base in ("ない", "ぬ", "ず"):
        p.negated = True
    if p.chain and p.chain[0].pos1 == "助動詞" and p.chain[0].base in ("れる", "られる"):
        p.passive = True
        if _v032() and _m1_potential(t, p):
            p.passive, p.potential, p.m1 = False, True, True          # M1: 一段動詞＋「られる」を可能と読む
    if _v031() and any(c.pos1 == "助動詞" and c.base in ("ない", "ぬ", "ず") and k > 0 and p.chain[k - 1].pos1 == "動詞"
                       for k, c in enumerate(p.chain)):
        p.negated = True                                              # 表 §1「動詞＋ない」（記録していない：い＋ない）
    vk = W.index(p.verb_i)
    # 「〜立場で関われる」の〜
    if p.head == "関わる" and vk >= 3 and tok(vk - 1).surface == "で" and tok(vk - 2).surface == "立場" \
            and tok(vk - 3).pos1 == "動詞":
        p.content, p.content_i = verb_key(tok(vk - 3)), W[vk - 3]
    # 「〜のが好き・得意・趣味」の〜
    hk = W.index(p.head_i)
    if p.head in ("好き", "得意", "趣味") and hk >= 3 and tok(hk - 1).surface == "が" and tok(hk - 2).surface == "の" \
            and tok(hk - 3).pos1 == "動詞":
        p.inner, p.inner_i = verb_key(tok(hk - 3)), W[hk - 3]
    return p


def chain_bases(p):
    """述語の後の語の辞書形の並び。v0.3.2-dev M3: 縮んだ形「てる・でる」は「て・いる」「で・いる」と読む。"""
    if not _v032():
        return [t.base for t in p.chain]
    con = _reading("M3")["contracted"]
    out = []
    for t in p.chain:
        out += con[t.base] if t.pos1 == "助動詞" and t.base in con else [t.base]
    return out


def _m1_potential(v, p):
    """v0.3.2-dev M1: 一段動詞＋「られる」を可能と読むか。① 動詞に「〜を」の対象がある ② 「られる」の後が「ます」だけか、何も無い。"""
    m = _reading("M1")
    if v.pos1 != "動詞" or not str(v.ctype).startswith(tuple(m["verb_ctype"])) or not str(v.cform).startswith("未然形"):
        return False
    if not p.chain or p.chain[0].base != m["aux"]:
        return False
    rest = [t for t in p.chain[1:] if not (t.pos1 == "助詞" and t.pos2 == "接続助詞" and t.surface in _CONJ_TAIL)]
    if any(t.base not in m["after_allowed"] for t in rest):
        return False
    return bool(np_before(p.toks, p.head_i, m["object_marker"], p.src))


def chain_has(p, seq):
    bases = chain_bases(p)
    n = len(seq)
    return any(bases[i:i + n] == list(seq) for i in range(len(bases) - n + 1))


def is_past(p):
    return any(t.pos1 == "助動詞" and t.base == "た" for t in p.chain)


def period_of(span):
    """期間（数詞＋年・年間）。表の例（十年・五年間・八年間）の単位だけ。"""
    tabs = load_tables()
    units = tabs["clause_kinds"]["期間"]["match"]["数詞の後"]
    ws = span.words
    for i, t in enumerate(ws[:-1]):
        if t.pos2 == "数詞" and ws[i + 1].surface in units:
            k = i
            while k > 0 and ws[k - 1].pos2 == "数詞":
                k -= 1
            return "".join(w.surface for w in ws[k:i + 2])
    return None


# ── 名詞句（枠の穴・対象）───────────────────────────────────────────────────────────────
def np_before(toks, v, marker, src=None):
    """v の語の前で、いちばん近い marker（格助詞）の前の名詞句。
    marker を探すとき、間に別の述語（名詞の一部でない動詞・形容詞）があれば、その marker は別の述語のものとして探さない。
    名詞句は、ほかの格助詞・読点・述語で止める（連用形の動詞＋名詞・接尾辞は名詞の一部）。"""
    m = None
    for i in range(v - 1, -1, -1):
        t = toks[i]
        if t.surface == marker and t.pos1 == "助詞":
            m = i
            break
        if t.pos1 in ("動詞", "形容詞") and not _compound_noun_part(toks, i) and not _is_compound_particle(toks, i):
            return None
    if m is None:
        return None
    s = m
    while s > 0:
        t = toks[s - 1]
        if t.pos1 == "助詞" and t.surface in _NP_DELIMS:
            break
        if t.surface in PUNCT:
            break
        if (t.pos1 in ("動詞", "形容詞") and not _compound_noun_part(toks, s - 1)) or (t.pos1 == "助動詞" and t.base != "だ"):
            break
        s -= 1
    seg = [t for t in toks[s:m]]
    while seg and (seg[0].surface in PUNCT or seg[0].surface == "の"):
        seg.pop(0)
    if not seg:
        return None
    return _text(seg, src)


def _text(seg, src):
    """語の並びの、もとの文での連続した部分（空白もそのまま）。もとの文が無ければ語をつなぐ。"""
    if src is not None and seg:
        return src[seg[0].start:seg[-1].end]
    return "".join(t.surface for t in seg)


def object_np(p):
    """中心の述語の「を」の名詞句（無ければ None）。"""
    i = p.content_i if p.content_i is not None else p.head_i
    if i is None:
        return None
    return np_before(p.toks, i, "を", p.src)


def interest_np(p):
    """「〜に関心がある」の〜（句の始めから「に関心」の前まで）。"""
    if p.head != "関心がある" or p.head_i is None or p.head_i < 1:
        return None
    i = p.head_i - 1
    if _v032():                                                       # M4: 「に」と「関心」の間の読点・副詞
        while i > 0 and _m4_skip(p.toks[i]):
            i -= 1
    if p.toks[i].surface != "に":
        return None
    seg = [t for t in p.toks[:i]]
    while seg and seg[0].surface in PUNCT:
        seg.pop(0)
    return _text(seg, p.src) or None


# ── 対象の種類（§4）と概念（§5）─────────────────────────────────────────────────────────────
def object_kinds(text):
    """語の並びに出る §4 の例から、(種類, 下位の種類) の集合。"""
    sp = Span(tokenize(text)) if isinstance(text, str) else text
    out = []
    for r in load_tables()["objects"]["rows"]:
        if any(sp.has(w) for w in r["例"]):
            out.append((r["種類"], r["下位の種類"]))
    return out


def center_noun_kind(text):
    """中心の名詞（名詞句の終わりの名詞）の §4 の種類。§4 の例の語が、名詞句の終わりに（切れ目に合って）あるもの。"""
    if not text:
        return None
    toks = [t for t in tokenize(text) if t.surface not in PUNCT]
    while toks and toks[-1].pos1 not in ("名詞", "接尾辞"):
        toks.pop()
    if not toks:
        return None
    sp = Span(toks)
    for r in load_tables()["objects"]["rows"]:
        if any(sp.ends(w) for w in r["例"]):
            return r["種類"]
    return None


def concept_detail(span):
    """v0.3 §5: 概念ごとに、句にある領域の名詞・支える語・禁止語。"""
    out = {}
    for c in load_tables()["concepts"]["concepts"]:
        m = c["match"]
        out[c["概念"]] = {"領域の名詞": [w for w in m["domain"] if span.has(w)], "支える語": [w for w in m["support"] if span.has(w)],
                        "禁止語": [w for w in m["forbidden"] if span.has(w)]}
    return out


def hit_concepts(text):
    """v0.3.1-dev §3-2: 句が、概念の領域の名詞か支える語に当たる概念。"""
    if not text:
        return []
    det = concept_detail(Span(tokenize(text)))
    return [c for c, d in det.items() if d["領域の名詞"] or d["支える語"]]


def concepts_v03(span):
    """v0.3 §5: 句が概念に入るのは、領域の名詞が 1 つ以上あり、禁止語が無いとき（支える語だけでは入らない）。"""
    return [c for c, d in concept_detail(span).items() if d["領域の名詞"] and not d["禁止語"]]


def need_concepts(span):
    if not _v02():
        return concepts_v03(span)
    out = []
    for c in load_tables()["concepts"]["concepts"]:
        if all(any(span.has(w) for w in g) for g in c["match"]["need_groups"]):
            out.append(c["概念"])
    return out


def offer_concepts(span):
    if not _v02():
        return concepts_v03(span)
    out = []
    for c in load_tables()["concepts"]["concepts"]:
        if any(span.has(w) for w in c["match"]["phrases"]):
            out.append(c["概念"])
    return out


def hole_concepts(text):
    """枠の穴の名詞句が入る概念（まとめる句で見る）。"""
    if not text:
        return []
    return offer_concepts(Span(tokenize(text)))


# ── 述語の種類（§3-1）─────────────────────────────────────────────────────────────────────
def _verb_kind_candidates(key, obj_text, clause_span):
    """動詞の鍵と対象から、述語の種類の候補（並びは表の順）。"""
    tabs = load_tables()["predicates"]
    objsp = Span(tokenize(obj_text)) if obj_text else None
    direct = []
    for k in tabs["kinds"]:
        m = k["match"]
        if "gate" in m:
            continue
        for v, objs in m.get("verb_objects", []):
            if key == v and objsp is not None and any(objsp.has(o) for o in objs):
                direct.append((k["種類"], f"{'・'.join(objs)}を{v}"))
    if direct:
        return direct, "動詞と対象の組"
    light = tabs["軽い動詞"]["match"]
    if key in light["verbs"]:
        subs = []
        for kind, sub in (object_kinds(objsp) if objsp is not None else []):
            if sub and light["subkind_to_kind"].get(sub) and light["subkind_to_kind"][sub] not in [s for s, _ in subs]:
                subs.append((light["subkind_to_kind"][sub], f"軽い動詞＋{sub}"))
        return subs, "軽い動詞（対象の下位の種類）"
    for k in tabs["kinds"]:
        m = k["match"]
        if key in m.get("verbs", []):
            if "by_subkind" in m and objsp is not None:
                subs = [(m["by_subkind"][sub], f"{key}＋{sub}") for _, sub in object_kinds(objsp)
                        if sub in m.get("by_subkind", {})]
                if subs:
                    return subs, "動詞（対象の下位の種類）"
            return [(k["種類"], key)], "動詞"
    return [], "表に無い動詞"


def predicate_kind(p, clause_span, prefer=None):
    """句の述語の種類。複数の候補があるときは、必要の述語と同じ種類があればそれを使う（§5 の注に倣う）。
    返り値: (種類 or None, 動詞の鍵, 候補, 説明)"""
    key = p.content or p.head
    i = p.content_i if p.content_i is not None else p.head_i
    obj = np_before(p.toks, i, "を") if i is not None else None
    cands, how = _verb_kind_candidates(key, obj, clause_span)
    kinds = [c for c, _ in cands]
    # 複合（聞き取り＋制作）: 句の中に、聞き取りと制作・記録の述語が両方ある
    comp = load_tables()["predicates"]["複合"]
    for name, parts in comp.items():
        if kinds and kinds[0] in parts:
            others = set()
            for vi, t in enumerate(p.toks):
                if t.pos1 != "動詞" or vi == i:
                    continue
                vk = verb_key(t)
                if t.base == "する" and vi > 0 and p.toks[vi - 1].pos1 == "名詞":
                    vk = p.toks[vi - 1].surface + "する"
                c2, _ = _verb_kind_candidates(vk, np_before(p.toks, vi, "を"), clause_span)
                others.update(c for c, _ in c2)
            if set(parts) <= (others | {kinds[0]}):
                return name, key, kinds, f"{how}・複合"
    if not kinds:
        return None, key, kinds, how
    if len(set(kinds)) > 1:
        if prefer in kinds:
            return prefer, key, kinds, f"{how}・候補が複数（必要と同じ種類を使う）"
        return None, key, kinds, f"{how}・候補が複数で決まらない"
    return kinds[0], key, kinds, how


# ── 必要の句 ─────────────────────────────────────────────────────────────────────────────
class Need:
    pass


def _match_row(m, p, span, obj):
    if isinstance(m, list):
        return any(_match_row(x, p, span, obj) for x in m)
    if "head" in m and p.head not in m["head"]:
        return False
    if "chain" in m and not chain_has(p, m["chain"]):
        return False
    if "past" in m and is_past(p) != m["past"]:
        return False
    if "potential" in m and p.potential != m["potential"]:
        return False
    if "object_kind" in m and not any(k in m["object_kind"] for k, _ in object_kinds(span)):
        return False
    if "object_words" in m and not (obj and any(Span(tokenize(obj)).has(w) for w in m["object_words"])):
        return False
    return True


def analyze_need(text):
    """必要の文（「〜人。」）から、型・述語の種類・枠の穴・概念・条件を決める（原則 v1 §1・表 §3〜§7）。"""
    tabs = load_tables()
    n = Need()
    n.text = text
    toks = [t for t in tokenize(text) if t.surface != "。"]
    n.ends_with_person = bool(toks) and toks[-1].surface == "人"
    body = toks[:-1] if n.ends_with_person else toks
    n.span = Span(body)
    n.pred = analyze_pred(body, text)
    p = n.pred
    n.object = object_np(p)
    n.type = "型なし（照合しない）"
    for row in tabs["need_types"]["rows"]:
        if "match" in row and _match_row(row["match"], p, n.span, n.object):
            n.type = row["型"]
            break
    # 述語の種類
    n.pred_kind, n.verb = None, p.head
    if n.type == "関心":
        n.pred_kind = "関心"
        n.object = interest_np(p)
    elif n.type == "役割（能力）":
        # 「〜を持つ人」で対象が技術: 表 §3-3 の「実施・担当（技術）」の行（反例 S43 はこの必要の組）
        subs = [s for _, s in object_kinds(n.object or "")]
        n.pred_kind = "実施・担当（技術）" if "音響・照明" in subs else None
    elif n.type != "型なし（照合しない）":
        n.pred_kind, n.verb, _, _ = predicate_kind(p, n.span)
    n.object_place = any(k == "場所" for k, _ in object_kinds(n.span))
    # 枠の穴（必要の文で埋まっているもの）
    n.frame, n.holes = None, {}
    for fr in tabs["predicates"]["枠"]:
        if n.verb in fr["match"]["verbs"] or (p.content and p.content in fr["match"]["verbs"]):
            n.frame = fr["動詞"]
            for name, marker in fr["match"]["holes"]:
                h = np_before(p.toks, p.content_i if p.content_i is not None else p.head_i, marker, p.src)
                if h:
                    n.holes[name] = h
            break
    n.techniques = [w for w in next(r for r in tabs["objects"]["rows"] if r["下位の種類"] == "音響・照明")["例"]
                    if n.span.has(w)]
    n.concepts = need_concepts(n.span)
    cond = tabs["conditions"]["rows"][-1]["match"]
    n.conditions = [w for w in cond["need_words"] if n.span.has(w)]
    n.B = nominalize(text)
    return n


def nominalize(text):
    """［B］: 必要の句を「〜人」で終わらない形にする（生成仕様 v1.0 §6-1。規則は nominalize.json）。"""
    rules = load_tables()["nominalize"]["rules"]
    s = text.rstrip("。").rstrip()
    toks = [t for t in tokenize(s) if t.surface != "。"]
    if not toks or toks[-1].surface != "人":
        return None
    for r in rules:
        if not s.endswith(r["末尾"]):
            continue
        if "条件" in r:
            prev = toks[-2] if len(toks) > 1 else None
            if prev is None or prev.pos1 not in ("動詞", "助動詞") or not str(prev.cform).startswith("連体形"):
                continue
        return s[: len(s) - len(r["末尾"])] + r["置き換え"]
    return None


# ── 照合（表 §8 の順）──────────────────────────────────────────────────────────────────────
def _gate_hit(g, p, span, clause):
    for m in g["match"]:
        ok = True
        if "contains" in m and not span.has(m["contains"]):
            ok = False
        if ok and "end" in m:
            ok = span.ends(m["end"]) or (p.modality is not None and m["end"] == p.modality)
        if ok and "not_contains" in m and span.has(m["not_contains"]):
            ok = False
        if ok and "negated" in m and p.negated != m["negated"]:
            ok = False
        if ok and "passive" in m:
            passive = p.passive and not (not _v02() and p.head == "任す")   # 表 §1 の注「任されて」は受身に当てない
            ok = passive == m["passive"]
        if ok and "head" in m and p.head not in m["head"]:
            ok = False
        if ok and "chain" in m and not chain_has(p, m["chain"]):
            ok = False
        if ok and "aux_tai" in m:
            idx = [k for k, t in enumerate(span.words) if t.pos1 == "助動詞" and t.base == "たい"]
            ok = bool(idx) and all(t.base in m["aux_tai"] for t in span.words[idx[-1] + 1:])
        if ok and "verb_object" in m:
            ok = False
            for key, i in ((p.head, p.head_i), (p.inner, p.inner_i)):
                if key is None:
                    continue
                for v, objs in m["verb_object"]:
                    if key != v:
                        continue
                    if objs is None:
                        ok = True
                    else:
                        o1, o2 = np_before(p.toks, i, "を"), np_before(p.toks, i, "に")
                        ok = any(o and Span(tokenize(o)).has(w) for o in (o1, o2) for w in objs)
                    if ok:
                        break
                if ok:
                    break
        if ok:
            return True
    return False


def _drop_m4(toks):
    """v0.3.2-dev M4: 「に」と「関心」の間にある読点・副詞を除いた語の並び。"""
    a, b = _reading("M4")["between"]
    drop = set()
    for k, t in enumerate(toks):
        if t.surface != b:
            continue
        i = k - 1
        while i > 0 and _m4_skip(toks[i]):
            i -= 1
        if i < k - 1 and toks[i].surface == a:
            drop.update(range(i + 1, k))
    return [t for k, t in enumerate(toks) if k not in drop]


def _plain_end(span):
    """v0.3.2-dev M2・M3: 句の末尾を常体に直した語の並び。文末の「よ」「ね」と節をつなぐ接続助詞を除き、
    丁寧の「動詞＋ます」は動詞の辞書形に、縮んだ「てる・でる」（「〜てます」を含む）は「ている・でいる」に直す。"""
    ws = [t for t in core_toks(span.toks) if t.surface not in PUNCT]
    if not ws:
        return span
    con = _reading("M3")["contracted"]
    last, text = ws[-1], None
    if last.pos1 == "助動詞" and last.base == _reading("M2")["polite"] and str(last.cform).startswith("終止形") \
            and len(ws) >= 2:
        v, head = ws[-2], "".join(t.surface for t in ws[:-2])
        if v.pos1 == "助動詞" and v.base in con:
            text = head + "".join(con[v.base])
        elif v.pos1 == "動詞" and str(v.cform).startswith("連用形"):
            text = head + v.base
    elif last.pos1 == "助動詞" and last.base in con:
        text = "".join(t.surface for t in ws[:-1]) + "".join(con[last.base])
    return Span(tokenize(text)) if text else Span(ws)


def _form_ends(span, term):
    """表 §2 の形が句の末尾にあるか。v0.3.2-dev は丁寧の形・縮んだ形・文末の「よ」「ね」でも当てる（M2・M3）。"""
    return span.ends(term) or (_v032() and _plain_end(span).ends(term))


def _form_contains(span, term):
    """表 §2 の形が句にあるか。v0.3.2-dev は関心の形の「に」と「関心」の間の読点・副詞を飛ばす（M4）。"""
    return span.has(term) or (_v032() and Span(_drop_m4(span.toks)).has(term))


def clause_kind(p, span, sent=None):
    """句の種類（表 §2）。返り値: (種類, 期間) 。応えないものは「応えない：…」。
    v0.3: 使える立場の形は文全体から取る（sent）。"""
    tabs = load_tables()["clause_kinds"]
    kinds = {k["種類"]: k for k in tabs["kinds"]}
    period = period_of(span)
    core = Span(core_toks(span.toks))

    def ok(m):
        if "contains" in m and not _form_contains(span, m["contains"]):
            return False
        if "end" in m and not _form_ends(span, m["end"]):
            return False
        if "sentence_contains_any" in m and not any((sent or span).has(x) for x in m["sentence_contains_any"]):
            return False
        if "ends_any" in m and not any(_form_ends(core, x) for x in m["ends_any"]):
            return False
        if "period" in m and bool(period) != m["period"]:
            return False
        return _match_row({k: v for k, v in m.items() if k not in ("contains", "end", "period", "sentence_contains_any",
                                                                     "ends_any")}, p, span, None)

    if p.negated:
        return None, period
    for name in ("使える立場", "所有", "現職", "関心"):
        if any(ok(m) for m in kinds[name]["match"]):
            return name, period
    na = tabs["応えない"]["match"]
    if ok(na[0]):
        return "応えない：好み・自己評価・知識", period
    if any(ok(m) for m in kinds["経験"]["match"]):
        return "経験", period
    if any(ok(m) for m in kinds["継続"]["match"]):
        return "継続", period
    if any(ok(m) for m in kinds["申告"]["match"]):
        return "申告", period
    if ok(na[1]):
        return "応えない：現在の形・期間なし", period
    return None, period


class Step:
    """1 つの句の、段ごとの判定。"""

    def __init__(self, clause):
        self.clause = clause
        self.where, self.verdict, self.why = None, None, None
        self.trace = {}

    def fail(self, where, verdict, why):
        self.where, self.verdict, self.why = where, verdict, why
        return self

    def as_dict(self):
        return {"句": self.clause.text, "落ちた段": self.where, "判定": self.verdict, "理由": self.why, **self.trace}


def _gate_span(clause):
    """関門の形を見る語の並び。v0.2 は句のまま、v0.3 は終わりの接続助詞（〜が・〜ので）を除く。"""
    return clause.span if _v02() else Span(core_toks(clause.toks))


def gate_hits(clause, need_type=None, names=None):
    """句が当たる関門の名前。need_type を渡すと、効く必要の型の関門だけ。names を渡すと、その関門だけ。"""
    p = analyze_pred(clause.toks, clause.sentence)
    sp = _gate_span(clause)
    return [g["関門"] for g in load_tables()["gates"]["gates"]
            if (need_type is None or need_type in g["効く型"]) and (names is None or g["関門"] in names)
            and p.head is not None and _gate_hit(g, p, sp, clause)]


def _teach_frame_v031(need, holes, names, clause, sent, st):
    """表 v0.3.1-dev §3-2「教える」の比べ方。合えば None、落とすなら (判定, 理由)。"""
    teach = load_tables()["predicates"]["教えるの比べ方"]["match"]
    generic, ghole = set(teach["generic_nouns"]), teach["generic_hole"]
    chosen = {}
    for name in names:
        if name not in need.holes:
            continue                                                  # 必要に無い穴は比べない
        nh, oh = need.holes[name], holes.get(name)
        if name == ghole and nh in generic:
            st.trace.setdefault("枠（比べない穴）", []).append(f"〈{name}〉必要が汎用の名詞（{nh}）")
            continue
        if name == ghole and oh in generic:
            return "合わない", f"〈{name}〉相手の側だけが汎用の名詞（{oh}）で、必要は特定（{nh}）"
        nc = hit_concepts(nh)
        if not oh:
            # 相手の句に穴が無い: 相手の文全体に、穴の概念と同じ概念の語があれば合う（v0.3 から）
            common = set(nc) & set(offer_concepts(sent)) if sent is not None else set()
            if not common:
                return "分からない", f"〈{name}〉が相手の句に無く、文全体にも同じ概念の語が無い（必要「{nh}」{nc}）"
            st.trace.setdefault("枠（文全体で見た穴）", {})[name] = sorted(common)
        else:
            oc = hit_concepts(oh)
            common = set(nc) & set(oc)
            if not common:
                return "合わない", f"〈{name}〉が同じ概念の領域の名詞・支える語に当たらない（必要「{nh}」{nc}・相手「{oh}」{oc}）"
        chosen[name] = sorted(common)
    det = concept_detail(clause.span)
    for name, cs in chosen.items():
        ok = [c for c in cs if det[c]["領域の名詞"] and not det[c]["禁止語"]]
        if not ok:
            forb = sorted({w for c in cs for w in det[c]["禁止語"]})
            if forb:
                return "合わない", f"〈{name}〉の概念（{'・'.join(cs)}）の禁止語が節にある（{'・'.join(forb)}）"
            return "分からない", f"〈{name}〉の概念（{'・'.join(cs)}）の領域の名詞が節に無い"
    st.trace["枠（教える・v0.3.1-dev）"] = chosen
    return None


def _check_clause(need, clause, sim, threshold, sent=None):
    tabs = load_tables()
    st = Step(clause)
    p = analyze_pred(clause.toks, clause.sentence)
    st.trace["中心の述語"] = p.head
    if p.head is None:
        return st.fail("句", "分からない", "述語が無い")
    # 2 関門
    hits = [g["関門"] for g in tabs["gates"]["gates"] if need.type in g["効く型"] and _gate_hit(g, p, _gate_span(clause), clause)]
    st.trace["関門"] = hits
    if hits:
        return st.fail("関門", "合わない", "・".join(hits))
    # 3 句の種類と、必要の型の互換
    kind, period = clause_kind(p, clause.span, sent)
    st.trace["句の種類"] = kind
    if kind is None:
        return st.fail("種類", "分からない", "表 §2 の形に当たらない")
    if kind.startswith("応えない"):
        return st.fail("種類", "合わない", kind)
    compat = tabs["type_compat"]["match"].get(need.type, {})
    if kind not in compat:
        return st.fail("種類", "合わない", f"必要の型「{need.type}」に「{kind}」は応えない")
    rel = compat[kind]
    # 4 述語の種類と枠
    pk, verb, cands, how = predicate_kind(p, clause.span, prefer=need.pred_kind)
    st.trace["述語の種類"] = pk
    st.trace["述語の鍵"] = verb
    st.trace["述語の決め方"] = how
    if len(set(cands)) > 1:
        st.trace["述語の候補"] = cands
    row = next((r for r in tabs["predicates"]["互換"] if r["match"]["need"] == need.pred_kind), None)
    if row is None:
        return st.fail("述語", "分からない", f"必要の述語「{need.pred_kind}」の行が表 §3-3 に無い")
    m = row["match"]
    if "need_verb" in m and need.verb != m["need_verb"]:
        return st.fail("述語", "分からない", f"必要の述語「{need.verb}」が表 §3-3 の行（{row['必要の述語']}）と違う")
    if "need_object" in m and m["need_object"] == "場所" and not need.object_place:
        return st.fail("述語", "分からない", "必要の対象が場所でない")
    if pk is None:
        return st.fail("述語", "分からない", f"相手の述語「{verb}」の種類が決まらない（{how}）")
    if pk not in m["offer"]:
        return st.fail("述語", "合わない", f"「{need.pred_kind}」に「{pk}」は表 §3-3 の許可に無い")
    if "offer_verbs" in m and verb not in m["offer_verbs"]:
        return st.fail("述語", "合わない", f"表 §3-3 の許可は「{'・'.join(m['offer_verbs'])}」だけ（相手は「{verb}」）")
    if m.get("offer_object") == "場所" and not any(k == "場所" for k, _ in object_kinds(clause.span)):
        return st.fail("述語", "分からない", "相手の句に場所（§4 の例）が無い")
    if m.get("same") == "技術":
        offer_tech = [w for w in next(r for r in tabs["objects"]["rows"] if r["下位の種類"] == "音響・照明")["例"]
                      if clause.span.has(w)]
        common = [w for w in offer_tech if w in need.techniques]
        st.trace["同じ技術"] = common
        if not common:
            return st.fail("述語", "合わない", "同じ技術ではない")
    # 枠（比べ方が文書にあるのは「教える」だけ: 〈相手〉と〈内容〉が同じ概念）
    if m.get("holes"):
        vi = None
        for k, t in enumerate(p.toks):
            if t.pos1 == "動詞" and verb_key(t) in ("教える",):
                vi = k
            if t.pos1 == "名詞" and t.surface == "指導" and k + 1 < len(p.toks) and p.toks[k + 1].base == "する":
                vi = k
        fr = next(f for f in tabs["predicates"]["枠"] if f["動詞"] == "教える")
        holes = {}
        if vi is not None:
            for name, marker in fr["match"]["holes"]:
                holes[name] = np_before(p.toks, vi, marker, p.src)
        st.trace["枠の穴（相手の句）"] = holes
        if _v031():
            bad = _teach_frame_v031(need, holes, m["holes"], clause, sent, st)
            if bad is not None:
                return st.fail("枠", *bad)
        for name in ([] if _v031() else m["holes"]):
            if name not in need.holes:
                continue                                    # 必要に無い穴は比べない
            if not holes.get(name):
                if not _v02() and sent is not None:
                    # v0.3 §3-2: 相手の文全体に、穴の概念と同じ概念の語があれば合う
                    a, b = hole_concepts(need.holes[name]), offer_concepts(sent)
                    if set(a) & set(b):
                        st.trace.setdefault("枠（文全体で見た穴）", {})[name] = sorted(set(a) & set(b))
                        continue
                    return st.fail("枠", "分からない", f"〈{name}〉が相手の句に無く、文全体にも同じ概念の語が無い（必要「{need.holes[name]}」{a}）")
                return st.fail("枠", "分からない", f"〈{name}〉が相手の句に無い（必要は「{need.holes[name]}」）")
            a, b = hole_concepts(need.holes[name]), hole_concepts(holes[name])
            if not set(a) & set(b):
                return st.fail("枠", "合わない", f"〈{name}〉が同じ概念でない（必要「{need.holes[name]}」{a}・相手「{holes[name]}」{b}）")
    # 5 対象の領域（§5・§6）
    oc = offer_concepts(clause.span)
    st.trace["概念"] = {"必要": need.concepts, "相手": oc}
    common = [c for c in need.concepts if c in oc]
    if common:
        st.trace["領域"] = f"概念 {common[0]}"
    elif not _v02():
        # v0.3 §6: 概念表で合わなければ分からない（nomic は使わない）。理由は「概念不明」のログに残す
        det = concept_detail(clause.span)
        parts = []
        for c in need.concepts:
            d = det[c]
            if d["禁止語"]:
                parts.append(f"{c}: 禁止語（{'・'.join(d['禁止語'])}）")
            elif d["支える語"] and not d["領域の名詞"]:
                parts.append(f"{c}: 支える語だけ（{'・'.join(d['支える語'])}）")
            else:
                parts.append(f"{c}: 領域の名詞が相手の句に無い")
        st.trace["概念不明"] = {"必要の概念": need.concepts, "相手の句": {c: det[c] for c in need.concepts}}
        return st.fail("領域", "分からない", "概念不明（" + "／".join(parts or ["必要の句が概念に入らない"]) + "）")
    else:
        step1 = "合わない" if (need.concepts and oc) else "分からない"
        n_obj = need.object
        o_obj = interest_np(p) if pk == "関心" else object_np(p)
        nk = "主題" if pk == "関心" else center_noun_kind(n_obj)
        ok_ = "主題" if pk == "関心" else center_noun_kind(o_obj)
        q = {"必要の対象": n_obj, "相手の対象": o_obj, "必要の名詞の種類": nk, "相手の名詞の種類": ok_, "概念の判定": step1}
        st.trace["nomic"] = q
        if not (n_obj and o_obj and nk and ok_ and nk == ok_):
            return st.fail("領域", "分からない", "概念が同じでない・中心の名詞の種類が同じでない（nomic は見ない）")
        s = sim(n_obj, o_obj) if sim is not None else None
        q["近さ"] = s
        if s is None or threshold is None:
            return st.fail("領域", "分からない", "概念が同じでない（nomic の値が無い）")
        if not s > threshold:
            return st.fail("領域", "分からない", "概念が同じでない・nomic が線を越えない")
        if step1 == "合わない":
            return st.fail("領域", "分からない", "概念（合わない）と nomic（合う）が食い違う（自動では採らない）")
        st.trace["領域"] = "nomic"
    # 6 必要の文の条件（§7）
    cond = tabs["conditions"]["rows"][-1]["match"]
    if need.conditions:
        where = clause.span if (_v02() or sent is None) else sent          # v0.3: 相手の文全体で見る
        clash = [w for w in cond["conflict_words"] if where.has(w)]
        if clash:
            return st.fail("条件", "分からない", f"必要の条件（{'・'.join(need.conditions)}）と、相手の句の条件（{'・'.join(clash)}）が違う")
    # 7 関係と［C］
    k = next((x for x in tabs["clause_kinds"]["kinds"] if x["種類"] == kind), None)
    st.where, st.verdict = None, "合う"
    st.trace.update({"関係": rel, "C の種類": kind, "C": k["C"].replace("〔期間〕", period or "〔期間〕"),
                     "短い名前": k["短い名前"], "期間": period})
    return st


def match(need_text, offer_text, mode="split", sim=None, threshold=None, version=None, words=None):
    """必要の句（1 つ）と相手の文（1 つ）を照合する。
    version: 表の版（既定 v0.2）。words: 内容語の当て方（v0.2 の既定は表記のまま・v0.3 の既定は辞書形）。"""
    version = version or VERSION
    words = words or ("surface" if version == "v0.2" else "lemma")
    t1, t2 = _VER.set(version), _WORDS.set(words)
    try:
        return _match(need_text, offer_text, mode, sim, threshold)
    finally:
        _VER.reset(t1)
        _WORDS.reset(t2)


def _need_summary(need):
    return {"型": need.type, "述語の種類": need.pred_kind, "述語": need.verb, "対象": need.object,
            "枠の穴": need.holes, "概念": need.concepts, "条件": need.conditions, "B": need.B}


def _passed(out, st, text):
    out.update({"passed": True, "clause": text, "relation": st.trace["関係"], "kind": st.trace["C の種類"],
                "C": st.trace["C"], "short": st.trace["短い名前"], "period": st.trace["期間"],
                "domain": st.trace.get("領域"), "where": None, "why": None})
    return out


def _match(need_text, offer_text, mode, sim, threshold):
    need = analyze_need(need_text)
    out = {"need": _need_summary(need), "mode": mode, "passed": False, "clauses": []}
    if need.type.startswith("型なし") or need.type == "資格":
        out.update({"where": "種類", "why": f"必要の型が「{need.type}」"})
        return out
    if _v031():
        return _match_v031(need, offer_text, mode, out)
    if not _v02():
        return _match_v03(need, offer_text, mode, out)
    order = {s: i for i, s in enumerate(STEPS)}
    best = None
    for c in split_clauses(offer_text, mode):
        st = _check_clause(need, c, sim, threshold)
        out["clauses"].append(st.as_dict())
        if st.verdict == "合う":
            return _passed(out, st, c.text)
        if best is None or order[st.where] > order[best.where]:
            best = st
    if best is not None:
        out.update({"where": best.where, "why": best.why, "verdict": best.verdict})
    return out


def quote_text(clause):
    """v0.3.1-dev §8-0 の引用: 一致した述語を含む最小の節。終わりの読点と、節をつなぐ接続助詞（〜が・〜ので）は除く。"""
    core = core_toks(clause.toks)
    return clause.sentence[core[0].start:core[-1].end] if core else clause.text


def _match_v031(need, offer_text, mode, out):
    """表 v0.3.1-dev §8-0: 節ごとに試す。前の節をつなぐのは 2 つの場合だけ（① 前の節が条件・修飾 ② 主節に自分の「〜を」の
    対象が無く、前の節が独立した主張）。どちらも前の節が関門に当たればつながない。40 字まで。"""
    units = load_tables()["units"]["match"]
    order = {s: i for i, s in enumerate(STEPS)}
    toks = [t for t in tokenize(offer_text) if t.surface != "。"]
    sent = Clause(offer_text, toks, 0).span
    clauses = split_clauses(offer_text, mode)
    out["units"] = [c.text for c in clauses]
    for c in clauses:                                                 # 文全体に追加でかける関門（否定・推量・伝聞・主語）
        hits = gate_hits(c, need.type, units["sentence_gates"])
        if hits:
            out["clauses"].append({"句": c.text, "落ちた段": "関門", "判定": "合わない", "理由": "・".join(hits) + "（文全体）",
                                   "関門": hits})
            out.update({"where": "関門", "why": "・".join(hits) + "（文全体で見る）", "verdict": "合わない"})
            return out
    tried, passed = [], []
    for i, c in enumerate(clauses):
        unit, how = c, None
        if i > 0:
            prev = clauses[i - 1]
            cond = any(Span(core_toks(prev.toks)).ends(f) or prev.span.ends(f) for f in units["join_condition_ends"])
            need_obj = not own_object(analyze_pred(c.toks, c.sentence))
            if cond or need_obj:
                case = "① 条件・修飾" if cond else "② 対象を補う"
                joined = Clause(offer_text, prev.toks + c.toks, i)
                if gate_hits(prev):
                    note = f"前の節が関門に当たる（{'・'.join(gate_hits(prev))}）"
                elif len(joined.text) > units["join_max_chars"]:
                    note = f"つなぐと {len(joined.text)} 字（{units['join_max_chars']} 字まで）"
                else:
                    note = None
                    unit, how = joined, case
                if note is not None:
                    out.setdefault("つながなかった", []).append({"前の節": prev.text, "節": c.text, "場合": case, "理由": note})
        st = _check_clause(need, unit, None, None, sent=sent)
        st.trace["つないだ"] = how or False
        tried.append(st)
        if st.verdict == "合う":
            passed.append(st)
    out["clauses"] = [st.as_dict() for st in tried]
    if passed:
        rels = {(st.trace["関係"], st.trace["C の種類"]) for st in passed}
        if len(rels) > 1:
            out.update({"where": "種類", "why": f"有効な述語が 2 つ以上で、関係が決まらない（{sorted(rels)}）", "verdict": "分からない"})
            return out
        st = passed[0]
        out["joined"] = st.trace["つないだ"]
        return _passed(out, st, quote_text(st.clause))
    best = None
    for st in tried:
        if best is None or order[st.where] > order[best.where]:
            best = st
    if best is not None:
        out.update({"where": best.where, "why": best.why, "verdict": best.verdict, "unit": best.clause.text,
                    "joined": best.trace.get("つないだ", False)})
    return out


def own_object(p):
    """主節に自分の対象があるか（「〜を」の名詞句。関心は「〜に関心」の〜）。"""
    return interest_np(p) if p.head == "関心がある" else object_np(p)


def _match_v03(need, offer_text, mode, out):
    """表 v0.3 §8-0: 節ごとに試し、どれも通らなければ前の節をつないで試す。関門（否定・推量・伝聞・主語）と条件は文全体で見る。"""
    units = load_tables()["units"]["match"]
    order = {s: i for i, s in enumerate(STEPS)}
    toks = [t for t in tokenize(offer_text) if t.surface != "。"]
    sent = Clause(offer_text, toks, 0).span                          # 相手の文全体（使える立場の形・条件・枠の穴）
    clauses = split_clauses(offer_text, mode)
    out["units"] = [c.text for c in clauses]
    # 文全体の関門（節ごとの中心の述語・従属節も）
    for c in clauses:
        hits = gate_hits(c, need.type, units["sentence_gates"])
        if hits:
            out["clauses"].append({"句": c.text, "落ちた段": "関門", "判定": "合わない", "理由": "・".join(hits) + "（文全体）",
                                   "関門": hits})
            out.update({"where": "関門", "why": "・".join(hits) + "（文全体で見る）", "verdict": "合わない"})
            return out
    tried, passed = [], []
    for c in clauses:
        st = _check_clause(need, c, None, None, sent=sent)
        st.trace["つないだ"] = False
        tried.append(st)
        if st.verdict == "合う":
            passed.append(st)
    if not passed:
        for i in range(1, len(clauses)):
            prev, cur = clauses[i - 1], clauses[i]
            note = None
            if gate_hits(prev):
                note = f"前の節が関門に当たる（{'・'.join(gate_hits(prev))}）"
            elif own_object(analyze_pred(cur.toks, cur.sentence)):
                note = "主節に自分の対象がある（前の節は補わない）"
            joined = Clause(offer_text, prev.toks + cur.toks, i)
            if note is None and len(joined.text) > units["join_max_chars"]:
                note = f"つなぐと {len(joined.text)} 字（{units['join_max_chars']} 字まで）"
            if note is not None:
                out.setdefault("つながなかった", []).append({"前の節": prev.text, "節": cur.text, "理由": note})
                continue
            st = _check_clause(need, joined, None, None, sent=sent)
            st.trace["つないだ"] = True
            tried.append(st)
            if st.verdict == "合う":
                passed.append(st)
    out["clauses"] = [st.as_dict() for st in tried]
    if passed:
        rels = {(st.trace["関係"], st.trace["C の種類"]) for st in passed}
        if len(rels) > 1:
            out.update({"where": "種類", "why": f"有効な述語が 2 つ以上で、関係が決まらない（{sorted(rels)}）", "verdict": "分からない"})
            return out
        st = passed[0]
        out["joined"] = st.trace["つないだ"]
        return _passed(out, st, st.clause.text)
    best = None
    for st in tried:
        if best is None or order[st.where] > order[best.where]:
            best = st
    if best is not None:
        out.update({"where": best.where, "why": best.why, "verdict": best.verdict, "unit": best.clause.text,
                    "joined": best.trace.get("つないだ", False)})
    return out


def type1_sentence(B, offer_quote, short, N="{N}", O="{O}"):
    """型の ①（生成仕様 v1.0 §6-2）。"""
    return f"{N}が求める「{B}」について、{O}は「{offer_quote}」と書いています（{short}）。"
