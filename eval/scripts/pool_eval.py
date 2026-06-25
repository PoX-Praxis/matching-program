"""
GitHub プロフィールプール（github_profiles.jsonl）を使ったマッチング検証

eval_pairs_v1.jsonl の各 seeker を起点に、100件のプロフィールプールから
embedding モデルでランキングし、上位5件を標準出力に表示する。

使い方（1モデルずつ実行）:
    # Qwen3
    set POX_EMBED_MODEL_TAG=qwen3-embedding-0.6b-d1024
    set POX_EMBED_BACKEND=qwen3
    set POX_QWEN3_ENDPOINT=http://localhost:8000/embed
    set ANTHROPIC_API_KEY=sk-ant-xxxx
    python eval/scripts/pool_eval.py

    # EmbGemma
    set POX_EMBED_MODEL_TAG=embgemma-300m
    set POX_EMBED_BACKEND=embgemma
    set POX_EMBGEMMA_ENDPOINT=http://localhost:8001/embed
    python eval/scripts/pool_eval.py

    # Nomic
    set POX_EMBED_MODEL_TAG=nomic-emb-v2
    set POX_EMBED_BACKEND=nomic
    set POX_NOMIC_ENDPOINT=http://localhost:8002/embed
    python eval/scripts/pool_eval.py

出力例:
    === Seeker c01: テクノロジーで社会の仕組みを変えたい ===
    Rank 1: yuki-suzuki  「障害を持つ人が...」  score=0.832
    Rank 2: taro-yamada  「AIで医療を変え...」  score=0.791
    ...

    結果は eval/results/pool_<MODEL_TAG>_<timestamp>.json にも保存。
"""
import sys, os, json, pathlib, datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))

from necessity_gen import generate_necessity
from embedding_service import build_vectors
from matcher_v4 import rank_candidates
from embedding_config import MODEL_TAG, SHORT_DIM

EVAL_FILE    = pathlib.Path(__file__).parent.parent / "eval_pairs_v1.jsonl"
POOL_FILE    = pathlib.Path(__file__).parent.parent / "github_profiles.jsonl"
RESULTS_DIR  = pathlib.Path(__file__).parent.parent / "results"
TOP_N        = int(os.environ.get("POOL_EVAL_TOP_N", "5"))


def load_seekers():
    seekers = []
    with open(EVAL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_comment" in obj or "_schema" in obj:
                continue
            seekers.append(obj)
    return seekers


def load_pool():
    pool = []
    if not POOL_FILE.exists():
        print(f"ERROR: {POOL_FILE} が見つかりません。", file=sys.stderr)
        print("先に eval/scripts/fetch_github_profiles.py を実行してください。", file=sys.stderr)
        sys.exit(1)
    with open(POOL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            pool.append(json.loads(line))
    return pool


def profile_from_eval_node(node):
    return {
        "will_text":       node.get("will", ""),
        "state_have":      node.get("now", ""),
        "state_can_type":  node.get("state_can_type", ""),
        "state_bound":     node.get("state_bound", ""),
        "state_unsorted":  node.get("state_unsorted", ""),
    }


def profile_from_pool(p):
    return {
        "will_text":       p.get("will_text", ""),
        "state_have":      p.get("state_have", ""),
        "state_can_type":  p.get("state_can_type", ""),
        "state_bound":     p.get("state_bound", ""),
        "state_unsorted":  p.get("state_unsorted", ""),
    }


def build_seeker_vecs(profile):
    necessity = generate_necessity(profile)
    vecs = build_vectors(profile, necessity)
    return vecs


def build_candidate_vecs(profile):
    return build_vectors(profile, "")


def main():
    print(f"モデル: {MODEL_TAG}")
    seekers = load_seekers()
    pool    = load_pool()
    print(f"Seeker: {len(seekers)} 件 / プール: {len(pool)} 件\n")

    results = []

    for case in seekers:
        case_id = case["case_id"]
        seeker_node = case["seeker"]
        seeker_profile = profile_from_eval_node(seeker_node)
        will_preview = seeker_node.get("will", "")[:40]

        print(f"=== Seeker {case_id}: {will_preview}... ===")

        try:
            seeker_vecs = build_seeker_vecs(seeker_profile)
        except Exception as e:
            print(f"  ERROR (seeker vecs): {e}\n")
            continue

        candidates = []
        for p in pool:
            try:
                cand_profile = profile_from_pool(p)
                cand_vecs    = build_candidate_vecs(cand_profile)
                candidates.append({
                    "id":    p["github_login"],
                    "name":  p.get("display_name", p["github_login"]),
                    "will":  p.get("will_text", ""),
                    "state": p.get("state_have", ""),
                    "vecs":  cand_vecs,
                })
            except Exception as e:
                print(f"  skip {p.get('github_login','?')}: {e}")

        if not candidates:
            print("  候補なし\n")
            continue

        # rank_candidates はベクトル dict と seeker_vecs dict を取る
        ranked = rank_candidates(seeker_vecs, [c["vecs"] for c in candidates])
        # ranked は score のリスト（candidates と同順）
        scored = sorted(zip(ranked, candidates), key=lambda x: -x[0])

        top = scored[:TOP_N]
        case_result = {"case_id": case_id, "model_tag": MODEL_TAG, "top": []}
        for rank, (score, cand) in enumerate(top, 1):
            print(f"  Rank {rank}: {cand['name']} ({cand['id']})  score={score:.4f}")
            print(f"           意志: {cand['will'][:60]}")
            case_result["top"].append({
                "rank": rank, "login": cand["id"], "name": cand["name"],
                "score": round(score, 6), "will": cand["will"], "state": cand["state"],
            })
        print()
        results.append(case_result)

    # 結果保存
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"pool_{MODEL_TAG}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"model_tag": MODEL_TAG, "pool_size": len(pool), "cases": results},
                  f, ensure_ascii=False, indent=2)
    print(f"結果保存: {out_path}")


if __name__ == "__main__":
    main()
