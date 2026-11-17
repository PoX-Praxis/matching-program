#!/usr/bin/env python3
"""指示書76 — 借りた機械（Hugging Face Jobs）の中で動かす計測。架空の 60 組（synthetic_v1）だけを使う。

- 同じ入れ物の中の llama-server（127.0.0.1）を、LlamaCppBackend で呼ぶ
- 入力は、指示書72 の形（1 つの必要 × 1 つの相手の文）。数え方は scripts/explanation_measure.py の bench_measure のまま
- `local/` は読まない（このスクリプトと、渡す一式に、手元のデータへの道が無い。テスト 510）
- ログ（標準出力）には文を出さない。件数・時間だけ。出力の文（架空の組の文）は --out の JSON にだけ書く
使い方（入れ物の中）:
  python3 scripts/stage3a_run.py --profile nemotron-9b-ja --gguf-sha256 <sha> --ids S01,S04,S31 --repeat 1 \
      --record /out/record.json --out /out/result.json
"""
import argparse
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [p for p in (ROOT, os.path.join(ROOT, "src"), os.path.join(ROOT, "scripts")) if p not in sys.path]
import explanation_gen as G  # noqa: E402
import explanation_llamacpp as L  # noqa: E402
import explanation_measure as M  # noqa: E402

SYNTHETIC = os.path.join(ROOT, "data", "explanation_bench", "synthetic_v1.json")
TRIAL_IDS = ("S01", "S04", "S31")              # 順1 の小さな試し（R1・無関係・R4 共鳴）。結果を見て何も動かさない
# 橋渡しの 12 組（指示書76 §2。走らせる前に報告に書いた。選び方は報告76 順1）
BRIDGE_IDS = ("S01", "S02", "S03", "S04", "S05", "S06", "S08", "S10", "S13", "S14", "S23", "S31")
UNRELATED_NOTE = "無関係"


def load_synthetic():
    with open(SYNTHETIC, encoding="utf-8") as f:
        return json.load(f)


def items_for(syn, ids=None):
    return [it for it in M.synthetic_items(syn) if ids is None or it[0]["id"] in ids]


def one_run(backend, items):
    """60 組（または選んだ組）を 1 回。組ごとの記録と、呼んだ記録（出力の文）を返す。"""
    def tagged():
        for pr, n, o in items:
            backend.tag = pr["id"]
            yield pr, n, o
    backend.calls = []
    t0 = time.time()
    out = M.bench_measure(tagged(), backend)
    return {"counts": out["counts"], "records": out["records"], "calls": backend.calls,
            "seconds": round(time.time() - t0, 2)}


def determinism(runs):
    """出力が一字ずつ同じか（組・段・呼んだ順ごとに、出力の sha256 を比べる）。"""
    def key(run):
        seen, out = {}, {}
        for c in run["calls"]:
            k = (c["id"], c["stage"])
            seen[k] = seen.get(k, 0) + 1
            out[(c["id"], c["stage"], seen[k])] = c["output_sha256"]
        return out
    keys = [key(r) for r in runs]
    if not keys:
        return {"runs": 0, "calls": 0, "same": 0, "all_same": False}
    ref = keys[0]
    same = sum(1 for k, v in ref.items() if all(other.get(k) == v for other in keys[1:]))
    return {"runs": len(runs), "calls": len(ref), "same": same,
            "all_same": all(other == ref for other in keys[1:])}


def unrelated_errors(syn, records):
    """「無関係」の組（note が無関係）で、説明を出した数（指示書76 §3 の線 1）。"""
    ids = {p["id"] for p in syn["pairs"] if (p.get("note") or "") == UNRELATED_NOTE}
    return {"n": len(ids), "shown": sum(1 for r in records if r["id"] in ids and r.get("result") == "d")}


def main(argv=None, backend_factory=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--profile", required=True, choices=sorted(L.PROFILES))
    ap.add_argument("--endpoint", default="http://127.0.0.1:8080")
    ap.add_argument("--gguf-sha256", required=True)
    ap.add_argument("--ids", default="", help="組の id（, 区切り）。trial・bridge・all も使える")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--record", default="", help="入れ物・GPU などの記録（JSON）")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    ids = {"trial": TRIAL_IDS, "bridge": BRIDGE_IDS, "all": None, "": None}.get(args.ids)
    if args.ids and args.ids not in ("trial", "bridge", "all"):
        ids = tuple(x.strip() for x in args.ids.split(",") if x.strip())
    record = {}
    if args.record and os.path.exists(args.record):
        with open(args.record, encoding="utf-8") as f:
            record = json.load(f)
    syn = load_synthetic()
    items = items_for(syn, ids)
    make = backend_factory or (lambda: L.LlamaCppBackend(args.endpoint, args.profile, args.gguf_sha256, record=record))
    runs = []
    for i in range(args.repeat):
        backend = make()
        r = one_run(backend, items)
        r["repeat"] = i + 1
        runs.append(r)
        c = r["counts"]
        print(f"[stage3a] run {i + 1}/{args.repeat}: pairs {c['pairs']} kinds {c['kinds']} "
              f"errors {c['errors']['shown']}/{c['errors']['n']} recall {c['recall']['shown']}/{c['recall']['n']} "
              f"leak {c['leak']} seconds {r['seconds']}", flush=True)
    res = {"profile": args.profile, "profile_detail": L.PROFILES[args.profile], "gguf_sha256": args.gguf_sha256,
           "ids": list(ids) if ids else "all", "synthetic_sha256": M.sha256_file(SYNTHETIC),
           "prompt_sha256": {"1": G.PROMPT1_SHA, "2": G.PROMPT2_SHA}, "grammar_sha256": L.grammar_sha(args.profile),
           "decoding": backend.decoding, "version_key": G.version_key(backend), "record": record,
           "determinism": determinism(runs), "unrelated": unrelated_errors(syn, runs[0]["records"]) if runs else None,
           "runs": runs}
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"[stage3a] determinism {res['determinism']}", flush=True)
    return res


if __name__ == "__main__":
    main()
