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
import sys, os, json, pathlib, datetime, traceback, time

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent / "src"))
sys.path.insert(0, str(pathlib.Path(__file__).parent))  # vector_cache（同ディレクトリ）

from necessity_gen import generate_necessity
from embedding_service import build_vectors
from matcher_v4 import rank_candidates
from embedding_config import MODEL_TAG
import vector_cache

def _s(v):
    """str 以外（dict・list・None 等）を空文字に落とす。github_profiles の型ずれ対策。"""
    return v if isinstance(v, str) else ""


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


def _resolve_necessity(profile):
    """
    必要像を解決する。seeker に derived_necessity が紐づいていればそれを使い
    （②生成を再実行しない＝ANTHROPIC_API_KEY 不要）、無ければ generate_necessity を呼ぶ。
    solo_eval._resolve_necessity と同じ分岐。
    """
    bound = profile.get("derived_necessity")
    if isinstance(bound, dict) and bound.get("necessity_text"):
        print("  （seeker に紐づく必要像を使用：Claude 呼び出しをスキップ）", flush=True)
        return {
            "necessity_text": bound["necessity_text"],
            "gamma":          float(bound.get("gamma", 0.0)),
            "p_sharpness":    float(bound.get("p_sharpness", 0.0)),
            "alpha":          float(bound.get("alpha", 1.0)),
            "beta":           float(bound.get("beta", 1.0)),
        }
    return generate_necessity(profile)


def _seeker_vecs_and_params(profile):
    """seeker: 必要像解決 → build_vectors → 4チャネル dict + gamma/p/alpha/beta。"""
    nec  = _resolve_necessity(profile)
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

    # ── 候補ベクトルを一度だけ構築（seeker をまたいで再利用）────────────────
    # 候補は seeker が変わっても同じなので 1 回だけ埋め込む（seeker 間の再計算回避）。
    # さらにモデル別キャッシュ経由で「実行間」も再利用（入力テキスト不変ならbuildしない）。
    cache = vector_cache.load_cache(MODEL_TAG)
    print(f"候補プールのベクトル化を開始（{len(pool)} 件・キャッシュ {len(cache)} 件）...", flush=True)
    _t = time.perf_counter()
    hits = misses = 0
    cand_list = []        # [(login, cvecs), ...]
    rec_by_login = {}     # login -> p_rec
    for i, p_rec in enumerate(pool, 1):
        login = p_rec.get("github_login", "?")
        cand_profile = {
            "will_text":      _s(p_rec.get("will_text")),
            "state_have":     _s(p_rec.get("state_have")),
            "state_can_type": _s(p_rec.get("state_can_type")),
            "state_bound":    _s(p_rec.get("state_bound")),
            "state_unsorted": _s(p_rec.get("state_unsorted")),
        }
        try:
            if vector_cache.is_cached(login, cand_profile, MODEL_TAG, cache):
                hits += 1
            else:
                misses += 1
            full = vector_cache.get_or_build(login, cand_profile, MODEL_TAG, cache)
            cvecs = {k: full[k] for k in ("will_symmetric", "will_passage", "state_passage", "necessity_query")}
            cand_list.append((login, cvecs))
            rec_by_login[login] = p_rec
        except Exception as e:
            print(f"\n  skip {login}: {e}")
        if i % 5 == 0 or i == len(pool):
            print(f"\r  候補ベクトル化: {i}/{len(pool)} 完了（hit {hits} / miss {misses}）", end="", flush=True)
    print()  # 改行
    vector_cache.save_cache(MODEL_TAG, cache)
    print(f"候補ベクトル化: {time.perf_counter() - _t:.2f}秒（hit {hits} / miss {misses}）")

    if not cand_list:
        print("候補ベクトルが1件も作れませんでした。終了します。")
        return
    print(f"候補ベクトル化 完了: {len(cand_list)} 件\n")

    all_results = []

    for s_idx, case in enumerate(seekers, 1):
        case_id     = case["case_id"]
        seeker_node = case["seeker"]
        seeker_profile = {
            "will_text":      seeker_node.get("will", ""),
            "state_have":     seeker_node.get("now", ""),
            "state_can_type": seeker_node.get("state_can_type", ""),
            "state_bound":    seeker_node.get("state_bound", ""),
            "state_unsorted": seeker_node.get("state_unsorted", ""),
        }
        # seeker に必要像が紐づいていれば Claude 呼び出しをスキップ（case または seeker_node に置ける）
        _bound = case.get("derived_necessity") or seeker_node.get("derived_necessity")
        if isinstance(_bound, dict):
            seeker_profile["derived_necessity"] = _bound
        will_preview = seeker_node.get("will", "")[:40]
        print(f"=== Seeker {s_idx}/{len(seekers)} {case_id}: {will_preview}... ===")

        try:
            print("  必要像を生成中（Claude）→ seeker をベクトル化...", flush=True)
            svecs, gamma, p, alpha, beta = _seeker_vecs_and_params(seeker_profile)
        except Exception as e:
            traceback.print_exc()
            print(f"  ERROR (seeker vecs): {e}\n")
            continue

        # rank_candidates は (seeker_vecs, [(cid, cvecs), ...], gamma, ...) を期待
        # 候補ベクトルは事前計算済みを再利用
        ranked = rank_candidates(
            svecs,
            cand_list,
            gamma=gamma, p=p, alpha=alpha, beta=beta,
            top_k=TOP_N,
        )

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
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {"model_tag": MODEL_TAG, "pool_size": len(pool), "cases": all_results}

    # results/: 一時保存（gitignore 対象）
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = RESULTS_DIR / f"pool_{MODEL_TAG}_{ts}.json"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    # snapshots/: 永続保存（git 追跡対象）
    SNAPSHOTS_DIR = RESULTS_DIR.parent / "snapshots"
    SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    snap_name = f"pool_{MODEL_TAG}_{ts}.json"
    snap_path = SNAPSHOTS_DIR / snap_name
    with open(snap_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"結果保存（一時）: {tmp_path}")
    print(f"結果保存（永続）: {snap_path}")
    print()
    print("git でスナップショットを記録するには:")
    print(f"  git add eval/snapshots/{snap_name}")
    print(f'  git commit -m "snapshot: pool × {MODEL_TAG} マッチング結果"')
    print("  git push origin exp/embedding-model-eval")


if __name__ == "__main__":
    main()
