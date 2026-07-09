"""
全件スコア分布の蓄積（将来の shortlist/ANN 導入判断の根拠データ・蓄積のみ・判定は将来）

solo_eval 実行のたびに、TOP30 スナップショットとは別に「必要像×候補」の全件分布を貯める。
判定ロジック・足切りは一切しない（蓄積のみ）。蓄積単位は necessity_id（必要像テキストのハッシュ）。

副次的に seeker ベクトル（full+256）も保存する。MRL256 検証（mrl_report.py）が
サーバー無しでランキングを再現するために必要（seeker は候補キャッシュに載らないため）。

出力（すべて eval/analysis/distributions/ ＝ gitignore 配下）:
  {model_tag}_{ts}_full_scores.jsonl  … 1行1候補: necessity_id, login, final_score, a_sim,b_sim,c_sim, limiting_axis
  {model_tag}_{ts}_summary.json       … necessity_id ごとの percentile・b近傍密度指標
  seeker_{model_tag}_{necessity_id}.json（eval/cache/ 配下）… seeker の 8 ベクトル（MRL 用）
"""
import os, json, glob, hashlib, pathlib

# 本モジュールは src/ に配置。出力先は repo/eval/ 配下（eval ランナーと同じ・gitignore 配下）。
BASE      = pathlib.Path(__file__).parent.parent / "eval"
DIST_DIR  = BASE / "analysis" / "distributions"
CACHE_DIR = BASE / "cache"

# seeker ベクトル保存に使う 8 キー（build_vectors 戻りのベクトル部分）
SEEKER_KEYS = (
    "will_symmetric", "will_sym_256",
    "will_passage",   "will_pas_256",
    "state_passage",  "state_pas_256",
    "necessity_query", "necessity_q_256",
)


def necessity_id(necessity_text):
    """必要像テキストの sha256 先頭12桁。1字でも変われば別 ID＝別の問いとして蓄積される。"""
    t = necessity_text if isinstance(necessity_text, str) else ""
    return hashlib.sha256(t.encode("utf-8")).hexdigest()[:12]


def _percentile(sorted_vals, q):
    """線形補間の percentile（q は 0..100）。"""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = (len(sorted_vals) - 1) * (q / 100.0)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def count_prior(model_tag, nid):
    """同一 necessity_id の過去分布（summary）が何件あるか。突合・判定はしない。"""
    n = 0
    for f in glob.glob(str(DIST_DIR / f"{model_tag}_*_summary.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            if d.get("necessity_id") == nid:
                n += 1
        except Exception:
            pass
    return n


def save_seeker_vecs(model_tag, nid, seeker_full_dict):
    """seeker の 8 ベクトルを eval/cache/seeker_{model_tag}_{nid}.json に保存（MRL 用）。"""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"seeker_{model_tag}_{nid}.json"
    payload = {
        "model_tag": model_tag,
        "necessity_id": nid,
        "vecs": {k: list(seeker_full_dict[k]) for k in SEEKER_KEYS if k in seeker_full_dict},
    }
    tmp = path.parent / (path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)
    os.replace(tmp, path)
    return path


def save_distribution(model_tag, nid, ranked_all, ts):
    """
    全候補ランキング（rank_candidates(top_k=None) の戻り）から full_scores.jsonl と summary.json を書く。
    ranked_all: [{"candidate_id", "score", "attribution": {a_sim,b_sim,c_sim,limiting_axis,...}}, ...]
    """
    DIST_DIR.mkdir(parents=True, exist_ok=True)
    jsonl_path = DIST_DIR / f"{model_tag}_{ts}_full_scores.jsonl"
    summary_path = DIST_DIR / f"{model_tag}_{ts}_summary.json"

    scores, b_sims = [], []
    with open(jsonl_path, "w", encoding="utf-8") as fh:
        for r in ranked_all:
            attr = r.get("attribution") or {}
            row = {
                "necessity_id": nid,
                "login": r["candidate_id"],
                "final_score": round(r["score"], 6),
                "a_sim": round(attr.get("a_sim", 0.0), 6),
                "b_sim": round(attr.get("b_sim", 0.0), 6),
                "c_sim": round(attr.get("c_sim", 0.0), 6),
                "limiting_axis": attr.get("limiting_axis"),
            }
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            scores.append(r["score"])
            b_sims.append(attr.get("b_sim", 0.0))

    s_sorted = sorted(scores)
    b_sorted = sorted(b_sims)
    summary = {
        "necessity_id": nid,
        "model_tag": model_tag,
        "timestamp": ts,
        "n_candidates": len(scores),
        "score_percentiles": {
            "p10": _percentile(s_sorted, 10), "p25": _percentile(s_sorted, 25),
            "p50": _percentile(s_sorted, 50), "p75": _percentile(s_sorted, 75),
            "p90": _percentile(s_sorted, 90),
        },
        # 経路 b の近傍密度指標：b_sim 上位10%の閾値（p90）。将来の shortlist 設計材料。
        "b_sim_top10pct_threshold": _percentile(b_sorted, 90),
    }
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=2)
    return jsonl_path, summary_path
