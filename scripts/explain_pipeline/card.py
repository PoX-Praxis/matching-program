"""カードを組み立てる（指示書68 の形・expl-v0.7）。KAORU×PA の試し 5 回目（状態）・6 回目（選ぶ・まとめる）と同じ組み立て。
- 目的の状態は、ラベル（確かめの後）の A の組で決める（5 回目）
- 要素と ① の文は、選ぶ段の出力から（6 回目）。軽い確かめ：chosen が入力の組にあるか・cites が chosen の中か・
  禁止の語・「満たせる」・表示名。外れたら、その目的は「選べなかった」
- 形だけの機械の直し：括弧のない N・O を {N}・{O} に、文末に「。」が無ければ足す"""
import copy
import json
import os
import re
import sys

from . import atoms as AT
from . import prompts as PR

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))
import explanation as X  # noqa: E402

MODEL, REVISION = "gpt-oss-120b", "b5c939de8f754692c1647ca79fbf85e8c1e70f8a"
DECODING = {"temperature": 0, "top_p": 1, "seed": 0, "reasoning_effort": "medium"}
REL5 = {"経験": "R1", "申告": "R2", "能力": "R2", "資源": "R3"}                 # 5 回目（状態を決める）
REL6 = {"経験": "R1", "申告": "R2", "能力": "R2", "資源": "R3", "関心": "R4"}   # 6 回目（選ばれた組の要素）
SHOWN_LINKS = ("same_kind", "relational", "same_interest")
STATE_NAME = {"state.have": "持っているもの", "state.can_type": "できること_型", "state.bound": "縛られているもの", "state.unsorted": "未分類"}
BARE = re.compile(r"(?<![A-Za-z{])([NO])(?![A-Za-z}])")
# ① に紛れた部品・組・候補の番号（N01・O18・P012・C031・{O}18。括弧ごと）を消す（指示書81 追補1 §3-1）
_ID = r"(?:\{[NO]\}|[NOPC])[0-9０-９]{2,3}"
ID_BRACKETS = re.compile(r"[（(［\[〔【]\s*" + _ID + r"(?:\s*[・、,，/／]\s*" + _ID + r")*\s*[）)］\]〕】]")
ID_BARE = re.compile(r"(?<![A-Za-z0-9])" + _ID + r"(?:\s*[・、,，/／]\s*" + _ID + r")*(?![0-9０-９])")


def card_ref(fk, viewer_who):
    """人の欄（A.necessity.p1.1-1 など）→ カードの参照（viewer.need.p1.1-1 など）。目的の無い人の必要像は need.p1.<番号>。"""
    side = "viewer" if fk[0] == viewer_who else "target"
    f = fk.split(".", 1)[1].split("#")[0]
    if f.startswith("necessity."):
        rest = f.split(".", 1)[1]
        return f"{side}.need.{rest if rest.startswith('p') else 'p1.' + rest}"
    if f.startswith("purpose."):
        return f"{side}.purpose.{f.split('.', 1)[1]}"
    if f.startswith("offer.") or f.startswith("interest."):
        return f"{side}.{f}"
    if f in STATE_NAME:
        return f"{side}.state.{STATE_NAME[f]}"
    return f"{side}.state.{ {'experience': '経験', 'why': 'なぜ', 'background': '背景'}.get(f, f) }"


def citations(F, viewer_who, bound_for=()):
    """bound_for の人の縛られているものも入れる（縛りの目的があるときだけ。指示書81 追補1 §3-3）。"""
    out = []
    for fk, t in F.items():
        f = fk.split(".", 1)[1]
        if (f.startswith(("necessity", "purpose", "offer", "interest")) or f in ("state.have", "state.can_type", "experience")
                or (f == "state.bound" and fk[0] in bound_for)):
            out.append({"ref": card_ref(fk, viewer_who), "text": t})
    return out


def base_purposes(need_who, viewer_who, pid, labels, atoms, F, META, bound_for=()):
    """need_who の目的 × 相手の文（ラベルの向き pid）。目的の状態を、確かめの後のラベルの A の組で決める（5 回目）。
    縛りの目的（bound_for。指示書81 追補1 §3-3）は B の組で、resolves だけを使う。"""
    A = {a["id"]: a for a in atoms}
    out = []
    for order, p in enumerate(PR.purposes(atoms, F, META, need_who, bound_for)):
        side_ids = p["side_ids"]
        shown = PR.BOUND_USE if p.get("bound") else SHOWN_LINKS
        mine = [v for v in labels.values() if v["cand"]["dir"] == pid and v["cand"]["left"] in side_ids]
        elements = []
        for v in sorted(mine, key=lambda v: v["cand"]["pair"]):
            if v["cand"]["cell"] != p["cell"]:
                continue
            L, R = A[v["cand"]["left"]], A[v["cand"]["right"]]
            rel = "R4" if v["final"] == "same_interest" else REL5.get(R.get("basis"))
            if v["final"] in shown and rel:
                elements.append({"need_ref": card_ref(L["field"], viewer_who), "must": bool(META.get(L["field"].split("#")[0], {}).get("必須")),
                                 "need_quote": L["text"], "offer_ref": card_ref(R["field"], viewer_who), "offer_quote": R["text"], "relation": rel})
        covered = {e["need_ref"] for e in elements}
        musts = [card_ref(f, viewer_who) for f in p["needs"] if META.get(f, {}).get("必須")]
        if p["kind"] == "共鳴":
            status, missing = ("explained" if elements else "not_covered"), []
        else:
            missing = [m for m in musts if m not in covered]
            status = "explained" if (elements and not missing) else "not_covered"
        unknown = []
        for aid in side_ids:
            a = A[aid]
            if not a["field"].startswith(f"{need_who}.necessity."):
                continue
            if not any(v["cand"]["left"] == aid and v["final"] in shown for v in mine):
                unknown.append({"need_ref": card_ref(a["field"], viewer_who), "need_quote": a["text"]})
        side = "viewer" if need_who == viewer_who else "target"
        out.append({"ref": f"{side}.purpose.p{p['n']}", "order": order, "number": order + 1, "title": p["title"], "kind": p["kind"],
                    "status": status, "meaning": None, "elements": elements, "unknown": unknown, "missing_must": missing})
    return out


def parse_or_none(raw):
    """選ぶ段の出力から JSON を取り出す（最初の「{」から最後の「}」まで）。読めなければ None（6 回目と同じ）。"""
    s = str(raw or "")
    i, j = s.find("{"), s.rfind("}")
    try:
        return json.loads(s[i:j + 1])
    except ValueError:
        return None


def bad_text(t, names):
    t = str(t or "")
    out = []
    if X._forbidden(t):
        out.append("禁止の語・連絡先")
    if "満たせる" in t:
        out.append("「満たせる」")
    if any(n in t for n in tuple(names) + ("あなた",)):
        out.append("表示名・「あなた」")
    return out


def select(raw, meta, atoms, names):
    """選ぶ段のプロンプト 1 つの出力 → 目的ごとの {chosen, meaning, unknown, why_not, ok, why}。"""
    A = {a["id"]: a for a in atoms}
    inv = {v: k for k, v in meta["num"].items()}
    v = parse_or_none(raw)
    got = {}
    for it in (v or {}).get("purposes") or []:
        if isinstance(it, dict) and str(it.get("purpose_id")) not in got:
            got[str(it.get("purpose_id"))] = it
    res = {}
    for p in meta["purposes"]:
        it = got.get(p["purpose_id"])
        why = []
        if v is None:
            why.append("JSON として読めない")
        elif it is None:
            why.append("この目的の答えが無い")
        chosen = [str(x) for x in (it or {}).get("chosen") or []]
        if any(c not in p["pairs"] for c in chosen):
            why.append("chosen に入力の組に無い番号")
        meaning = [m for m in (it or {}).get("meaning") or [] if isinstance(m, dict)]
        for m in meaning:
            if any(str(c) not in chosen for c in m.get("cites") or []) or not m.get("cites"):
                why.append("cites が chosen の外・空")
            why += bad_text(m.get("text"), names)
        why += bad_text((it or {}).get("why_not"), names)
        unk, unk_bad = [], 0
        for u in (it or {}).get("unknown") or []:
            aid = inv.get(str(u).strip())
            if aid and aid in p["side_ids"] and A[aid]["field"].split(".")[1] == "necessity":
                unk.append(aid)
            else:
                unk_bad += 1
        res[p["purpose_id"]] = {"chosen": chosen, "meaning": meaning, "unknown": unk, "unknown_bad": unk_bad,
                                "why_not": (it or {}).get("why_not"), "ok": not why, "why": sorted(set(why)), "n_pairs": len(p["pairs"])}
    return res


def strip_ids(t, counter=None):
    """① の中の部品・組・候補の番号を、括弧ごと機械で消す。消した数を counter["ids_removed"] に足す（数だけ）。"""
    t = str(t or "")
    n = 0
    for rx in (ID_BRACKETS, ID_BARE):
        n += sum(len(re.findall(_ID, m)) for m in rx.findall(t))
        t = rx.sub("", t)
    if n:
        t = re.sub(r"[ 　]{2,}", " ", t)
        t = re.sub(r"[ 　]+(?=[、。，．）)])", "", t)
        t = re.sub(r"(?<=[（(])[ 　]+", "", t)
        t = re.sub(r"[、，]{2,}", "、", t)
        t = re.sub(r"(^|(?<=。))[ 　、，]+", "", t)
        t = re.sub(r"[、，]+(?=[。])", "", t)
        t = re.sub(r"[（(]\s*[）)]", "", t)
    if counter is not None:
        counter["ids_removed"] = counter.get("ids_removed", 0) + n
    return t


def fix_form(t, counter=None):
    """形だけの機械の直し：番号を消す（strip_ids）・括弧のない N・O を {N}・{O} に、文末に「。」が無ければ足す。"""
    t = strip_ids(t, counter).strip()
    n = len(BARE.findall(t))
    if counter is not None:
        counter["bare_no"] = counter.get("bare_no", 0) + n
    t = BARE.sub(lambda m: "{" + m.group(1) + "}", t)
    if t and not t.endswith("。"):
        if counter is not None:
            counter["add_period"] = counter.get("add_period", 0) + 1
        t += "。"
    return t


def element(pair, atoms_by_id, META, viewer_who):
    L, R = atoms_by_id[pair["left"]], atoms_by_id[pair["right"]]
    rel = "R4" if pair["label"] == "same_interest" else REL6.get(pair["basis"], "R2")
    return {"need_ref": card_ref(L["field"], viewer_who), "must": bool(META.get(L["field"].split("#")[0], {}).get("必須")),
            "need_quote": L["text"], "offer_ref": card_ref(R["field"], viewer_who), "offer_quote": R["text"], "relation": rel}


def purposes_from(sel, meta, pairs, base, atoms, META, viewer_who, need_title, counter=None, bound_title=""):
    """目的の状態は base のまま。要素・① の文・分からないことを、選ぶ段の結果から入れる（6 回目）。"""
    A = {a["id"]: a for a in atoms}
    out = []
    order = [p["purpose_id"] for p in meta["purposes"]]
    for i, bp in enumerate(base):
        q = copy.deepcopy(bp)
        s = sel[order[i]]
        if order[i] == "N.need":
            q["title"] = need_title
        if order[i] == "N.bound":
            q["title"] = bound_title
        if s["ok"] and s["chosen"]:
            q["elements"] = [element(pairs[c], A, META, viewer_who) for c in s["chosen"]]
            idx = {c: k for k, c in enumerate(s["chosen"])}
            cites = []
            for m in s["meaning"]:
                for c in m.get("cites") or []:
                    if idx[str(c)] not in cites:
                        cites.append(idx[str(c)])
            q["meaning"] = {"text": "".join(fix_form(m.get("text"), counter) for m in s["meaning"]), "cites": cites or [0]}
            ends = [x for x in re.split(r"(?<=。)", q["meaning"]["text"]) if x.strip()]
            if counter is not None and any(not x.strip().endswith(X.PIPELINE_MEANING_END) for x in ends):
                counter["meaning_end_ng"] = counter.get("meaning_end_ng", 0) + 1     # 確かめ（X.check）で出さない
        else:
            q["elements"] = []
            msg = "この目的については、組を選べませんでした。" if not s["ok"] else (str(s["why_not"] or "").strip() or "この目的については、選べる組がありませんでした。")
            q["meaning"] = {"text": msg, "cites": []} if q["status"] in X.SHOWN_STATUSES else None
        if q["status"] not in X.SHOWN_STATUSES:
            q["meaning"] = None
        q["unknown"] = [{"need_ref": card_ref(A[a]["field"], viewer_who), "need_quote": A[a]["text"]} for a in s["unknown"]]
        out.append(q)
    return out


def make_card(viewer_who, people, F, META, atoms, labels, sel, meta, pairs, label_sha, select_sha, counter=None, generation=None,
              bound_for=()):
    """people: {"A": {"name", "tag", "has_purposes"}, "B": …}。sel: {"A_need": select の結果, "B_need": …}。
    generation: カードの generation の model・revision・decoding（無ければ 5・6 回目の値）。
    bound_for: 縛りの目的を足す人（目的の欄が無い人。スイッチを入れたときだけ。指示書81 追補1 §3-3）。"""
    target_who = "B" if viewer_who == "A" else "A"
    fwd_lab, rev_lab = ("N_by_O", "O_by_N") if viewer_who == "A" else ("O_by_N", "N_by_O")
    fwd_sel, rev_sel = f"{viewer_who}_need", f"{target_who}_need"
    base_f = base_purposes(viewer_who, viewer_who, fwd_lab, labels, atoms, F, META, bound_for)
    base_r = base_purposes(target_who, viewer_who, rev_lab, labels, atoms, F, META, bound_for)
    has_bound = tuple(w for w in bound_for if any(p.get("bound") for p in PR.purposes(atoms, F, META, w, bound_for)))
    tag = {w: people[w]["tag"] for w in ("A", "B")}
    nec = {w: (tag[w] if people[w]["has_purposes"] else None) for w in ("A", "B")}
    card = {"schema_version": X.SCHEMA_VERSION, "relation_rule_version": X.RELATION_RULE_VERSION,
            "status": "explained" if any(p["status"] in X.SHOWN_STATUSES for p in base_f + base_r) else "not_covered",
            "show_reason": None,
            "as_of": {"viewer": {"profile": tag[viewer_who], "necessity": nec[viewer_who]},
                      "target": {"profile": tag[target_who], "necessity": nec[target_who]}},
            "generation": {"prompt1_sha": label_sha, "prompt2_sha": None, "model": (generation or {}).get("model", MODEL),
                           "revision": (generation or {}).get("revision", REVISION),
                           "decoding": dict((generation or {}).get("decoding") or DECODING)},
            "target_has_purposes": people[target_who]["has_purposes"], "purposes": base_f, "reverse_purposes": base_r,
            "citations": citations(F, viewer_who, has_bound)}
    bt = {w: f"{people[w]['name']} さんの縛られているものから見た、{people['B' if w == 'A' else 'A']['name']} さん" for w in ("A", "B")}
    card["purposes"] = purposes_from(sel[fwd_sel], meta[fwd_sel], pairs, base_f, atoms, META, viewer_who,
                                     f"{people[viewer_who]['name']} さんが求めている相手", counter, bt[viewer_who])
    card["reverse_purposes"] = purposes_from(sel[rev_sel], meta[rev_sel], pairs, base_r, atoms, META, viewer_who,
                                             f"{people[target_who]['name']} さんが求めている相手", counter, bt[target_who])
    card["generation"] = {**card["generation"], "prompt2_sha": select_sha}
    card["output_digest"] = X.output_digest(card)
    return card
