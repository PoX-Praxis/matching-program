"""指示書81 追補1 §3・§4 — 画面のギャップを直す（モデルは走らせない。架空の人と、手元の偽の推論先だけ）。テスト 577〜581。

577 ① の中の部品・組の番号（P012・C031・O18・{O}18。括弧ごと）を機械で消し、消した数を記録に残す。消した後に安全の確かめを当てる
578 ① の文の終わりが「可能性があります。」でなければ、その目的の ① は出さない（数える）
579 ラベルの語（同種・same_kind など）・禁止の語があれば、その目的の ① は出さない
580 縛りのスイッチ（EXPLAIN_BOUND_FOR_NO_PURPOSE）：切れば B の候補は無い。入れれば目的の欄が無い人だけ、足りないもの × 相手の力・resolves だけ
581 申し出の画面：つながる意味がある目的を、最初に選ばれた状態にする
"""
import json
import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts"), os.path.join(ROOT, "tests")]

import explanation as X  # noqa: E402
from explain_pipeline import atoms as AT  # noqa: E402
from explain_pipeline import card as CD  # noqa: E402
from explain_pipeline import fields as FL  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
from explain_pipeline import runner as RN  # noqa: E402
from explain_pipeline import runtime as RT  # noqa: E402
from explain_pipeline import synthetic as SY  # noqa: E402

CAND = re.compile(r"^(C\d{3})（([ABC])）：", re.M)
PURPOSE = re.compile(r"^## 目的 (N\.purpose\.p\d+|N\.need|N\.bound)", re.M)
PAIR = re.compile(r"^  - (P\d{3})：", re.M)


class FakeEP:
    """偽の推論先（同じ入力には同じ答え）。A の候補は relational、B の候補は resolves。① の文は meaning で決める。"""

    def __init__(self, meaning="{O}の経験は、{N}の目的の力になれる可能性があります。"):
        self.meaning, self.prompts = meaning, []

    def complete(self, prompt, max_tokens):
        self.prompts.append(prompt)
        cands = CAND.findall(prompt)
        if cands:
            labels = [{"pair": c, "link": "relational" if cell == "A" else "resolves", "basis": "経験",
                       "key": {"side": "語", "power": "語"}, "fails_if": "合うかは分からない"} for c, cell in cands]
            text = json.dumps({"labels": labels}, ensure_ascii=False)
        else:
            out = []
            for block in re.split(r"\n\n(?=## 目的 )", prompt[prompt.find("## 目的 "):]):
                m = PURPOSE.search(block)
                if not m:
                    continue
                ps = PAIR.findall(block)
                out.append({"purpose_id": m.group(1), "chosen": ps[:1], "unknown": [], "why_not": None if ps else "組がありません。",
                            "meaning": [{"text": self.meaning, "cites": ps[:1]}] if ps else []})
            text = json.dumps({"purposes": out}, ensure_ascii=False)
        return {"text": text, "reasoning": "", "finish_reason": "stop", "usage": None, "timings": None,
                "t_send": 0.0, "t_first": 0.0, "t_first_content": 0.0, "t_end": 0.0}


def _spec(sid):
    return next(s for s in SY.pairs() if s["id"] == sid)


def _run(spec, ep, bound=False):
    return RN.run_pair(spec, ep, cells=("A",), bound_for_no_purpose=bound)


def _shown_meanings(card, names):
    kept, problems = X.check(card, names=names)
    return [p for p in kept["purposes"] + kept["reverse_purposes"] if p["status"] in X.SHOWN_STATUSES and p.get("meaning")], problems


# ── 577 番号を消す ─────────────────────────────────────────────────────────────────
def test_t577_strip_ids():
    c = {}
    assert CD.fix_form("{N}の経験（P012・C031）が{O}の力に（O18）なれる可能性があります", c) == "{N}の経験が{O}の力になれる可能性があります。"
    assert c["ids_removed"] == 3
    c = {}
    assert CD.fix_form("{O}18 と N01 の経験が、{N}の目的の力になれる可能性があります。", c) == "と の経験が、{N}の目的の力になれる可能性があります。"
    assert c["ids_removed"] == 2
    c = {}
    t = "2024年に作った経験（3年）が、{N}の目的の力になれる可能性があります。"         # ふつうの数・括弧は消さない
    assert CD.fix_form(t, c) == t and c["ids_removed"] == 0
    spec = _spec("synL")
    out, rec = _run(spec, FakeEP("{O}の経験（P001）は、{N}の目的の力になれる可能性があります。"))   # 消した後に確かめを通れば出す
    assert rec["counts"]["form_fixes"]["ids_removed"] > 0                                     # 消した数（数だけ）を記録に残す
    shown, _ = _shown_meanings(out["cards"]["A"], tuple(spec["names"]))
    assert shown and all(not re.search(r"[NOPC]\d{2,3}", p["meaning"]["text"]) for p in shown)
    out, rec = _run(spec, FakeEP("P001 は最適な相手です。"))                                  # 消しても確かめに落ちれば出さない
    shown, _ = _shown_meanings(out["cards"]["A"], tuple(spec["names"]))
    assert not shown                                       # 選ぶ段の軽い確かめ（禁止の語）で、その目的は「選べなかった」
    assert not any("P001" in p["meaning"]["text"] or "最適" in p["meaning"]["text"]
                   for q in out["cards"].values() for p in q["purposes"] if p.get("meaning"))


# ── 578 文の終わり ───────────────────────────────────────────────────────────────────
def test_t578_meaning_end():
    spec = _spec("synL")
    out, rec = _run(spec, FakeEP("{O}の経験は、{N}の目的の力になれる可能性があります。"))
    shown, _ = _shown_meanings(out["cards"]["A"], tuple(spec["names"]))
    assert shown and all(p["meaning"]["text"].endswith(X.PIPELINE_MEANING_END) for p in shown)
    assert not rec["counts"]["form_fixes"].get("meaning_end_ng")
    out, rec = _run(spec, FakeEP("{O}の経験で、{N}の目的を進められる。"))                      # 言い切り
    shown, problems = _shown_meanings(out["cards"]["A"], tuple(spec["names"]))
    assert not shown and any("可能性があります" in x for x in problems)
    assert rec["counts"]["form_fixes"]["meaning_end_ng"] > 0                                  # 出さなかった数を数える
    assert "「〜になれる可能性があります。」の形にする" in open(PR.SELECT_FILE, encoding="utf-8").read()


# ── 579 ラベルの語・禁止の語 ────────────────────────────────────────────────────────────
@pytest.mark.parametrize("text", ["{O}の経験は同種なので、{N}の目的の力になれる可能性があります。",
                                  "{O}の経験は Same_Kind で、{N}の目的の力になれる可能性があります。",
                                  "{O}の経験は relational に、{N}の目的の力になれる可能性があります。",
                                  "{O}は相性が良く、{N}の目的の力になれる可能性があります。"])
def test_t579_forbidden_words(text):
    spec = _spec("synL")
    out, _ = _run(spec, FakeEP(text))
    shown, _ = _shown_meanings(out["cards"]["A"], tuple(spec["names"]))
    assert not shown                                       # 選ぶ段の軽い確かめで「選べなかった」→ 出さない
    assert CD.bad_text(text, ()) == ["禁止の語・連絡先"]
    card = json.loads(json.dumps(_run(spec, FakeEP())[0]["cards"]["A"]))       # カードの確かめ（X.check）でも当たる
    p = next(p for p in card["purposes"] if p.get("meaning"))
    p["meaning"]["text"] = text
    _, problems = X.check(card, names=tuple(spec["names"]))
    assert any(p["ref"] in x and "禁止の語" in x for x in problems)
    assert X.FORBIDDEN_VERSION == "forbid-v0.5" and "同種" in X.FORBIDDEN_WORDS and "same_kind" in X.FORBIDDEN_WORDS
    assert "同種" in open(PR.SELECT_FILE, encoding="utf-8").read()


# ── 580 縛りのスイッチ ──────────────────────────────────────────────────────────────────
def test_t580_bound_switch(monkeypatch):
    spec = _spec("synS")                                     # 二人目（P6）は v4：目的の欄が無い。一人目（P5）は目的がある
    F, META = FL.pair_fields(spec["a"], spec["b"])
    atoms = AT.build_atoms(F, META)
    assert [p["field"] for p in AT.purposes_of(F, META, "B")] == [None] and AT.purposes_of(F, META, "A")[0]["field"]
    assert any(a["who"] == "B" and a["kind"] == "縛り" and a["bound"] == "lack" for a in atoms)
    assert any(a["who"] == "A" and a["kind"] == "縛り" for a in atoms)
    # 切る（既定）：B の候補は無い
    ep = FakeEP()
    out, rec = _run(spec, ep, bound=False)
    assert not any("（B）" in p for p in ep.prompts[:2]) and rec["counts"]["bound_for"] == []
    assert not any(p["ref"].endswith(".p2") for c in out["cards"].values() for p in c["purposes"] + c["reverse_purposes"])
    # 入れる：目的の欄が無い人（B）の向き O_by_N だけに、B の足りないもの × A の力
    ep = FakeEP()
    out, rec = _run(spec, ep, bound=True)
    assert rec["counts"]["bound_for"] == ["B"]
    _, cands, _ = PR.label_prompts(atoms, ("A",), ("B",))
    A = {a["id"]: a for a in atoms}
    bc = [c for c in cands.values() if c["cell"] == "B"]
    assert bc and all(c["dir"] == "O_by_N" and A[c["left"]]["who"] == "B" and A[c["left"]]["bound"] == "lack"
                      and A[c["right"]]["who"] == "A" and A[c["right"]]["kind"] == "力" for c in bc)
    assert "（B）" not in ep.prompts[0] and "（B）" in ep.prompts[1]
    assert "N.bound" in ep.prompts[3] and "N.bound" not in ep.prompts[2]
    card = out["cards"]["A"]                                 # P5 が見るカード：相手（P6）の縛られているものから見た目的
    bp = next(p for p in card["reverse_purposes"] if p["ref"] == "target.purpose.p2")
    assert bp["title"] == "架空P6 さんの縛られているものから見た、架空P5 さん" and bp["kind"] == "補完"
    assert bp["elements"] and all(e["need_ref"] == "target.state.縛られているもの" for e in bp["elements"])
    assert any(c["ref"] == "target.state.縛られているもの" for c in card["citations"])
    kept, problems = X.check(card, names=tuple(spec["names"]))
    assert any(p["ref"] == "target.purpose.p2" and p.get("meaning") for p in kept["reverse_purposes"]), problems
    # resolves 以外（eases など）は使わない
    labels = {"x": {"cand": bc[0], "final": "eases", "lab": {}}}
    sp, pairs, meta = PR.select_prompts(atoms, labels, F, META, ("B",))
    assert not pairs and "N.bound" in sp["B_need"]
    # 設定：既定は切る。環境変数で入れる。版の鍵が変わる
    for n in (*RT.PLACEMENT_ENV.values(), RT.CELLS_ENV, RT.BOUND_ENV):
        monkeypatch.delenv(n, raising=False)
    cfg = RT.load()
    assert RT.bound_for_no_purpose(cfg) is False
    monkeypatch.setenv(RT.BOUND_ENV, "1")
    on = RT.load()
    assert RT.bound_for_no_purpose(on) is True and RT.version_key(on) != RT.version_key(cfg)
    assert RT.version_record(on, "local_http")["bound_for_no_purpose"] is True


# ── 581 申し出の画面の目的 ────────────────────────────────────────────────────────────
def test_t581_offer_preselect(monkeypatch):
    import app as appmod
    spec = _spec("synL")
    out, _ = _run(spec, FakeEP())
    card = out["cards"]["A"]
    inp = {"purposes": [{"purpose_id": "pid-x"}]}
    monkeypatch.setattr(appmod, "_explain_input", lambda s: inp)
    shown = [p for p in X.check(card, names=tuple(spec["names"]))[0]["purposes"] if p["status"] in X.SHOWN_STATUSES and p.get("meaning")]
    assert shown and appmod._meaning_purpose_ids("u1", card) == ["pid-x"]
    bad = json.loads(json.dumps(card))
    for p in bad["purposes"]:
        if p.get("meaning"):
            p["meaning"]["text"] = "{O}は最適です。"                       # 確かめに落ちた目的は選ばない
    assert appmod._meaning_purpose_ids("u1", bad) == []
    other = json.loads(json.dumps(card))
    other["generation"]["prompt1_sha"] = "other"
    assert appmod._meaning_purpose_ids("u1", other) == []                   # 流れのカードでなければ何もしない
    html = open(os.path.join(ROOT, "templates", "profile.html"), encoding="utf-8").read()
    assert "explainMeaningPurposes = Array.isArray(d.meaning_purposes)" in html and "if (pre) sel.value = pre;" in html
