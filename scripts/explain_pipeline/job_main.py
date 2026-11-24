"""流れを回（a・b・c）ごとに走らせ、出力・記録・比べた結果を書く（指示書80 §2）。hf_jobs のジョブの中でも、local_http でも同じ。

  python -m explain_pipeline.job_main --pairs pairs.json --modes a,b,c --out OUTDIR [--endpoint local_http --base-url URL]
      [--origin エポック秒] [--vllm-log ログ] [--vllm-settings JSON] [--warmup]

書くもの（OUTDIR）：
- outputs.json：回 → 組 → ラベル・選ぶ段の出力（文）とカード。KAORU・PA の組の文が入るので、非公開のジョブの出力にだけ置き、引き取ったら消す
- records.json：数・時刻・番号だけ（リクエストごとの時刻とトークン数・機械の段の秒・vLLM の設定）
- compare.json：回 a と b・c の比べ（もの → None か 何字目から違うか）。番号と数だけ
- vllm_log.json：vLLM のログから取り出した数だけ（起動の内訳・速さ）
標準出力（ジョブのログ）には数だけを出す。"""
import argparse
import json
import os
import sys
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from explain_pipeline import endpoint as EP  # noqa: E402
from explain_pipeline import prompts as PR  # noqa: E402
from explain_pipeline import runner as RN  # noqa: E402
from explain_pipeline import vllm_log as VL  # noqa: E402

WARMUP_PROMPT = "架空の確かめ：「了解」とだけ答えてください。"


def log(*a):
    print("[kp]", *a, flush=True)


def token_counts(endpoint, outputs, records):
    """出力の JSON の部分（content）と考える部分のトークン数を数え、記録に足す（時間を測った後に数える）。"""
    for mode, outs in outputs.items():
        for pr in records["modes"][mode]["pairs"]:
            o = outs[pr["pair"]]
            for rq in pr["requests"]:
                src = o["label" if rq["stage"] == "label" else "select"][rq["id"]]
                rq["content_tokens"] = endpoint.count_tokens(src["text"]) if src.get("text") else 0
                rq["reasoning_tokens_counted"] = endpoint.count_tokens(src["reasoning"]) if src.get("reasoning") else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pairs", required=True)
    ap.add_argument("--modes", default="a,b,c")
    ap.add_argument("--out", required=True)
    ap.add_argument("--endpoint", default="local_http", choices=("local_http", "hf_jobs"))
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--origin", type=float, default=None, help="ジョブの始まり（エポック秒）。記録の時刻はここからの秒")
    ap.add_argument("--vllm-log", default="")
    ap.add_argument("--vllm-settings", default="{}")
    ap.add_argument("--max-tokens", type=int, default=RN.MAX_TOKENS)
    ap.add_argument("--warmup", action="store_true", help="測る前に、架空の短いリクエストを 1 つ送る（記録は別）")
    a = ap.parse_args(argv)
    origin = a.origin if a.origin is not None else time.time()
    ep = EP.make(a.endpoint, **({"base_url": a.base_url} if a.endpoint == "local_http" else {}), clock=time.time)
    specs = json.load(open(a.pairs, encoding="utf-8"))["pairs"]
    os.makedirs(a.out, exist_ok=True)
    records = {"origin": origin, "settings": {"vllm": json.loads(a.vllm_settings), "max_tokens": a.max_tokens, "endpoint": a.endpoint,
                                              "decoding": EP.DECODING, "model": EP.MODEL,
                                              "prompt_sha256": {"label": PR.LABEL_SHA256, "select": PR.SELECT_SHA256}},
               "pairs": [s["id"] for s in specs], "modes": {}}
    if a.warmup:
        r = ep.complete(WARMUP_PROMPT, 256)
        records["warmup"] = {"t_send": round(r["t_send"] - origin, 3), "t_end": round(r["t_end"] - origin, 3),
                             "completion_tokens": (r.get("usage") or {}).get("completion_tokens")}
        log("warmup seconds", round(r["t_end"] - r["t_send"], 1))
    outputs = {}
    for m in a.modes.split(","):
        cfg = RN.MODES[m]
        outputs[m], records["modes"][m] = RN.run_pairs(specs, ep, cfg["direction_parallel"], cfg["pair_concurrency"], a.max_tokens, origin, time.time)
        rm = records["modes"][m]
        log("mode", m, "pairs", len(specs), "seconds", round(rm["t_end"] - rm["t_start"], 1),
            "requests", sum(len(p["requests"]) for p in rm["pairs"]),
            "finish_stop", sum(1 for p in rm["pairs"] for q in p["requests"] if q["finish_reason"] == "stop"),
            "errors", sum(1 for p in rm["pairs"] for q in p["requests"] if q["error"]))
        json.dump(outputs, open(os.path.join(a.out, "outputs.json"), "w", encoding="utf-8"), ensure_ascii=False)
    token_counts(ep, outputs, records)
    cmp = RN.compare(outputs) if "a" in outputs else {}
    for pair, by in cmp.items():
        log("compare", pair, " ".join(f"{m}:{sum(1 for v in d.values() if v is None)}/{len(d)}" for m, d in by.items()))
    json.dump(records, open(os.path.join(a.out, "records.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(cmp, open(os.path.join(a.out, "compare.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if a.vllm_log and os.path.exists(a.vllm_log):
        parsed = VL.parse(open(a.vllm_log, encoding="utf-8", errors="replace"), origin)
        wins = [(m, records["modes"][m]["t_start"], records["modes"][m]["t_end"]) for m in records["modes"]]
        json.dump({"parsed": parsed, "summary": VL.summary(parsed, wins)}, open(os.path.join(a.out, "vllm_log.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        log("vllm_log events", len(parsed["events"]))
    log("done")


if __name__ == "__main__":
    main()
