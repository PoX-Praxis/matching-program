"""指示書77b §3 — 機械の［A］（規則の版 a_rules_v1）。

照合の表が出した offer_quote から、「〜人です」につながる［A］を作る。計測の道具なので scripts/ に置く
（本番のコードは照合の表を読まない。テスト 526）。変えるのは末尾の丁寧の形だけ（§3 の 2 に
並んだものだけ）。規則に当たらないとき・迷うときは［A］を作らず、型の ① にする。規則の中身は
data/machine_a/a_rules_v1.json（指示書の写し）にある。ここに規則に無い変換を足さない。
"""
import functools
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))
import match_table as MT  # noqa: E402
RULES_PATH = os.path.join(ROOT, "data", "machine_a", "a_rules_v1.json")
VERSION = "a_rules_v1"

_ICHIDAN = ("上一段", "下一段")
_END_FORMS = ("終止形-一般", "連体形-一般")
_CONTENT_POS = ("名詞", "動詞", "形容詞", "副詞", "形状詞", "代名詞", "連体詞", "接頭辞", "接尾辞")


@functools.lru_cache(maxsize=1)
def load_rules():
    with open(RULES_PATH, encoding="utf-8") as f:
        return json.load(f)


def rules_digest():
    return hashlib.sha256(open(RULES_PATH, "rb").read()).hexdigest()


def _is_te(t):
    return t.pos1 == "助詞" and t.pos2 == "接続助詞" and t.lemma == "て"


def _renyo_verb(t):
    return t.pos1 == "動詞" and t.cform == "連用形-一般"


def _plain(text, core):
    """規則 2。返り値 (［A］, 変換の名前, None) か、(None, None, 型にした理由)。"""
    n = len(core)
    last = core[-1]
    if n >= 3 and core[-2].base == "ます" and core[-2].pos1 == "助動詞" and last.base == "た" and last.surface == "た":
        v = core[-3]
        if not _renyo_verb(v):
            return None, None, "「ました」の前が動詞の連用形でない"
        head = text[:v.end]
        te = n >= 4 and _is_te(core[-4])
        if v.ctype == "カ行変格":
            if te and v.surface == "き":
                return head + "た", "〜てきました→〜てきた", None
            return None, None, "カ変の「〜ました」（「〜てきました」でない）は規則の並びに無い"
        if v.ctype.startswith(_ICHIDAN):
            if te and v.base == "いる":
                return head + "た", "〜ていました→〜ていた", None
            return head + "た", "一段動詞の〜ました→〜た", None
        if v.ctype == "サ行変格":
            if v.surface == "し":
                return head + "た", "〜しました（サ変）→〜した", None
            return None, None, "サ変で表記が「し」でない（迷う）"
        if v.ctype.startswith("五段"):
            return None, None, "五段動詞の〜ました（音便が要る）"
        return None, None, "規則の並びに無い「〜ました」"
    if n >= 2 and last.base == "ます" and last.pos1 == "助動詞" and last.cform == "終止形-一般":
        v = core[-2]
        if not _renyo_verb(v):
            return None, None, "「ます」の前が動詞の連用形でない"
        te = n >= 3 and _is_te(core[-3])
        if v.ctype.startswith(_ICHIDAN):
            if te and v.base == "いる":
                name = "〜ています→〜ている"
            elif v.base == "できる":
                name = "〜できます→〜できる"
            else:
                name = "〜ます（一段・可能動詞）→辞書形"
            return text[:v.end] + "る", name, None
        if v.ctype == "サ行変格":
            if v.surface == "し":
                return text[:v.start] + "する", "〜ます（サ変）→辞書形", None
            return None, None, "サ変で表記が「し」でない（迷う）"
        if v.ctype.startswith("五段") and v.base == "ある" and v.surface == "あり":
            return text[:v.start] + "ある", "〜あります→〜ある", None
        return None, None, "規則の並びに無い「〜ます」（五段動詞など）"
    if any(t.base == "ます" and t.pos1 == "助動詞" for t in core[-3:]):
        return None, None, "規則の並びに無い丁寧の形"
    return text, "なし（常体のまま）", None


def _ends_ok(toks):
    """規則 3。末尾が 動詞（できる・ある・いる を含む）か、助動詞「た」の終止形・連体形。"""
    if not toks:
        return False, "空"
    t = toks[-1]
    if t.pos1 == "動詞" and t.cform in _END_FORMS:
        return True, None
    if t.pos1 == "助動詞" and t.ctype == "助動詞-タ" and t.cform in _END_FORMS:   # 音便の後の「だ」（読んだ）も「た」
        return True, None
    if t.pos1 == "助動詞" and t.ctype in ("助動詞-ダ", "助動詞-デス"):
        return False, f"末尾が「{t.base}」"
    return False, f"末尾が{t.pos1}"


def remaining_forms(text, toks=None):
    """規則 4。［A］の全体に残っている否定・推量・希望の形（区分の名前の並び）。"""
    rem = load_rules()["match"]["remain"]
    toks = toks if toks is not None else [t for t in MT.tokenize(text) if t.surface.strip()]
    plain = text.replace("、", "")
    out = []
    for kind, spec in rem.items():
        aux = set(spec.get("助動詞", []))
        adj = set(spec.get("形容詞", []))
        hit = any((t.pos1 == "助動詞" and t.base in aux) or (t.pos1 == "形容詞" and t.lemma in adj) for t in toks)
        hit = hit or any(w in plain for w in spec.get("含む", []))
        if hit:
            out.append(kind)
    return out


def _content(toks):
    return [(t.pos1, t.base) for t in toks if t.pos1 in _CONTENT_POS]


def make_a(offer_quote):
    """機械の［A］。返り値: {"A", "出し方"（生成／型）, "変換", "型の理由"（§3 の番号の並び）, "詳しさ", "残っていた形"}。"""
    out = {"A": None, "出し方": "型", "変換": None, "型の理由": [], "詳しさ": None, "残っていた形": [],
           "rules": VERSION}
    if not offer_quote:
        out["型の理由"], out["詳しさ"] = [1], "offer_quote が無い"
        return out
    core = MT.core_toks(MT.tokenize(offer_quote))                       # 規則 1（表の W9 と同じ）
    if not core:
        out["型の理由"], out["詳しさ"] = [1], "語が無い"
        return out
    text = offer_quote[core[0].start:core[-1].end]
    a, name, why = _plain(text, core)                                    # 規則 2
    rem = remaining_forms(text if a is None else a)                      # 規則 4（あれば必ず記録する）
    out["残っていた形"] = rem
    if a is None:
        out["型の理由"], out["詳しさ"] = [2], why
        if rem:
            out["型の理由"].append(4)
        return out
    out["変換"] = name
    a_toks = [t for t in MT.tokenize(a) if t.surface.strip()]
    ok, why = _ends_ok(a_toks)                                           # 規則 3
    reasons, detail = [], []
    if not ok:
        reasons.append(3)
        detail.append(why)
    if rem:                                                              # 規則 4
        reasons.append(4)
        detail.append("残っていた形: " + "・".join(rem))
    if len(a) > load_rules()["match"]["max_chars"]:                     # 規則 5
        reasons.append(5)
        detail.append(f"{len(a)} 字")
    if _content(a_toks) != _content(core):                               # 規則 6（確かめ）
        reasons.append(6)
        detail.append("内容語の並びが offer_quote と違う")
    if reasons:
        out["型の理由"], out["詳しさ"] = reasons, "／".join(detail)
        return out
    out.update({"A": a, "出し方": "生成"})
    return out


def sentence(result, a, N="{N}", O="{O}"):
    """① の文。生成の ①（［A］があるとき）か、型の ①。result は match_table.match の結果（通ったもの）。"""
    B = result["need"]["B"]
    if a["出し方"] == "生成":
        return f"{O}は、{a['A']}人です。{N}が求める「{B}」には、{result['C']}。"
    return MT.type1_sentence(B, result["clause"], result["short"], N=N, O=O)


def explain(need, offer, version="v0.3.1-dev", N="{N}", O="{O}"):
    """照合の表 ＋ 機械の［A］。返り値: (表の結果, ［A］の結果 または None, ① の文 または None)。"""
    r = MT.match(need, offer, version=version)
    if not r["passed"]:
        return r, None, None
    a = make_a(r["clause"])
    return r, a, sentence(r, a, N=N, O=O)
