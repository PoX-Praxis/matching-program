#!/usr/bin/env python3
"""指示書69 §5 — 接続の説明の計測の仕組み（段階3 の準備。閾値を決めない・数値を報告に載せない）。

正解つきのデータで、パイプライン全体（段1 → 確かめ → 段2 → 確かめ → 表示するか）を走らせ、次を数える。
  - 基準テスト（指示書72 §2・報告72 §3）: 物差し 2 つ（架空の 60 組・KAORU の 48 組）で、組ごとに、その組の必要 1 つと
    相手の文 1 つだけで説明を作り、誤り・拾えた・関係の一致と、1 回ごとの結果の種類（a〜d）を数える
  - 人が確かめた負例で、説明を出してしまう数（KAORU が「応えない」とした上層の 4 人）
  - hard negative 60（el-v2）・C 群 36（el-v2）での同じ数（補助）
  - 確かめで落ちた数（段ごと・理由ごと）
  - 決定性（同じ入力を 2 回）
  - 段2 に原文が漏れていないこと（段2 の入力が、確かめ済みの句・ラベル・見出しだけか）
  - お手本（KAORU×PA の 2 枚）の再現（組・ラベル・句・① の一致）
出力: local/69/explanation_measure_<backend>.json・.md（件数と、組の id・結果の種類・理由・関係だけ。文は書かない）。
候補の線（§2）は通さない（データの対は、すでに組になっている）。上層の結果は使わない。外部の AI の API は使わない。
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src")) if p not in sys.path]
import explanation as X  # noqa: E402
import explanation_gen as G  # noqa: E402

DATA = os.path.join(ROOT, "scripts", "probe_data")
BENCH = os.path.join(ROOT, "data", "explanation_bench", "synthetic_v1.json")   # 架空の 60 組（指示書72）


def open_count(direction):
    """向きの中で、状態が open（根拠はあるが、欠かせない要素に欠けがある）の目的の数（指示書73 §3。出した数とは別に書く）。"""
    return sum(1 for p in direction["purposes"] if p["status"] == "open")


def bench_person(person, only_need=None):
    """架空の人（基準テストの人）から N の側を作る。only_need を渡すと、その必要 1 つだけ（指示書72 の形）。"""
    needs = [{"text": n["text"], "type": "", "must": bool(n["必須"])} for n in person["needs"]
             if only_need is None or n["ref"] == only_need]
    return {"subject": "n", "purposes": [{"purpose_id": "p1", "title": person["purpose"], "means": "",
                                          "kind": person["種類"], "needs": needs}]}


def bench_counts(backend, form="one", bench=None):
    """架空の 60 組で、出した数と open の数（指示書73 §3）。
    form="one": 1 つの必要 × 1 つの相手の文（指示書72 の形。open は起きないはず）／"whole": その人の必要像すべて × 1 つの相手の文。"""
    if bench is None:
        with open(BENCH, encoding="utf-8") as f:
            bench = json.load(f)
    people = {p["id"]: p for p in bench["people"]}
    out = {"n": 0, "shown": 0, "open": 0}
    for pr in bench["pairs"]:
        n = bench_person(people[pr["person"]], pr["need_ref"] if form == "one" else None)
        res = G.generate_direction(n, one_sentence_person(pr["other"]), backend)
        out["n"] += 1
        out["shown"] += int(X.showable(X.check(X.assemble(res["direction"], None, True))[0]))
        out["open"] += open_count(res["direction"])
    return out


# ── 基準テスト（指示書72 §1・§2。報告72 §3 と、その確認 2026-11-16）─────────────────────────────────
LABELS = os.path.join(ROOT, "local", "65a", "labels_v1.json")   # KAORU の 48 組（手元だけ。リポジトリに入れない）
RELS = ("R1", "R2", "R3", "R4")
CLASSES = ("説明できる", "説明できない", "判断できない", "対象外")
NOT_GENERATED = ("判断できない", "対象外")                         # 生成しない。件数だけ書く
KINDS = {"a": "JSON として読めなかった", "b": "JSON は読めたが組が 0", "c": "組は出たが確かめで落ちた", "d": "出た"}
STATE_LABELS = {"持っているもの": "持っているもの", "動き方の型": "できること_型"}   # 「縛られているもの」は O の側では使わない
R4_RULE = "補完の目的で R4 だけ"                                   # 仕様 §12-4（規則は変えない。確認 2 の案(ア)）


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def label_class(expl_label):
    """expl_label（配列）を 4 つに分ける。R1〜R4 を含めば「説明できる」（["R4","R1"] は、どれでも正しい）。"""
    lab = [str(x) for x in expl_label or []]
    if any(x in RELS for x in lab):
        return "説明できる"
    for c in CLASSES[1:]:
        if c in lab:
            return c
    return "読めない"


def synthetic_items(bench):
    """架空の組 → (組, N, O)。N は 1 つの目的に、その組の必要 1 つだけ（見出し・種類はその人の。欠かせないかは組の must）。
    相手の文は、文の種類を持っていないので、すべて与え像の 1 文として置く（報告72 §3-1）。"""
    people = {p["id"]: p for p in bench["people"]}
    for pr in bench["pairs"]:
        person = people[pr["person"]]
        n = {"subject": "n", "purposes": [{"purpose_id": "p1", "title": person["purpose"], "means": "",
                                           "kind": person["種類"],
                                           "needs": [{"text": pr["need"], "type": "", "must": bool(pr["must"])}]}]}
        yield pr, n, one_sentence_person(pr["other"])


def state_from_full_text(text):
    """「PA の現状（全文）」を現状の欄に分ける（「持っているもの: …」「動き方の型: …」。「縛られているもの」は使わない）。"""
    state = {}
    for seg in str(text).split(" / "):
        label, sep, body = seg.partition(": ")
        slot = STATE_LABELS.get(label.strip())
        if sep and slot and body.strip():
            state[slot] = body.strip()
    return state


def other_person(pair):
    """KAORU の 48 組の O（相手の文 1 つ。other_kind で置き場所を分ける。報告72 §3-1）。"""
    kind = str(pair.get("other_kind") or "")
    if kind.startswith("PA の現状"):
        return {"subject": "o", "offers": [], "state": state_from_full_text(pair["other"]), "interests": []}
    if kind.startswith("関心の文"):
        return {"subject": "o", "offers": [], "state": {}, "interests": [pair["other"]]}
    return one_sentence_person(pair["other"])


def kaoru_need_person(pair, kaoru):
    """KAORU の 48 組の N（報告72 §3-1）。作れなければ None。
    - need_key が 1-x・2-x: KAORU の公開の JSON の、その目的の向かう先・欠かせないか・型。目的の種類は目的全体から仮の規則で
    - need_key が PA（v4）: 見出しは空・補完・欠かせない要素（v4 の必要は 1 つだけなので）"""
    key = str(pair.get("need_key") or "")
    if key.startswith("PA"):
        return {"subject": "n", "purposes": [{"purpose_id": "p1", "title": "", "means": "", "kind": "補完",
                                              "needs": [{"text": pair["need"], "type": "", "must": True}]}]}
    m = re.fullmatch(r"(\d+)-(\d+)", key)
    purposes = (kaoru or {}).get("purposes") or []
    if not m or not 1 <= int(m.group(1)) <= len(purposes):
        return None
    p = purposes[int(m.group(1)) - 1]
    needs = [{"text": str(x.get("文") or ""), "type": str(x.get("型") or ""), "must": bool(x.get("必須"))}
             for x in p.get("必要像") or []]
    i = int(m.group(2)) - 1
    one = needs[i] if 0 <= i < len(needs) and needs[i]["text"] == pair["need"] else \
        next((x for x in needs if x["text"] == pair["need"]), None)
    if one is None:
        return None
    return {"subject": "n", "purposes": [{"purpose_id": str(p.get("purpose_id") or f"p{m.group(1)}"),
                                          "title": str(p.get("向かう先") or ""), "means": "",
                                          "kind": G.purpose_kind_rule(needs), "needs": [one]}]}


def kaoru_items(labels, kaoru):
    for pr in labels.get("pairs") or []:
        n = None if label_class(pr.get("expl_label")) in NOT_GENERATED else kaoru_need_person(pr, kaoru)
        yield pr, n, other_person(pr)


_KEEP = ("連絡先・URL", "引用・目的・句の長さ")


def drop_reasons(problems):
    """確かめで落ちた理由（括弧の中の理由を 1 つずつ。参照・番号・文は残さない）。"""
    out = []
    for pr in problems:
        m = re.search(r"（(.+)）\s*$", pr)
        body = m.group(1) if m else pr.split(": ", 1)[-1]
        for k, keep in enumerate(_KEEP):
            body = body.replace(keep, f"\0{k}\0")
        for r in body.split("・"):
            for k, keep in enumerate(_KEEP):
                r = r.replace(f"\0{k}\0", keep)
            out.append(re.sub(r"組 \d+ ", "組 ", r.strip()))
    return out


def run_pair(n, o, backend):
    """組 1 つを生成して、結果の種類（a〜d）・理由・出した関係・段2 まで進んだか・open の数を返す（文は返さない）。"""
    trace = []
    res = G.generate_direction(n, o, backend, trace=trace)
    d = res["direction"]
    shown = X.showable(X.check(X.assemble(d, None, True))[0])
    rels = sorted({e["relation"] for p in d["purposes"] if p["status"] in X.SHOWN_STATUSES for e in p["elements"]})
    reasons = []
    if shown:
        kind = "d"
    elif res["show_reason"] == "model_declined":
        kind = "a"
    elif res["show_reason"] == "no_pair":
        kind = "b"
    else:
        kind = "c"
        reasons = drop_reasons(res["problems"])
        if any(p["kind"] == "補完" and p["elements"] and all(e["relation"] == "R4" for e in p["elements"])
               and p["status"] not in X.SHOWN_STATUSES for p in d["purposes"]):
            reasons.append(R4_RULE)                       # 機械の状態で not_covered にした（理由の文が problems に出ない）
    stage2 = bool(trace and trace[0]["stage2_payload"]["purposes"])
    leak = leak_violations(d, trace[0]["stage2_payload"]) if trace else 0      # 段2 に原文が漏れた数（指示書76 §2）
    return {"result": kind, "reasons": sorted(set(reasons)), "relations": rels, "stage2": stage2,
            "open": open_count(d), "leak": leak}


def bench_measure(items, backend):
    """物差し 1 つ分を数える（指示書72 §2・報告72 §3-2 と確認 2・4）。
    - 誤り: 「説明できない」の組で出した数
    - 拾えた: 「説明できる」の組で出した数 ÷ 「説明できる」の数。ただし補完の目的で expl_label が R4 だけの組は、規則（仕様 §12-4）で
      出さないので、「規則で出さない」として別に数え、分母から外す（確認 2 の案(ア)）
    - 関係の一致: 出した「説明できる」の組で、出した組の関係が、すべて expl_label に含まれる数（厳しい側。確認 4）
    - 関係ごとの拾えた: expl_label にその関係がある組で、その関係の組を出した数
    - 「判断できない」・「対象外」は生成しない（件数だけ）。1 回ごとの結果の種類 a〜d を組ごとに記録する（文は記録しない）"""
    c = {"pairs": 0, "classes": {k: 0 for k in CLASSES}, "rule_excluded": {"n": 0, "shown": 0},
         "errors": {"shown": 0, "n": 0}, "recall": {"shown": 0, "n": 0}, "relation_match": {"same": 0, "shown": 0},
         "by_relation": {r: {"shown": 0, "n": 0} for r in RELS}, "kinds": {k: 0 for k in KINDS}, "reasons": Counter(),
         "open": 0, "leak": 0, "not_built": 0, "unreadable_label": 0}
    records = []
    for pr, n, o in items:
        c["pairs"] += 1
        cls = label_class(pr.get("expl_label"))
        if cls == "読めない":
            c["unreadable_label"] += 1
            records.append({"id": pr["id"], "class": cls, "result": None})
            continue
        c["classes"][cls] += 1
        if cls in NOT_GENERATED:
            records.append({"id": pr["id"], "class": cls, "result": None})
            continue
        if n is None:
            c["not_built"] += 1
            records.append({"id": pr["id"], "class": cls, "result": "入力が作れない"})
            continue
        r = run_pair(n, o, backend)
        shown = r["result"] == "d"
        c["kinds"][r["result"]] += 1
        c["reasons"].update(r["reasons"])
        c["open"] += r["open"]
        c["leak"] += r["leak"]
        labels = {x for x in pr.get("expl_label") or [] if x in RELS}
        rule = cls == "説明できる" and n["purposes"][0]["kind"] == "補完" and labels == {"R4"}
        if cls == "説明できない":
            c["errors"]["n"] += 1
            c["errors"]["shown"] += int(shown)
        elif rule:
            c["rule_excluded"]["n"] += 1
            c["rule_excluded"]["shown"] += int(shown)
        else:
            c["recall"]["n"] += 1
            c["recall"]["shown"] += int(shown)
            for rel in labels:
                c["by_relation"][rel]["n"] += 1
                c["by_relation"][rel]["shown"] += int(shown and rel in r["relations"])
        if cls == "説明できる" and shown:
            c["relation_match"]["shown"] += 1
            c["relation_match"]["same"] += int(set(r["relations"]) <= labels)
        records.append({"id": pr["id"], "class": cls, "rule_excluded": rule, **r})
    c["reasons"] = dict(c["reasons"])
    return {"counts": c, "records": records}


def bench_total(parts):
    """物差しごとの数を足す（合計の行）。"""
    tot = None
    for p in parts:
        if tot is None:
            tot = json.loads(json.dumps(p))
            continue
        for k, v in p.items():
            if isinstance(v, int):
                tot[k] += v
            elif isinstance(v, dict):
                for kk, vv in v.items():
                    if isinstance(vv, dict):
                        for k3, v3 in vv.items():
                            tot[k].setdefault(kk, {}).setdefault(k3, 0)
                            tot[k][kk][k3] += v3
                    else:
                        tot[k][kk] = tot[k].get(kk, 0) + vv
    return tot


def n_person_from_kaoru(doc):
    """KAORU の v5 の公開の範囲の JSON（local/）から、N の側の公開の文を作る（目的の種類は §4-4 の仮の規則）。"""
    purposes = []
    for i, p in enumerate(doc.get("purposes") or [], 1):
        needs = [{"text": str(x.get("文") or ""), "type": str(x.get("型") or ""), "must": bool(x.get("必須"))}
                 for x in p.get("必要像") or [] if str(x.get("文") or "").strip()]
        if needs:
            purposes.append({"purpose_id": p.get("purpose_id") or f"p{i}", "title": str(p.get("向かう先") or ""),
                             "means": str(p.get("手段") or ""), "needs": needs})
    return {"subject": "n", "purposes": purposes}


def one_sentence_person(text):
    return {"subject": "o", "offers": [{"text": text, "type": ""}], "state": {}, "interests": []}


def _strings(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _strings(v)
    elif isinstance(x, list):
        for v in x:
            yield from _strings(v)
    elif isinstance(x, str):
        yield x


def leak_violations(direction, stage2_payload):
    """段2 の入力の文字列が、確かめ済みの句・ラベル・見出し・種類・目的の参照だけか。外れた数を返す。"""
    allowed = {"補完", "共鳴"}
    for p in direction["purposes"]:
        allowed |= {p["title"], p["ref"]}
    for p in stage2_payload.get("purposes") or []:
        allowed |= {p.get("purpose_ref"), p.get("見出し")}
        for e in p.get("elements") or []:
            allowed |= {e["relation"], X.RELATIONS.get(e["relation"], ("",))[0]}
    kept = {(e["need_quote"], e["offer_quote"]) for p in direction["purposes"] for e in p["elements"]}
    quotes = {q for pair in kept for q in pair}
    bad = 0
    for p in stage2_payload.get("purposes") or []:
        for e in p.get("elements") or []:
            if (e["need_quote"], e["offer_quote"]) not in kept and not (e["need_quote"] in quotes and e["offer_quote"] in quotes):
                bad += 1
    bad += sum(1 for s in _strings({k: v for k, v in stage2_payload.items()}) if s not in allowed and s not in quotes)
    return bad


def run_one(n, o, backend, drops, stage_drops):
    trace = []
    res = G.generate_direction(n, o, backend, trace=trace)
    for pr in res["problems"]:
        m = re.search(r"（(.+)）", pr)
        for why in (m.group(1).split("・") if m else [pr]):
            drops[why] += 1
        stage_drops["段1" if "組" in pr else "段2" if "①" in pr else "全体"] += 1
    # 「出した」＝ どちらかの目的が explained か open になった（指示書73 §3。showable がそう決める）
    shown = X.showable(X.check(X.assemble(res["direction"], None, True))[0])
    leak = leak_violations(res["direction"], trace[0]["stage2_payload"]) if trace else 0
    return res, shown, leak


def exemplar_persons(direction):
    """お手本の向き（N.*・O.* の参照）から、N と O の公開の文を組み直す（引用の欄の原文から）。"""
    src = {c["ref"]: c["text"] for c in direction["citations"]}
    purposes = []
    for p in direction["purposes"]:
        pid = p["ref"].split(".")[2]
        needs = sorted([(r, t) for r, t in src.items() if r.startswith(f"N.need.{pid}.")])
        musts = {e["need_ref"] for e in p["elements"] if e["must"]} | set(p["missing_must"])
        purposes.append({"purpose_id": pid, "title": p["title"], "means": "", "kind": p["kind"],
                         "needs": [{"text": t, "type": "", "must": r in musts} for r, t in needs]})
    offers = [{"text": t, "type": ""} for r, t in sorted(src.items()) if r.startswith("O.offer.")]
    state = {r.split(".", 2)[2]: t for r, t in src.items() if r.startswith("O.state.")}
    interests = [t for r, t in sorted(src.items()) if r.startswith("O.interest.")]
    return {"subject": "n", "purposes": purposes}, {"subject": "o", "offers": offers, "state": state, "interests": interests}


def compare_exemplar(want, got):
    """お手本との比べ方（案）: 組（必要の参照・相手の参照）の一致・ラベルの一致・句の一致・① の一致・状態の一致を数える。
    参照は、組み直した入力で番号が変わりうるので、句の組で比べる。"""
    def pairs(d):
        return {(e["need_quote"], e["offer_quote"]): e["relation"] for p in d["purposes"] for e in p["elements"]}
    w, g = pairs(want), pairs(got)
    wm = {p["title"]: (p["meaning"] or {}).get("text") for p in want["purposes"]}
    gm = {p["title"]: (p["meaning"] or {}).get("text") for p in got["purposes"]}
    ws = {p["title"]: p["status"] for p in want["purposes"]}
    gs = {p["title"]: p["status"] for p in got["purposes"]}
    return {"pairs_want": len(w), "pairs_got": len(g), "pairs_same": len(set(w) & set(g)),
            "labels_same": sum(1 for k in set(w) & set(g) if w[k] == g[k]),
            "meaning_same": sum(1 for t in wm if wm[t] and gm.get(t) == wm[t]), "meanings_want": sum(1 for t in wm if wm[t]),
            "status_same": sum(1 for t in ws if gs.get(t) == ws[t]), "purposes": len(ws)}


BENCH_NAMES = {"synthetic_v1": "架空の 60 組", "labels_v1": "KAORU の 48 組"}


def _frac(d, a="shown", b="n"):
    return f"{d[a]}/{d[b]}"


def bench_markdown(bench, total):
    """基準テストの表（件数だけ。組の文は書かない）。"""
    L = ["## 基準テスト（指示書72。組ごとに、その組の必要 1 つ × 相手の文 1 つ）", ""]
    if not bench:
        return L + ["- 物差しが無い", ""]
    L.append("物差しの版: " + "・".join(f"{BENCH_NAMES[k]} `{v['sha256']}`" for k, v in bench.items())
             + ("" if "labels_v1" in bench else "（KAORU の 48 組は手元に無いので、架空の組だけ）"))
    L += ["", "| 物差し | 組 | 説明できる | 説明できない | 判断できない | 対象外 | 誤り | 拾えた | 規則で出さない（補完で R4 だけ） | "
          "関係の一致 | open の目的 | 入力が作れない |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    rows = [(BENCH_NAMES[k], v["counts"]) for k, v in bench.items()] + ([("合計", total)] if len(bench) > 1 else [])
    for name, c in rows:
        cl = c["classes"]
        L.append(f"| {name} | {c['pairs']} | {cl['説明できる']} | {cl['説明できない']} | {cl['判断できない']} | {cl['対象外']} | "
                 f"{_frac(c['errors'])} | {_frac(c['recall'])} | {_frac(c['rule_excluded'])} | "
                 f"{_frac(c['relation_match'], 'same', 'shown')} | {c['open']} | {c['not_built']} |")
    L += ["", "関係ごとの拾えた（expl_label にその関係がある組で、その関係の組を出した数）:", ""]
    for name, c in rows:
        L.append(f"- {name}: " + "・".join(f"{r} {_frac(c['by_relation'][r])}" for r in RELS))
    L += ["", "1 回ごとの結果の種類:", ""]
    for name, c in rows:
        L.append(f"- {name}: " + "・".join(f"({k}) {KINDS[k]} {c['kinds'][k]}" for k in KINDS))
    L += ["", "(c) の理由（組の数。1 つの組に理由が複数あれば、それぞれ数える）:", ""]
    for name, c in rows:
        L.append(f"- {name}: " + ("・".join(f"{k} {v}" for k, v in sorted(c["reasons"].items(), key=lambda x: (-x[1], x[0])))
                                  or "なし"))
    return L + [""]


def to_markdown(res):
    L = [f"# 接続の説明の計測（{res['backend']}）", "",
         "- 指示書69 §5 の計測の仕組み。**件数だけ。文は書かない。閾値は決めていない（段階3 の前に KAORU が決める）**",
         f"- モデル: {res['model']}（revision {res['revision']}）・走らせた時間 {res['seconds']} 秒", ""]
    L += bench_markdown(res.get("bench") or {}, res.get("bench_total"))
    for k, label in (("upper_neg", "上層の「応えない」4 人で出した"), ("hn_shown", "hard negative で出した"),
                     ("c_shown", "C 群で出した")):
        v = res.get(k)
        L.append(f"- {label}: " + ("データが無い" if v is None else f"{v['shown']}/{v['n']}（うち open の目的 {v.get('open', 0)}）"))
    for k, label in (("bench_one_need", "架空の 60 組（1 つの必要 × 1 つの相手の文）で出した"),
                     ("bench_whole", "架空の 60 組（その人の必要像すべて × 1 つの相手の文。指示書73 の形）で出した")):
        v = res.get(k)
        if v:
            L.append(f"- {label}: {v['shown']}/{v['n']}（うち open の目的 {v['open']}）")
    L += ["", "確かめで落ちた数（理由ごと）:", ""]
    L += [f"- {k}: {v}" for k, v in sorted(res["drops"].items(), key=lambda x: -x[1])] or ["- なし"]
    L += ["", "段ごと: " + "・".join(f"{k} {v}" for k, v in res["stage_drops"].items()),
          f"決定性: {res['determinism']['same']}/{res['determinism']['runs']} が同じ出力",
          f"段2 に原文が漏れた数: {res['leak']['violations']}（{res['leak']['runs']} 回）", ""]
    ex = res.get("exemplar")
    if ex:
        L += ["お手本の再現:", ""] + [f"- {k}: " + "・".join(f"{a} {b}" for a, b in v.items()) for k, v in ex.items()]
    else:
        L.append("お手本: データが無い")
    return "\n".join(L) + "\n"


def main(argv=None, backend=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--backend", default="fixed", choices=("fixed", "local"))
    ap.add_argument("--kaoru", default=os.path.join(ROOT, "local", "kaoru_v5_public.json"))
    ap.add_argument("--labels", default=os.path.join(ROOT, "local", "65a", "labels_v1.json"))
    ap.add_argument("--upper-neg", default=os.path.join(ROOT, "local", "65a", "upper_negatives.json"))
    ap.add_argument("--exemplar", default=os.path.join(ROOT, "local", "68", "explanation_cards.json"))
    ap.add_argument("--out", default=os.path.join(ROOT, "local", "69"))
    ap.add_argument("--limit", type=int, default=0, help="集合ごとに先頭の N 件だけ（local の動作確かめ用）")
    ap.add_argument("--model", default=None, help="local のモデル（必須。段階3 で決める）")
    ap.add_argument("--revision", default=None, help="local のモデルの revision（必須）")
    args = ap.parse_args(argv)
    t0 = time.time()

    def read(path):
        if path and os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        return None

    backend = backend or (G.FixedBackend() if args.backend == "fixed" else G.LocalBackend(args.model, args.revision))
    lim = (lambda xs: xs[:args.limit]) if args.limit else (lambda xs: xs)
    n = n_person_from_kaoru(read(args.kaoru) or {})
    with open(os.path.join(DATA, "element_sets_v2.json"), encoding="utf-8") as f:
        el = json.load(f)
    drops, stage_drops = Counter(), Counter({"段1": 0, "段2": 0, "全体": 0})
    leak = {"runs": 0, "violations": 0}
    det = {"runs": 0, "same": 0}
    res = {"backend": backend.name, "model": backend.model, "revision": backend.revision}

    def count(texts):
        shown = opened = 0
        for t in texts:
            r, s, lk = run_one(n, one_sentence_person(t), backend, drops, stage_drops)
            shown += int(s)
            opened += open_count(r["direction"])
            leak["runs"] += 1
            leak["violations"] += lk
        return {"shown": shown, "n": len(texts), "open": opened}

    res["hn_shown"] = count(lim([x["text"] for x in el["HN"]["a"]] + list(el["HN"]["b"]))) if n["purposes"] else None
    res["c_shown"] = count(lim([x["text"] for x in el["C"]])) if n["purposes"] else None
    # 基準テスト（指示書72）: 架空の 60 組はいつも。KAORU の 48 組は手元にあるときだけ（無ければ架空の組だけで動く）
    res["bench"] = {}
    if os.path.exists(BENCH):
        with open(BENCH, encoding="utf-8") as f:
            syn = json.load(f)
        res["bench"]["synthetic_v1"] = {"sha256": sha256_file(BENCH), **bench_measure(lim(list(synthetic_items(syn))), backend)}
    labels = read(args.labels)
    if labels:
        kaoru_doc = read(args.kaoru)
        res["bench"]["labels_v1"] = {"sha256": sha256_file(args.labels),
                                     **bench_measure(lim(list(kaoru_items(labels, kaoru_doc))), backend)}
    parts = [v["counts"] for v in res["bench"].values()]
    res["bench_total"] = bench_total(parts) if parts else None
    up = read(args.upper_neg)
    if up and n["purposes"]:
        shown = opened = 0
        for person in lim(up.get("persons") or []):
            r, s, _ = run_one(n, {"subject": "o", **person}, backend, drops, stage_drops)
            shown += int(s)
            opened += open_count(r["direction"])
        res["upper_neg"] = {"shown": shown, "n": len(lim(up.get("persons") or [])), "open": opened}
    else:
        res["upper_neg"] = None
    for t in lim([x["text"] for x in el["C"]])[:3] if n["purposes"] else []:     # 決定性（同じ入力を 2 回）
        a = G.generate_direction(n, one_sentence_person(t), backend)["output_digest"]
        b = G.generate_direction(n, one_sentence_person(t), backend)["output_digest"]
        det["runs"] += 1
        det["same"] += int(a == b)
    if os.path.exists(BENCH):                     # 架空の 60 組の、人のまるごとの形（指示書73 §3）。open の数を分けて書く
        res["bench_whole"] = bench_counts(backend, "whole")
    ex = read(args.exemplar)
    if ex and ex.get("directions"):
        res["exemplar"] = {}
        for i, d in enumerate(ex["directions"]):
            en, eo = exemplar_persons(d)
            r = G.generate_direction(en, eo, backend)
            res["exemplar"][f"向き{i + 1}"] = {**compare_exemplar(d, r["direction"]), "open": open_count(r["direction"])}
    else:
        res["exemplar"] = None
    res.update(drops=dict(drops), stage_drops=dict(stage_drops), leak=leak, determinism=det,
               seconds=round(time.time() - t0, 1))
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, f"explanation_measure_{backend.name}.json"), "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    with open(os.path.join(args.out, f"explanation_measure_{backend.name}.md"), "w", encoding="utf-8") as f:
        f.write(to_markdown(res))
    return res


if __name__ == "__main__":
    main()
