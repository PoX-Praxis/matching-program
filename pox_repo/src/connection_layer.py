#!/usr/bin/env python3
"""
PoX 接続層 ②  —  ③プラットフォームから叩く単一インターフェース
================================================================
run_matching(seeker, candidate_pool, want) を呼ぶと、
マッチング候補者リスト（順位＋総合スコア＋二チャネル内訳＋根拠文）を返す。

③の責務（投稿・共有DB蓄積・候補プールの参照・結果の表示）は呼び出し側。
②の責務はこの関数の中＝「seeker と candidate_pool を受け取り、根拠付き候補を返す」だけ。
台帳記録・承認UI・本物の母数集め・多人数embeddingは②の範囲外（③／現実活動／後フェーズ）。

LLM判断部（(3.5)翻訳・(4)二チャネル類似度）は、
 - ANTHROPIC_API_KEY があれば実Claude呼び出し
 - なければ judge_fn 差し替え（テスト/確認用）
で動く。結線・合算・ランキング・根拠整形はコードで固定。
"""
import os, json, urllib.request

MODEL = "claude-opus-4-8"
API_URL = "https://api.anthropic.com/v1/messages"

# (3)系列予測層の事前分布。最小ハードコード（本番は github_transition_builder.py の出力に差し替え）
TABLE = [
    {"phase": "mvp", "follower_bucket": "fol:100-1k", "repo_bucket": "repo:>30", "n": 40, "p_positive_change": 0.85},
    {"phase": "mvp", "follower_bucket": "fol:10-100", "repo_bucket": "repo:5-30", "n": 30, "p_positive_change": 0.55},
    {"phase": "early_validation", "follower_bucket": "fol:1k+", "repo_bucket": "repo:>30", "n": 25, "p_positive_change": 0.80},
    {"phase": "early_validation", "follower_bucket": "fol:100-1k", "repo_bucket": "repo:5-30", "n": 35, "p_positive_change": 0.60},
]
FEATURE_TO_ROLE_FALLBACK = {
    ("fol:100-1k", "repo:>30"): "実装力のあるエンジニア",
    ("fol:10-100", "repo:5-30"): "そこそこの開発者",
    ("fol:1k+", "repo:>30"): "実績ある実装リード／技術で発信できる人",
    ("fol:100-1k", "repo:5-30"): "プロダクトを形にできる開発者",
}

# 用途プリセット（合算重み）。③のUIから want で選ばせる想定。
WANT_PRESETS = {
    "complement": (0.7, 0.3),  # 欠落を埋める相手がほしい（相補重視）
    "similar":    (0.3, 0.7),  # 同じ夢の仲間がほしい（類似重視）
    "balanced":   (0.5, 0.5),
}


def _call_claude(system, user, max_tokens=1024):
    body = {"model": MODEL, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
        headers={"content-type": "application/json",
                 "x-api-key": os.environ.get("ANTHROPIC_API_KEY", ""),
                 "anthropic-version": "2023-06-01"})
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    return json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())


# ---- (3)+(3.5): 必要像の生成（テーブル参照＝コード固定 / 翻訳＝LLM、無ければフォールバック辞書） ----
def predict_needed_roles(phase, use_llm):
    rows = sorted([r for r in TABLE if r["phase"] == phase], key=lambda r: -r["p_positive_change"])
    if not rows:
        return []
    if use_llm:
        buckets = [{"follower": r["follower_bucket"], "repo": r["repo_bucket"], "w": r["p_positive_change"]} for r in rows]
        sys_p = "あなたはPoXマッチングエンジンの翻訳層(3.5)。統計の特徴バケットを、人の自己記述と同じ意味空間に乗る『役割記述』へ言語化する。"
        usr_p = f"次のバケットを簡潔な日本語の役割記述に翻訳。{json.dumps(buckets, ensure_ascii=False)}\n出力はJSONのみ: [{{\"role\":\"..\",\"w\":数値}}]"
        return _call_claude(sys_p, usr_p)
    return [{"role": FEATURE_TO_ROLE_FALLBACK.get((r["follower_bucket"], r["repo_bucket"]), "?"),
             "w": r["p_positive_change"]} for r in rows]


# ---- (4): 二チャネル採点 ----
def _score_via_llm(seeker, cand, roles):
    sys_p = ("あなたはPoXマッチングエンジンの照合層(4)。相補(必要役割⇔候補の実像/非対称)と"
             "類似(seekerの意志⇔候補の意志/対称)を各0..1で採点。意味で判断し文字面に引っ張られない。")
    usr_p = (f"seekerの意志:{seeker['意志']}\n必要な役割:{json.dumps([r['role'] for r in roles],ensure_ascii=False)}\n"
             f"候補:{cand['id']} / {cand['profile']}\n"
             '出力はJSONのみ:{"comp":0..1,"comp_via":"決め手役割","sim":0..1}')
    return _call_claude(sys_p, usr_p)


def run_matching(seeker, candidate_pool, want="balanced", judge_fn=None):
    """②の単一入口。③はこれを呼ぶだけ。
    seeker         : v3出力の seeker（意志/求めている/能力/フェーズ ...）
    candidate_pool : [{"id":..,"profile":..}, ...]（③が共有DBから参照して渡す）
    want           : "complement" | "similar" | "balanced"
    judge_fn       : テスト用。(seeker,cand,roles)->{"comp","comp_via","sim"} を返すと実APIを使わない
    戻り値         : {"needed_roles":[...], "want":..., "ranking":[...] }
    """
    phase = seeker.get("フェーズ")
    use_llm = (judge_fn is None) and bool(os.environ.get("ANTHROPIC_API_KEY"))
    roles = predict_needed_roles(phase, use_llm)
    w_comp, w_sim = WANT_PRESETS.get(want, WANT_PRESETS["balanced"])

    ranking = []
    for cand in candidate_pool:
        if judge_fn is not None:
            ch = judge_fn(seeker, cand, roles)
        elif use_llm:
            ch = _score_via_llm(seeker, cand, roles)
        else:
            raise RuntimeError("ANTHROPIC_API_KEY が無く judge_fn も未指定。どちらかが必要です。")
        score = round(w_comp * ch["comp"] + w_sim * ch["sim"], 3)
        reason = (f"必要像「{ch.get('comp_via')}」に対し相補{ch['comp']}で噛み合い、"
                  f"意志の類似は{ch['sim']}。用途『{want}』の重み(相補{w_comp}/類似{w_sim})で総合{score}。")
        ranking.append({"id": cand["id"], "score": score,
                        "comp": ch["comp"], "comp_via": ch.get("comp_via"),
                        "sim": ch["sim"], "reason": reason})
    ranking.sort(key=lambda r: -r["score"])
    for i, r in enumerate(ranking, 1):
        r["rank"] = i
    return {"needed_roles": roles, "want": want, "ranking": ranking}
