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
"""
import sys, os, json, pathlib, datetime

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))

from necessity_gen import generate_necessity
from embedding_service import build_vectors
from matcher_v4 import rank_candidates
from embedding_config import MODEL_TAG

EVAL_FILE   = pathlib.Path(__file__).parent.parent / "eval_pairs_v1.jsonl"
POOL_FILE   = pathlib.Path(__file__).parent.parent / "github_profiles.jsonl"
RESULTS_DIR = pathlib.Path(__file__).parent.parent / "results"
TOP_N       = int(os.environ.get("POOL_EVAL_TOP_N", "5"))


def load_seekers():
    cases = []
    with open(EVAL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "_comment" in obj or "_schema" in obj:
                continue
            cases.append(obj)
    return cases


def load_pool():
    if not POOL_FILE.exists():
        print(f"ERROR: {POOL_FILE} が見つかりません。", file=sys.stderr)
        print("先に eval/scripts/fetch_github_profiles.py を実行してください。", file=sys.stderr)
        sys.exit(1)
    pool = []
    with open(POOL_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                pool.append(json.loads(line))
    return pool


def _to_vecs(profile):
    """build_vectors の結果を rank_candidates 用の4チャネル dict に変換。"""
    v = build_vectors(profile, "")
    return {
        "will_symmetric":  v["will_symmetric"],
        "will_passage":    v["will_passage"],
        "state_passage":   v["state_passage"],
        "necessity_query": v["necessity_query"],
    }


def _seeker_vecs_and_params(profile):
    """seeker: generate_necessity → build_vectors → 4チャネル dict + gamma/p/alpha/beta。"""
    nec  = generate_necessity(profile)
    v    = build_vectors(profile, nec["necessity_text"])
    vecs = {
        "will_symmetric":  v["will_symmetric"],
        "will_passage":    v["will_passage"],
        "state_passage":   v["state_passage"],
        "necessity_query": v["necessity_query"],
    }
    return vecs, nec["gamma"], nec["p_sharpness"], nec["alpha"], nec["beta"]


def main():
    print(f"モデル: {MODEL_TAG}")
    seekers = load_seekers()
    pool    = load_pool()
    print(f"Seeker: {len(seekers)} 件 / プール: {len(pool)} 件\n")

    all_results = []

    for case in seekers:
        case_id     = case["case_id"]
        seeker_node = case["seeker"]
        seeker_profile = {
            "will_text":      seeker_node.get("will", ""),
            "state_have":     seeker_node.get("now", ""),
            "state_can_type": seeker_node.get("state_can_type", ""),
            "state_bound":    seeker_node.get("state_bound", ""),
            "state_unsorted": seeker_node.get("state_unsorted", ""),
        }
        will_preview = seeker_node.get("will", "")[:40]
        print(f"=== Seeker {case_id}: {will_preview}... ===")

        try:
            svecs, gamma, p, alpha, beta = _seeker_vecs_and_params(seeker_profile)
        except Exception as e:
            print(f"  ERROR (seeker vecs): {e}\n")
            continue

        # 候補ベクトルを構築
        cand_list = []
        for p_rec in pool:
            cand_profile = {
                "will_text":      p_rec.get("will_text", ""),
                "state_have":     p_rec.get("state_have", ""),
                "state_can_type": p_rec.get("state_can_type", ""),
                "state_bound":    p_rec.get("state_bound", ""),
                "state_unsorted": p_rec.get("state_unsorted", ""),
            }
            try:
                cvecs = _to_vecs(cand_profile)
                cand_list.append((p_rec["github_login"], cvecs, p_rec))
            except Exception as e:
                print(f"  skip {p_rec.get('github_login','?')}: {e}")

        if not cand_list:
            print("  候補なし\n")
            continue

        # rank_candidates は (seeker_vecs, [(cid, cvecs), ...], gamma, ...) を期待
        ranked = rank_candidates(
            svecs,
            [(cid, cvecs) for cid, cvecs, _ in cand_list],
            gamma=gamma, p=p, alpha=alpha, beta=beta,
            top_k=TOP_N,
        )

        # login → p_rec を引くための辞書
        rec_by_login = {p_rec["github_login"]: p_rec for _, _, p_rec in cand_list}

        case_result = {"case_id": case_id, "model_tag": MODEL_TAG, "top": []}
        for rank, r in enumerate(ranked, 1):
            login = r["candidate_id"]
            score = r["score"]
            rec   = rec_by_login.get(login, {})
            print(f"  Rank {rank}: {rec.get('display_name', login)} (@{login})  score={score:.4f}")
            print(f"           意志: {rec.get('will_text','')[:60]}")
            case_result["top"].append({
                "rank":  rank,
                "login": login,
                "name":  rec.get("display_name", login),
                "score": round(score, 6),
                "will":  rec.get("will_text", ""),
                "state": rec.get("state_have", ""),
            })
        print()
        all_results.append(case_result)

    # 結果保存
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts       = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"pool_{MODEL_TAG}_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"model_tag": MODEL_TAG, "pool_size": len(pool),
                   "cases": all_results}, f, ensure_ascii=False, indent=2)
    print(f"結果保存: {out_path}")


if __name__ == "__main__":
    main()
