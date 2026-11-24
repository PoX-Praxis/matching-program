"""説明の生成の流れ（指示書80）— 人の構造化の JSON から、名前の付いた欄を作る。
v5（目的あり）と v4（目的なし）の 2 つの形。欄の名前と並びは、KAORU×PA の試し（4〜6 回目）と同じ。
渡すもの：目的（向かう先・手段・種類）・必要像・与え像・関心・現状（持っているもの・できること_型・未分類・縛られているもの）・意志（なぜ）・背景・経験。
渡さないもの：生テキスト・根拠・evidence_span・数値・要約文・一行紹介。"""
import os
import re
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))
import explanation_gen as G  # noqa: E402

_BAD = re.compile("[\\u0000-\\u0008\\u000b-\\u001f\\u007f\u200b-\u200f\u2028-\u202e\u2060-\u2064\ufeff]")   # 制御文字・ゼロ幅・向きの制御（元の試しと同じ文字の範囲）
_URL = re.compile(r"https?://\S+|www\.\S+")
STATE = (("持っているもの", "state.have"), ("できること_型", "state.can_type"), ("未分類", "state.unsorted"), ("縛られているもの", "state.bound"))


def clean(t):
    """制御文字・ゼロ幅の文字・URL を除く（文の中身は変えない）。"""
    t = str(t or "")
    return _URL.sub("", _BAD.sub("", t)).strip()


def v5_fields(d, P):
    """v5 の人（目的あり）。P は欄の名前の頭（"N" など）。"""
    out = []
    for i, p in enumerate(d.get("purposes") or [], 1):
        needs = [{"text": str(x.get("文") or ""), "type": str(x.get("型") or ""), "must": bool(x.get("必須"))} for x in p.get("必要像") or []]
        kind = G.purpose_kind_rule(needs)
        out.append({"欄": f"{P}.purpose.p{i}", "目的の種類": kind, "文": clean(p.get("向かう先"))})
        out.append({"欄": f"{P}.means.p{i}", "文": clean(p.get("手段"))})
        for k, x in enumerate(p.get("必要像") or [], 1):
            out.append({"欄": f"{P}.necessity.p{i}.{i}-{k}", "必須": bool(x.get("必須")), "型": clean(x.get("型")), "文": clean(x.get("文"))})
    out += [{"欄": f"{P}.offer.{k}", "型": clean(x.get("型")), "文": clean(x.get("文"))} for k, x in enumerate(d.get("与え像") or [], 1)]
    out += [{"欄": f"{P}.interest.{k}", "文": clean(x.get("文"))} for k, x in enumerate(d.get("関心") or [], 1)]
    st = d.get("現状") or {}
    out += [{"欄": f"{P}.{name}", "文": clean(st.get(j))} for j, name in STATE]
    sm = d.get("supporting_material") or {}
    out += [{"欄": f"{P}.why", "文": clean(sm.get("意志_なぜ"))}, {"欄": f"{P}.background", "文": clean(sm.get("背景"))},
            {"欄": f"{P}.experience", "文": clean(sm.get("経験"))}]
    return [f for f in out if f["文"]]


def v4_fields(d, P):
    """v4 の人（目的なし）。必要像と現状だけ。"""
    out = [{"欄": f"{P}.necessity.{k}", "必須": bool(x.get("必須")), "文": clean(x.get("文"))} for k, x in enumerate(d.get("必要像") or [], 1)]
    st = d.get("現状") or {}
    out += [{"欄": f"{P}.{name}", "文": clean(st.get(j))} for j, name in STATE]
    return [f for f in out if f["文"]]


def has_purposes(d):
    return bool(d.get("purposes"))


def person_fields(d, P):
    return v5_fields(d, P) if has_purposes(d) else v4_fields(d, P)


def pair_fields(a, b):
    """二人（a を一人目・b を二人目）の欄を、人の印（"A"・"B"）を頭に付けて 1 つの並びにする。
    返り値: (F, META)。F は 欄の名前 → 文（一人目の欄が先）。META は 欄の名前 → 文以外（型・必須・目的の種類）。"""
    F, META = {}, {}
    for who, d in (("A", a), ("B", b)):
        for f in person_fields(d, "N"):
            name = who + f["欄"][1:]
            F[name] = f["文"]
            META[name] = {k: v for k, v in f.items() if k not in ("欄", "文")}
    return F, META
