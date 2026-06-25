"""
PoX embedding モデル比較 評価ランナー（設計書 §7-4 / §2 E1/E3/E4）

使い方:
    # 実モデル（GPUホスト）で1モデルずつ回す。② はキー有りで実Claude推奨。
    POX_EMBED_MODEL_TAG=qwen3-emb-0.6b  POX_EMBED_BACKEND=qwen3 \\
    POX_QWEN3_ENDPOINT=<url>  ANTHROPIC_API_KEY=<key> \\
    python eval/run_eval.py

    POX_EMBED_MODEL_TAG=embgemma-300m   POX_EMBED_BACKEND=embgemma \\
    POX_EMBGEMMA_ENDPOINT=<url>  python eval/run_eval.py

    POX_EMBED_MODEL_TAG=nomic-emb-v2    POX_EMBED_BACKEND=nomic \\
    POX_NOMIC_ENDPOINT=<url>  python eval/run_eval.py

    # 配管検証（GPU不要・非意味的）: stub backend で全経路が通るか確認
    POX_EMBED_BACKEND=stub  python eval/run_eval.py path/to/eval.jsonl

引数:
    argv[1] : 評価ファイルパス（省略時は eval/eval_pairs_v1.jsonl）

出力:
    Hit@1 / Hit@3 / MRR（全体 + 日本語のみ）を標準出力に表示。
    FULL_DIM と 256（MRL 後）の両方を測定（E4）。
    結果は eval/results/<MODEL_TAG>_<timestamp>.json にも保存する。

注意（設計書 §4-1 / §8）:
    - 1回の計算で複数モデルのベクトルを混ぜない。MODEL_TAG を変えて3回別々に実行する。
    - 評価ケースに _comment キーを持つ行は読み飛ばす。
    - raw テキストは渡さない。embedding_service.build_vectors が redaction を行う。
    - matcher_v4 / match_config は触らない（import して呼ぶのみ）。
"""
import sys
import os
import json
import time
import pathlib
import datetime

# src を import パスに追加
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

from necessity_gen import generate_necessity
from embedding_service import build_vectors
from matcher_v4 import rank_candidates

EVAL_FILE = pathlib.Path(__file__).parent / "eval_pairs_v1.jsonl"
RESULTS_DIR = pathlib.Path(__file__).parent / "results"


# ── 評価セット読み込み ──────────────────────────────────────────────────────
def load_eval_pairs(path=EVAL_FILE):
    """JSONL を読み込む。_comment キーを持つ行は読み飛ばす。"""
    cases = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_comment" in obj or "_schema" in obj:
                continue
            cases.append(obj)
    return cases


# ── profiles_v4 フラット dict 組み立て ───────────────────────────────────────
def _to_profile(node):
    """
    評価ケースの seeker / candidate ノードを profiles_v4 フラット dict に写す。

    対応形式:
      {"will": "...", "now": "..."}           推奨。will→意志, now→現状(state_have)
      {"will": "...", "state_have": ..., ...}  現状4スロットを明示する場合
      {"profile": "..."}                       fallback: 現状のみ（意志空）。
                                               候補の a/c チャネルが効かなくなるため非推奨。
    """
    will = node.get("will", "")
    prof = {
        "will_text":       will,
        "state_have":      node.get("state_have", node.get("now", "")),
        "state_can_type":  node.get("state_can_type", ""),
        "state_bound":     node.get("state_bound", ""),
        "state_unsorted":  node.get("state_unsorted", ""),
    }
    if not will and not prof["state_have"] and node.get("profile"):
        # fallback: profile 一本を現状として扱う
        prof["state_have"] = node["profile"]
    return prof


def _seeker_vecs(profile, use_short):
    """seeker の必要像を②生成 → build_vectors。(vecs, gamma, p, alpha, beta) を返す。"""
    nec = generate_necessity(profile)
    v = build_vectors(profile, nec["necessity_text"])
    vecs = _pick_dim(v, use_short)
    return vecs, nec["gamma"], nec["p_sharpness"], nec["alpha"], nec["beta"]


def _candidate_vecs(profile, use_short):
    """candidate の build_vectors。necessity は候補側では不要（state_passage を使う）。"""
    v = build_vectors(profile, "")
    return _pick_dim(v, use_short)


def _pick_dim(v, use_short):
    """build_vectors 戻り dict から full または short(256) のチャネルベクトルを選ぶ。"""
    if use_short:
        return {
            "will_symmetric":  v["will_sym_256"],
            "will_passage":    v["will_pas_256"],
            "state_passage":   v["state_pas_256"],
            "necessity_query": v["necessity_q_256"],
        }
    return {
        "will_symmetric":  v["will_symmetric"],
        "will_passage":    v["will_passage"],
        "state_passage":   v["state_passage"],
        "necessity_query": v["necessity_query"],
    }


# ── 1ケースの評価 ────────────────────────────────────────────────────────────
def eval_case(case, use_short_dim=False):
    """
    1 case を構成Cで順位付けし、正解(match)候補の最良順位（1始まり）を返す。
    正解が0件なら None。複数 match があれば最も上位の順位を採用。

    matcher_v4.rank_candidates を import して呼ぶ（matcher 本体は触らない）。
    seeker / candidate は同一 MODEL_TAG・同一 backend でベクトル化（§4-1 遵守）。
    """
    seeker_prof = _to_profile(case["seeker"])
    svecs, gamma, p, alpha, beta = _seeker_vecs(seeker_prof, use_short_dim)

    cand_list = []
    match_ids = set()
    for cand in case["candidates"]:
        cid = cand["cand_id"]
        cvecs = _candidate_vecs(_to_profile(cand), use_short_dim)
        cand_list.append((cid, cvecs))
        if cand.get("label") == "match":
            match_ids.add(cid)

    if not match_ids:
        return None

    ranked = rank_candidates(svecs, cand_list, gamma=gamma, p=p, alpha=alpha, beta=beta)
    order = [r["candidate_id"] for r in ranked]
    ranks = [order.index(mid) + 1 for mid in match_ids if mid in order]
    return min(ranks) if ranks else None


# ── 指標計算 ────────────────────────────────────────────────────────────────
def _metrics_over(rows):
    """rows: [rank, ...]（None 除外済み）。Hit@1/Hit@3/MRR を返す。"""
    n = len(rows)
    if n == 0:
        return {"n": 0, "hit@1": None, "hit@3": None, "mrr": None}
    hit1 = sum(1 for r in rows if r <= 1) / n
    hit3 = sum(1 for r in rows if r <= 3) / n
    mrr = sum(1.0 / r for r in rows) / n
    return {"n": n, "hit@1": round(hit1, 4), "hit@3": round(hit3, 4), "mrr": round(mrr, 4)}


def compute_metrics(results):
    """
    results: [{"rank": int|None, "lang": "ja"|"en"}, ...]
    全体と日本語のみの Hit@1/Hit@3/MRR を返す。rank=None（正解なし）は除外。
    """
    overall = [r["rank"] for r in results if r["rank"] is not None]
    ja_only = [r["rank"] for r in results if r["rank"] is not None and r.get("lang") == "ja"]
    return {"overall": _metrics_over(overall), "ja": _metrics_over(ja_only)}


# ── MRL 比較（FULL_DIM vs SHORT_DIM）────────────────────────────────────────
def eval_mrl(cases):
    """
    E4: FULL_DIM と SHORT_DIM=256 の両方で評価し、Hit@3 の差（劣化）を返す。
    """
    full = compute_metrics([
        {"rank": eval_case(c, use_short_dim=False), "lang": c.get("lang", "ja")}
        for c in cases
    ])
    short = compute_metrics([
        {"rank": eval_case(c, use_short_dim=True), "lang": c.get("lang", "ja")}
        for c in cases
    ])
    h3_full = full["overall"]["hit@3"]
    h3_short = short["overall"]["hit@3"]
    degradation = (round(h3_full - h3_short, 4)
                   if h3_full is not None and h3_short is not None else None)
    return {"full": full, "short_256": short, "hit@3_degradation": degradation}


# ── 結果保存 ────────────────────────────────────────────────────────────────
def save_results(model_tag, payload):
    RESULTS_DIR.mkdir(exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out = RESULTS_DIR / f"{model_tag}_{ts}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    return out


# ── メイン ──────────────────────────────────────────────────────────────────
def main():
    from embedding_config import MODEL_TAG, BACKEND, FULL_DIM, SHORT_DIM

    eval_path = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else EVAL_FILE

    print("=== PoX embedding 評価ランナー ===")
    print(f"MODEL_TAG : {MODEL_TAG}")
    print(f"BACKEND   : {BACKEND}")
    print(f"FULL_DIM  : {FULL_DIM}")
    print(f"SHORT_DIM : {SHORT_DIM}")
    print(f"eval file : {eval_path}")
    if BACKEND == "stub":
        print("[注意] BACKEND=stub は非意味的。数値は配管検証用で、モデル比較には使えません。")
    print()

    cases = load_eval_pairs(eval_path)
    if not cases:
        print("[警告] 評価ケースが0件です。eval/eval_pairs_v1.jsonl にケースを追加してください。")
        print("       match/unrelated ラベルは人が最終確認すること（設計書 §3-1 / 禁則）。")
        return

    print(f"評価ケース数: {len(cases)}")
    t0 = time.time()
    mrl = eval_mrl(cases)
    elapsed = round(time.time() - t0, 3)

    payload = {
        "model_tag": MODEL_TAG,
        "backend": BACKEND,
        "full_dim": FULL_DIM,
        "short_dim": SHORT_DIM,
        "n_cases": len(cases),
        "elapsed_sec": elapsed,
        **mrl,
    }

    print("\n--- FULL_DIM ---")
    print(json.dumps(mrl["full"], ensure_ascii=False, indent=2))
    print("\n--- SHORT_DIM=256 (MRL) ---")
    print(json.dumps(mrl["short_256"], ensure_ascii=False, indent=2))
    print(f"\nHit@3 劣化 (FULL→256): {mrl['hit@3_degradation']}")

    saved = save_results(MODEL_TAG, payload)
    print(f"\n結果を保存: {saved}")


if __name__ == "__main__":
    main()
