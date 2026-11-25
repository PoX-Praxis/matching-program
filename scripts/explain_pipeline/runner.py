"""1 組の流れ（部品 → ラベル → 確かめ → 選ぶ・まとめる → カード）と、同時に送る形・何組かをまとめる形・時間の記録（指示書80 §1）。

- 向きを同時に（direction_parallel）：ラベルの段の N_by_O と O_by_N を同時に送り、両方が返ってから選ぶ段へ進む
- 組どうしを同時に（pair_concurrency）：別の組の流れを、この数まで同時に動かす（1 なら 1 組ずつ）
- 選ぶ・まとめるの段は、組の中ではいまのまま（ラベルの後に A_need、その後に B_need）
- 本番の既定は DEFAULTS（向きも組も 1 つずつ）。指示書80 §2（報告 80）：vLLM v0.33.0 では、二つの向きを同時に送ると
  出力が変わった（4 組すべて）。だから同時に送る形（direction_parallel・pair_concurrency>1）は使わない。比べるためだけに残す
- 記録（records）には数・時刻・番号だけを入れる。文（プロンプト・出力・部品の文）は outputs にだけ入れる"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import atoms as AT
from . import card as CD
from . import checks as CK
from . import fields as FL
from . import prompts as PR

MAX_TOKENS = 30000
DEFAULTS = {"direction_parallel": False, "pair_concurrency": 1}
MODES = {"a": {"direction_parallel": False, "pair_concurrency": 1},     # 順に（いまのやり方）
         "b": {"direction_parallel": True, "pair_concurrency": 1},      # 組の中で、二つの向きを同時に
         "c": {"direction_parallel": True, "pair_concurrency": 0}}      # 組どうしも同時に（0 は組の数）
_MACHINE = threading.Lock()     # 機械の段は 1 本ずつ（形態素解析の Tagger を共有するため）
LABEL_IDS = tuple(d[0] for d in PR.LABEL_DIRS)
SELECT_IDS = tuple(d[0] for d in PR.SELECT_DIRS)


TIMING_KEYS = ("prompt_n", "prompt_ms", "predicted_n", "predicted_ms", "predicted_per_second")   # llama.cpp の応答の時間（数だけ）


def _req_record(stage, pid, r, origin, err=None):
    tm = (r or {}).get("timings") or {}
    u = (r or {}).get("usage") or {}
    det = u.get("completion_tokens_details") or {}
    rel = (lambda t: None if t is None else round(t - origin, 3))
    return {"stage": stage, "id": pid, "t_send": rel((r or {}).get("t_send")), "t_first": rel((r or {}).get("t_first")),
            "t_first_content": rel((r or {}).get("t_first_content")), "t_end": rel((r or {}).get("t_end")),
            "prompt_tokens": u.get("prompt_tokens"), "completion_tokens": u.get("completion_tokens"),
            "reasoning_tokens": det.get("reasoning_tokens"), "finish_reason": (r or {}).get("finish_reason"), "error": err,
            "timings": {k: tm[k] for k in TIMING_KEYS if k in tm} or None}


def _call(endpoint, stage, pid, prompt, max_tokens, origin, clock):
    t = clock()
    try:
        r = endpoint.complete(prompt, max_tokens)
        return r, _req_record(stage, pid, r, origin)
    except Exception as e:                  # 失敗は記録して先へ（出力は空として扱う）
        return {"text": None, "reasoning": None}, {**_req_record(stage, pid, {"t_send": t, "t_end": clock()}, origin), "error": type(e).__name__}


def run_pair(spec, endpoint, direction_parallel=False, max_tokens=MAX_TOKENS, origin=0.0, clock=time.time, cells=("A", "B", "C"),
             label_sha=None, select_sha=None, bound_for_no_purpose=False):
    """1 組。spec: {"id", "a", "b", "people": {"A": {"name", "tag"}, "B": …}, "names": [表示名…]}。
    bound_for_no_purpose: 目的の欄が無い人の縛りの目的を足す（指示書81 追補1 §3-3。既定は切る）。
    返り値: (outputs, record)。outputs には文、record には数と時刻だけ。"""
    rec = {"pair": spec["id"], "t_start": round(clock() - origin, 3), "machine": {}, "requests": [], "counts": {}}

    def machine(name, fn):
        with _MACHINE:
            t = clock()
            v = fn()
            rec["machine"][name] = round(clock() - t, 4)
        return v

    def prep():
        F, META = FL.pair_fields(spec["a"], spec["b"])
        atoms = AT.build_atoms(F, META)
        bf = tuple(w for w in ("A", "B") if bound_for_no_purpose and not any(p["field"] for p in AT.purposes_of(F, META, w)))
        lp, cands, info = PR.label_prompts(atoms, cells, bf)
        return F, META, atoms, lp, cands, info, bf

    F, META, atoms, lp, cands, info, bound_for = machine("prep", prep)
    rec["counts"].update({"atoms": len(atoms), "cands": {pid: info[pid]["cells"] for pid in LABEL_IDS}})
    out = {"label": {}, "select": {}, "cards": {}}
    # ラベルの段
    if direction_parallel:
        with ThreadPoolExecutor(max_workers=len(LABEL_IDS)) as ex:
            futs = {pid: ex.submit(_call, endpoint, "label", pid, lp[pid], max_tokens, origin, clock) for pid in LABEL_IDS}
            got = {pid: futs[pid].result() for pid in LABEL_IDS}
    else:
        got = {pid: _call(endpoint, "label", pid, lp[pid], max_tokens, origin, clock) for pid in LABEL_IDS}
    for pid in LABEL_IDS:
        r, rr = got[pid]
        out["label"][pid] = {"text": r.get("text"), "reasoning": r.get("reasoning")}
        rec["requests"].append(rr)
    A = {a["id"]: a for a in atoms}

    def check():
        labels = CK.check_labels({pid: out["label"][pid]["text"] for pid in LABEL_IDS}, cands, A)
        ssha = select_sha or PR.SELECT_SHA256                              # 版の選ぶ段（explain-v06 は v0.1・p81 は v0.2）
        sp, pairs, meta = PR.select_prompts(atoms, labels, F, META, bound_for, file=PR.select_file(ssha), sha=ssha)
        return labels, sp, pairs, meta

    labels, sp, pairs, meta = machine("check", check)
    rec["counts"]["labels"] = _count(v["final"] for v in labels.values())
    rec["counts"]["select_pairs"] = {pid: sum(1 for p in pairs.values() if p["prompt"] == pid) for pid in SELECT_IDS}
    # 選ぶ・まとめるの段（組の中では順に）
    for pid in SELECT_IDS:
        r, rr = _call(endpoint, "select", pid, sp[pid], max_tokens, origin, clock)
        out["select"][pid] = {"text": r.get("text"), "reasoning": r.get("reasoning")}
        rec["requests"].append(rr)

    def cards():
        sel = {pid: CD.select(out["select"][pid]["text"], meta[pid], atoms, spec.get("names") or ()) for pid in SELECT_IDS}
        people = {w: {**spec["people"][w], "has_purposes": FL.has_purposes(spec["a" if w == "A" else "b"])} for w in ("A", "B")}
        fix = {}
        cs = {w: CD.make_card(w, people, F, META, atoms, labels, sel, meta, pairs, label_sha or PR.LABEL_SHA256,
                              select_sha or PR.SELECT_SHA256, fix, generation=spec.get("generation"), bound_for=bound_for)
              for w in ("A", "B")}
        return sel, cs, fix

    sel, out["cards"], fix = machine("card", cards)
    rec["counts"]["chosen"] = {pid: sum(len(s["chosen"]) for s in sel[pid].values()) for pid in SELECT_IDS}
    rec["counts"]["select_ok"] = {pid: sum(1 for s in sel[pid].values() if s["ok"]) for pid in SELECT_IDS}
    rec["counts"]["form_fixes"] = fix            # bare_no・add_period・ids_removed（消した番号の数）・meaning_end_ng
    rec["counts"]["bound_for"] = list(bound_for)
    rec["t_end"] = round(clock() - origin, 3)
    return out, rec


def _count(it):
    d = {}
    for k in it:
        d[k] = d.get(k, 0) + 1
    return dict(sorted(d.items()))


def run_pairs(specs, endpoint, direction_parallel=DEFAULTS["direction_parallel"], pair_concurrency=DEFAULTS["pair_concurrency"],
              max_tokens=MAX_TOKENS, origin=None, clock=time.time):
    """何組かを 1 回の起動で作る。pair_concurrency=0 は組の数。返り値: (outputs {組: …}, record)。"""
    origin = clock() if origin is None else origin
    n = pair_concurrency or len(specs)
    t0 = clock()
    if n <= 1:
        res = [run_pair(s, endpoint, direction_parallel, max_tokens, origin, clock) for s in specs]
    else:
        with ThreadPoolExecutor(max_workers=n) as ex:
            res = list(ex.map(lambda s: run_pair(s, endpoint, direction_parallel, max_tokens, origin, clock), specs))
    rec = {"direction_parallel": direction_parallel, "pair_concurrency": n, "t_start": round(t0 - origin, 3),
           "t_end": round(clock() - origin, 3), "pairs": [r for _, r in res]}
    return {s["id"]: o for s, (o, _) in zip(specs, res)}, rec


def first_diff(a, b):
    """同じなら None。違えば、何字目（0 から）から違うか。"""
    if a == b:
        return None
    a, b = str(a if a is not None else ""), str(b if b is not None else "")
    n = min(len(a), len(b))
    return next((i for i in range(n) if a[i] != b[i]), n)


def items(out):
    """比べるもの（組 1 つ）：ラベルの JSON・考える部分・選ぶ段の JSON・考える部分・カード（JSON の文字列）。"""
    import json
    d = {}
    for pid in LABEL_IDS:
        d[f"label.{pid}.text"] = out["label"][pid]["text"]
        d[f"label.{pid}.reasoning"] = out["label"][pid]["reasoning"]
    for pid in SELECT_IDS:
        d[f"select.{pid}.text"] = out["select"][pid]["text"]
        d[f"select.{pid}.reasoning"] = out["select"][pid]["reasoning"]
    for w in ("A", "B"):
        d[f"card.{w}"] = json.dumps(out["cards"][w], ensure_ascii=False, sort_keys=False)
    return d


def compare(outputs_by_mode, base="a"):
    """回ごとの出力を、base の回と比べる。返り値は番号と数だけ（{組: {回: {もの: None か 何字目}}}）。"""
    res = {}
    for pair in outputs_by_mode[base]:
        b = items(outputs_by_mode[base][pair])
        res[pair] = {}
        for m, outs in outputs_by_mode.items():
            if m == base or pair not in outs:
                continue
            o = items(outs[pair])
            res[pair][m] = {k: first_diff(b[k], o.get(k)) for k in b}
    return res
